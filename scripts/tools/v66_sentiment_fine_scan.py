#!/usr/bin/env python3
"""v66_sentiment 情绪择时精细参数扫描"""
import sys, os, json, time
sys.path.insert(0, '/root/a-share-quant-sim')
import sqlite3, numpy as np, pandas as pd

RESULT_FILE = '/root/a-share-quant-sim/scripts/tools/v66_sentiment_fine_results.json'
LOG_FILE = '/root/a-share-quant-sim/scripts/tools/v66_sentiment_fine_scan.log'

def log(msg):
    print(msg, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(msg + '\n')

def load_data():
    log("[1] 加载数据...")
    t0 = time.time()
    conn = sqlite3.connect('data/quant_stocks.db', timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    codes_df = pd.read_sql_query('SELECT code, float_shares FROM stock_pool_zz1800', conn)
    codes = codes_df['code'].tolist()
    fs_map = dict(zip(codes_df['code'], codes_df['float_shares']))

    placeholders = ','.join(['?']*len(codes))
    sql = f"""SELECT code, date, open, high, low, close, volume
              FROM daily_kline WHERE code IN ({placeholders})
              AND date >= '2020-06-01' AND date <= '2026-06-29'
              ORDER BY code, date"""
    df = pd.read_sql_query(sql, conn, params=codes)
    conn.close()

    df['date'] = pd.to_datetime(df['date'])
    df['float_shares'] = df['code'].map(fs_map)
    df['turnover'] = df['volume'] * 100 / df['float_shares']
    df['market_cap'] = df['close'] * df['float_shares']

    close = df.pivot(index='date', columns='code', values='close')
    turnover = df.pivot(index='date', columns='code', values='turnover')
    mcap = df.pivot(index='date', columns='code', values='market_cap')
    turn_5 = turnover.rolling(5, min_periods=3).mean()

    # 情绪因子
    daily_ret = close.pct_change()
    is_limit = ((daily_ret >= 0.095) & (daily_ret <= 0.105)).astype(float).fillna(0)
    two_day_limit = (is_limit.shift(1).fillna(0) == 1) & (is_limit == 1)
    two_day_limit = two_day_limit.astype(float)
    daily_limit_count = two_day_limit.sum(axis=1)

    log(f"    {close.shape[0]} 天 x {close.shape[1]} 只, {time.time()-t0:.1f}s")
    return {
        'close': close, 'turnover': turnover, 'mcap': mcap, 'turn_5': turn_5,
        'daily_limit_count': daily_limit_count
    }

def calc_scores(date, data):
    """v66选股评分 = v39g评分 + 两日涨停加分"""
    close = data['close']
    turnover = data['turnover']
    turn_5 = data['turn_5']
    mcap = data['mcap']
    daily_limit_count = data['daily_limit_count']

    t5 = turn_5.loc[date]
    sz = mcap.loc[date]

    scores = pd.Series(0.0, index=close.columns)
    for f in (-t5, -sz):
        valid = f.dropna()
        if len(valid) >= 50:
            ranked = valid.rank(ascending=True, pct=True)
            scores[ranked.index] += ranked

    # 两日涨停加分
    limit_count = daily_limit_count.get(date, 0) if date in daily_limit_count.index else 0
    if limit_count > 0:
        daily_ret = close.pct_change()
        if date in daily_ret.index:
            is_today_limit = (daily_ret.loc[date] >= 0.095) & (daily_ret.loc[date] <= 0.105)
            prev_date_idx = close.index.get_loc(date)
            if prev_date_idx > 0:
                prev_date = close.index[prev_date_idx - 1]
                is_yesterday_limit = (daily_ret.loc[prev_date] >= 0.095) & (daily_ret.loc[prev_date] <= 0.105)
                two_day = is_today_limit & is_yesterday_limit
                two_day = two_day.astype(float)
                if two_day.sum() > 0:
                    two_day_ranked = two_day.rank(ascending=True, pct=True)
                    scores = scores + two_day_ranked * 0.35

    valid_codes = [c for c in scores.dropna().index
                  if close.at[date, c] > 0 and turnover.at[date, c] > 0]
    return scores[valid_codes].sort_values(ascending=False)

def run_fold(data, test_start, test_end, rebal, top_n, sl, tp, hold_max,
             sent_window, sent_thresh):
    """v66_sentiment: v39g+两日涨停选股 + 情绪择时过滤"""
    close = data['close']
    turnover = data['turnover']
    daily_limit_count = data['daily_limit_count']
    dates = sorted(close.index)
    test_dates = [d for d in dates if test_start <= d <= test_end]
    if len(test_dates) < 10:
        return None

    sentiment = daily_limit_count.rolling(sent_window).mean()

    INIT_CASH = 200000
    cash = INIT_CASH
    holdings = {}
    nav_list = []
    days_since = rebal

    def sell(code, date):
        nonlocal cash
        if code in close.columns:
            p = close.at[date, code]
            if not np.isnan(p):
                cash += holdings[code]['shares'] * p * 0.9987
        del holdings[code]

    def buy_new(date):
        nonlocal cash
        # 情绪过滤：market_sentiment > threshold 时才交易
        if date in sentiment.index:
            sent = sentiment.loc[date]
            if not np.isnan(sent) and sent <= sent_thresh:
                return  # 情绪太低，不交易

        scores = calc_scores(date, data)
        target = scores.head(top_n).index.tolist()

        for code in list(holdings.keys()):
            if code not in target:
                sell(code, date)

        n_buy = len(target) - len(holdings)
        if n_buy > 0 and cash > 0:
            per = cash * 0.95 / n_buy
            for code in target:
                if code not in holdings and code in close.columns:
                    p = close.at[date, code]
                    if not np.isnan(p) and p > 0:
                        shares = int(per / p / 100) * 100
                        if shares > 0:
                            cost = shares * p * 1.0003
                            if cost <= cash:
                                cash -= cost
                                holdings[code] = {'shares': shares, 'cost': p, 'days': 0}

    for date in test_dates:
        val = cash
        to_sell = []
        for code, pos in holdings.items():
            if code in close.columns:
                p = close.at[date, code]
                if not np.isnan(p):
                    val += pos['shares'] * p
                    pnl = (p - pos['cost']) / pos['cost']
                    if pnl <= sl or pnl >= tp:
                        to_sell.append(code)
                        continue
                    pos['days'] = pos.get('days', 0) + 1
                    if pos['days'] >= hold_max:
                        to_sell.append(code)

        for code in to_sell:
            sell(code, date)

        nav_list.append({'date': date, 'nav': val})
        days_since += 1

        if days_since >= rebal or len(to_sell) > 0:
            days_since = 0 if days_since >= rebal else days_since
            buy_new(date)

    if not nav_list:
        return None

    nav = pd.Series([n['nav'] for n in nav_list], index=[n['date'] for n in nav_list])
    total = (nav.iloc[-1] / nav.iloc[0] - 1) * 100
    daily_ret = nav.pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    dd = (nav / nav.cummax() - 1).min() * 100
    return {'total': total, 'sharpe': sharpe, 'dd': dd}

def load_results():
    if os.path.exists(RESULT_FILE):
        with open(RESULT_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_results(results):
    with open(RESULT_FILE, 'w') as f:
        json.dump(results, f, indent=2)

def main():
    with open(LOG_FILE, 'w') as f:
        f.write('')

    data = load_data()
    dates = sorted(data['close'].index)
    start_idx = next(i for i, d in enumerate(dates) if d >= pd.Timestamp('2021-01-01'))

    TRAIN = 252
    TEST = 126
    STEP = 63
    REBAL = 5
    TOP_N = 5
    STOP_LOSS = -0.05
    TAKE_PROFIT = 0.05
    HOLD_MAX = 3

    log(f"\n[2] v66_sentiment 情绪阈值精细扫描")
    log(f"{'='*80}")

    # 精细扫描：阈值0.5-8，窗口5-30
    THRESHOLDS = [0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 5, 6, 8]
    WINDOWS = [5, 8, 10, 12, 15, 20, 25, 30]

    results = load_results()
    total_configs = len(THRESHOLDS) * len(WINDOWS)
    done_configs = len(results)
    log(f"    已完成: {done_configs}/{total_configs}")

    for thresh in THRESHOLDS:
        for window in WINDOWS:
            key = "thresh_%.1f_window_%d" % (thresh, window)

            if key in results:
                r = results[key]
                log("  [跳过] 阈值=%.1f, 窗口=%d: 夏普=%+.3f" % (thresh, window, r['sharpe']))
                continue

            t0 = time.time()
            fold_results = []
            i = start_idx
            while i + TRAIN + TEST <= len(dates):
                test_s = dates[i + TRAIN]
                test_e = dates[min(i + TRAIN + TEST - 1, len(dates) - 1)]
                r = run_fold(data, test_s, test_e, REBAL, TOP_N, STOP_LOSS, TAKE_PROFIT, HOLD_MAX,
                            window, thresh)
                if r:
                    fold_results.append(r)
                i += STEP

            if fold_results:
                avg_ret = np.mean([f['total'] for f in fold_results])
                avg_sharpe = np.mean([f['sharpe'] for f in fold_results])
                avg_dd = np.mean([f['dd'] for f in fold_results])
                pos = sum(1 for f in fold_results if f['sharpe'] > 0)
                nf = len(fold_results)

                results[key] = {
                    'thresh': thresh, 'window': window,
                    'ret': round(avg_ret, 2), 'sharpe': round(avg_sharpe, 3),
                    'dd': round(avg_dd, 1), 'pos_rate': round(pos/nf*100, 1),
                    'n_folds': nf,
                }

                save_results(results)

                elapsed = time.time() - t0
                log("  阈值=%.1f, 窗口=%d: 夏普=%+.3f, 收益=%+.1f%%, 回撤=%+.1f%%, 正fold=%d/%d (%.1fs)" % (
                    thresh, window, avg_sharpe, avg_ret, avg_dd, pos, nf, elapsed))

    # 输出最终结果
    log(f"\n{'='*80}")
    log("最终结果 (按夏普排序)")
    log(f"{'='*80}")

    sorted_results = sorted(results.values(), key=lambda x: x['sharpe'], reverse=True)
    log("\n阈值  窗口   收益     夏普     回撤    正fold")
    log("-" * 55)

    for r in sorted_results[:20]:
        marker = " ✅" if r['sharpe'] > 1.3 and r['pos_rate'] >= 70 else ""
        log("%5.1f %4d  %+7.1f%%  %7.3f  %6.1f%%  %5.1f%%%s" % (
            r['thresh'], r['window'], r['ret'], r['sharpe'], r['dd'], r['pos_rate'], marker))

    best = sorted_results[0] if sorted_results else None
    if best:
        log(f"\n{'='*80}")
        log("最优: 阈值=%.1f, 窗口=%d" % (best['thresh'], best['window']))
        log("  夏普=%+.3f, 收益=%+.1f%%, 回撤=%+.1f%%, 正fold=%.1f%%" % (
            best['sharpe'], best['ret'], best['dd'], best['pos_rate']))

if __name__ == '__main__':
    main()
