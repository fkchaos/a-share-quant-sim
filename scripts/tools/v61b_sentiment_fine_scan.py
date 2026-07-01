#!/usr/bin/env python3
"""v61b + market_sentiment 精细扫描（仅cold模式 + 分年详情）"""
import sys, os, json, time, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/root/a-share-quant-sim')
import sqlite3, numpy as np, pandas as pd

RESULT_FILE = '/root/a-share-quant-sim/scripts/tools/v61b_sentiment_fine_results.json'
LOG_FILE = '/root/a-share-quant-sim/scripts/tools/v61b_sentiment_fine_scan.log'

def log(msg):
    print(msg, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(msg + '\n')

def load_data():
    log("[1] 加载数据...")
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

    log(f"    {close.shape[0]} days, {close.shape[1]} stocks")
    return {'close': close, 'turnover': turnover, 'mcap': mcap}

def calc_market_sentiment(close, window):
    """计算market_sentiment"""
    daily_ret = close.pct_change()
    is_limit = ((daily_ret >= 0.095) & (daily_ret <= 0.105)).astype(float).fillna(0)
    two_day_limit = (is_limit.shift(1).fillna(0) == 1) & (is_limit == 1)
    daily_limit_count = two_day_limit.astype(float).sum(axis=1)
    sentiment = daily_limit_count.rolling(window).mean()
    return sentiment

def calc_scores(date, close, turnover, mcap):
    """v61b选股评分"""
    t5 = turnover.rolling(5, min_periods=3).mean().loc[date] if date in turnover.index else None
    sz = mcap.loc[date] if date in mcap.index else None
    if t5 is None or sz is None:
        return pd.Series(dtype=float)
    scores = pd.Series(0.0, index=close.columns)
    for f in (-t5, -sz):
        valid = f.dropna()
        if len(valid) >= 50:
            ranked = valid.rank(ascending=True, pct=True)
            scores[ranked.index] += ranked
    valid_codes = [c for c in scores.dropna().index
                  if close.at[date, c] > 0 and turnover.at[date, c] > 0]
    return scores[valid_codes].sort_values(ascending=False)

def run_fold(data, sentiment, test_start, test_end, threshold):
    """单fold回测（cold模式：情绪高时停止交易）"""
    close = data['close']
    turnover = data['turnover']
    mcap = data['mcap']
    dates = sorted(close.index)
    test_dates = [d for d in dates if test_start <= d <= test_end]
    if len(test_dates) < 10:
        return None

    INIT_CASH = 200000
    cash = INIT_CASH
    holdings = {}
    nav_list = []
    days_since = 5
    TOP_N = 5
    SL = -0.08
    TP = 0.25
    HOLD_MAX = 5
    REBAL = 5

    def sell(code, date):
        nonlocal cash
        if code in close.columns:
            p = close.at[date, code]
            if not np.isnan(p):
                cash += holdings[code]['shares'] * p * 0.9987
        del holdings[code]

    def buy_new(date):
        nonlocal cash
        
        # cold模式：情绪高时停止交易（sent >= threshold时停止）
        if date in sentiment.index:
            sent = sentiment.loc[date]
            if not np.isnan(sent):
                if sent >= threshold:  # 情绪高，停止交易
                    return
        
        scores = calc_scores(date, close, turnover, mcap)
        if len(scores) == 0:
            return
        target = scores.head(TOP_N).index.tolist()
        
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
                    if pnl <= SL or pnl >= TP:
                        to_sell.append(code)
                        continue
                    pos['days'] = pos.get('days', 0) + 1
                    if pos['days'] >= HOLD_MAX:
                        to_sell.append(code)

        for code in to_sell:
            sell(code, date)

        nav_list.append({'date': date, 'nav': val})
        days_since += 1

        if days_since >= REBAL or len(to_sell) > 0:
            days_since = 0 if days_since >= REBAL else days_since
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
    close = data['close']
    dates = sorted(close.index)
    start_idx = next(i for i, d in enumerate(dates) if d >= pd.Timestamp('2021-01-01'))

    TRAIN = 252
    TEST = 126
    STEP = 63

    log(f"\n[2] v61b + market_sentiment 精细扫描 (仅cold模式)")
    log(f"{'='*80}")

    # 精细参数范围（仅cold模式）
    WINDOWS = [5, 8, 10, 12, 15, 18, 20, 25, 30]
    THRESHOLDS = [0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0]

    results = load_results()
    total_configs = len(WINDOWS) * len(THRESHOLDS)
    done_configs = len([k for k in results.keys() if k.startswith('cold_')])
    log(f"    已完成cold: {done_configs}/{total_configs}")

    for window in WINDOWS:
        sentiment = calc_market_sentiment(close, window)
        
        for threshold in THRESHOLDS:
            key = f"cold_w{window}_t{threshold}"
            
            if key in results:
                r = results[key]
                log(f"  [跳过] window={window}, threshold={threshold}: 夏普={r['sharpe']:+.3f}")
                continue
            
            t0 = time.time()
            fold_results = []
            yearly_results = {}
            
            i = start_idx
            while i + TRAIN + TEST <= len(dates):
                test_s = dates[i + TRAIN]
                test_e = dates[min(i + TRAIN + TEST - 1, len(dates) - 1)]
                r = run_fold(data, sentiment, test_s, test_e, threshold)
                if r:
                    fold_results.append(r)
                    year = test_s.year
                    if year not in yearly_results:
                        yearly_results[year] = []
                    yearly_results[year].append(r)
                i += STEP

            if fold_results:
                avg_ret = np.mean([f['total'] for f in fold_results])
                avg_sharpe = np.mean([f['sharpe'] for f in fold_results])
                avg_dd = np.mean([f['dd'] for f in fold_results])
                pos = sum(1 for f in fold_results if f['sharpe'] > 0)
                nf = len(fold_results)
                
                # 分年统计
                yearly_stats = {}
                for year, folds in sorted(yearly_results.items()):
                    yearly_stats[year] = {
                        'sharpe': round(np.mean([f['sharpe'] for f in folds]), 3),
                        'ret': round(np.mean([f['total'] for f in folds]), 1),
                        'dd': round(np.mean([f['dd'] for f in folds]), 1),
                        'n_folds': len(folds),
                    }

                results[key] = {
                    'mode': 'cold', 'window': window, 'thresh': threshold,
                    'ret': round(avg_ret, 2), 'sharpe': round(avg_sharpe, 3),
                    'dd': round(avg_dd, 1), 'pos_rate': round(pos/nf*100, 1),
                    'n_folds': nf,
                    'yearly': yearly_stats,
                }

                save_results(results)
                elapsed = time.time() - t0
                log(f"  window={window}, threshold={threshold}: 夏普={avg_sharpe:+.3f}, 收益={avg_ret:+.1f}%, 回撤={avg_dd:+.1f}%, 正fold={pos}/{nf} ({elapsed:.1f}s)")

    # 输出最终结果
    log(f"\n{'='*80}")
    log(f"最终结果 (按夏普排序，top 20)")
    log(f"{'='*80}")
    
    cold_results = {k: v for k, v in results.items() if k.startswith('cold_')}
    sorted_results = sorted(cold_results.values(), key=lambda x: x['sharpe'], reverse=True)
    log(f"\n{'窗口':>6} {'阈值':>6} {'收益':>10} {'夏普':>8} {'回撤':>8} {'正fold':>8}")
    log("-" * 60)
    
    for r in sorted_results[:20]:
        marker = "✅" if r['sharpe'] > 2.0 and r['pos_rate'] >= 90 else "  "
        log(f"{r['window']:>6} {r['thresh']:>6.1f} {r['ret']:>+9.1f}% {r['sharpe']:>+7.3f} {r['dd']:>+7.1f}% {r['pos_rate']:>6.1f}% {marker}")

    # 最优配置的分年详情
    if sorted_results:
        best = sorted_results[0]
        log(f"\n{'='*80}")
        log(f"最优: cold window={best['window']}, threshold={best['thresh']}")
        log(f"  夏普={best['sharpe']:+.3f}, 收益={best['ret']:+.1f}%, 回撤={best['dd']:+.1f}%")
        log(f"\n  分年详情:")
        log(f"  {'年份':<6} {'夏普':>8} {'收益':>10} {'回撤':>8} {'fold数':>6}")
        log(f"  " + "-" * 40)
        for year, ys in sorted(best['yearly'].items()):
            log(f"  {year:<6} {ys['sharpe']:>+7.3f} {ys['ret']:>+9.1f}% {ys['dd']:>+7.1f}% {ys['n_folds']:>6}")

if __name__ == '__main__':
    main()
