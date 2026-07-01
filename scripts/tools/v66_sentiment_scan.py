#!/usr/bin/env python3
"""v66_sentiment 情绪阈值参数扫描"""
import sys, os, json, time
sys.path.insert(0, '/root/a-share-quant-sim')
import sqlite3, numpy as np, pandas as pd

RESULT_FILE = '/root/a-share-quant-sim/scripts/tools/v66_sentiment_scan_results.json'
LOG_FILE = '/root/a-share-quant-sim/scripts/tools/v66_sentiment_scan.log'

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
    turn_5 = turnover.rolling(5, min_periods=3).mean()

    # 情绪因子
    daily_ret = close.pct_change()
    is_limit = ((daily_ret >= 0.095) & (daily_ret <= 0.105)).astype(float).fillna(0)
    two_day_limit = (is_limit.shift(1).fillna(0) == 1) & (is_limit == 1)
    two_day_limit = two_day_limit.astype(float)
    daily_limit_count = two_day_limit.sum(axis=1)

    log(f"    {close.shape[0]} days, {close.shape[1]} stocks")
    return {
        'close': close, 'turnover': turnover, 'mcap': mcap, 'turn_5': turn_5,
        'daily_limit_count': daily_limit_count
    }

def calc_v39g_scores(date, data, params):
    """v39g评分逻辑"""
    close = data['close']
    
    # 动量因子
    mom_5 = close.pct_change(5)
    if date not in mom_5.index:
        return pd.Series(0, index=close.columns)
    
    m5 = mom_5.loc[date]
    candidates = m5[m5 > params.get('MOM_THRESHOLD', 0.03)].index.tolist()
    
    if not candidates:
        return pd.Series(0, index=close.columns)
    
    scores = pd.Series(0.0, index=candidates)
    
    # 简化版：只用关键因子
    # 动量
    scores += m5.reindex(candidates).fillna(0) * params.get('W_MOM', 0.08)
    
    # 市值（负向）
    mcap = data['mcap'].loc[date]
    mcap_rank = mcap.reindex(candidates).rank(ascending=True, pct=True)
    scores += (1 - mcap_rank) * params.get('W_SIZE', 0.35)
    
    # 两天涨停
    two_day_limit = data.get('two_day_limit')
    if two_day_limit is not None and date in two_day_limit.index:
        tdl = two_day_limit.loc[date]
        scores += tdl.reindex(candidates).fillna(0) * params.get('W_TWO_DAY_LIMIT', 0.35)
    
    return scores.sort_values(ascending=False)

def run_fold(data, test_start, test_end, rebal, top_n, sl, tp, hold_max,
             sentiment_threshold, sentiment_window):
    close = data['close']
    daily_limit_count = data['daily_limit_count']
    dates = sorted(close.index)
    test_dates = [d for d in dates if test_start <= d <= test_end]
    if len(test_dates) < 10:
        return None

    INIT_CASH = 200000
    cash = INIT_CASH
    holdings = {}
    nav_list = []
    days_since = rebal

    sentiment = daily_limit_count.rolling(sentiment_window).mean()

    # v39g参数
    params = {
        'MOM_THRESHOLD': 0.03,
        'W_MOM': 0.08,
        'W_SIZE': 0.35,
        'W_TWO_DAY_LIMIT': 0.35,
    }

    def sell(code, date):
        nonlocal cash
        if code in close.columns:
            p = close.at[date, code]
            if not np.isnan(p):
                cash += holdings[code]['shares'] * p * 0.9987
        del holdings[code]

    def buy_new(date):
        nonlocal cash
        
        # 情绪检查：市场情绪 > 阈值时才交易
        if date in sentiment.index:
            sent = sentiment.loc[date]
            if pd.notna(sent) and sent < sentiment_threshold:
                return  # 市场冷清，不交易
        
        scores = calc_v39g_scores(date, data, params)
        if scores.empty:
            return
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
    
    yearly = {}
    for year in range(2021, 2027):
        year_mask = nav.index.year == year
        if year_mask.sum() == 0:
            continue
        year_nav = nav[year_mask]
        if len(year_nav) < 2:
            continue
        year_ret = (year_nav.iloc[-1] / year_nav.iloc[0] - 1) * 100
        year_r = year_nav.pct_change().dropna()
        year_sharpe = year_r.mean() / year_r.std() * np.sqrt(252) if year_r.std() > 0 else 0
        year_dd = (year_nav / year_nav.cummax() - 1).min() * 100
        yearly[year] = {'ret': year_ret, 'sharpe': year_sharpe, 'dd': year_dd}
    
    return {'total': total, 'sharpe': sharpe, 'dd': dd, 'yearly': yearly}

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

    log(f"\n[2] v66_sentiment 情绪阈值扫描")
    log(f"{'='*80}")

    # 扫描情绪阈值和窗口
    THRESHOLDS = [1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 15]
    WINDOWS = [10, 15, 20]

    results = load_results()
    total_configs = len(THRESHOLDS) * len(WINDOWS)
    done_configs = len(results)
    log(f"    已完成: {done_configs}/{total_configs}")

    for thresh in THRESHOLDS:
        for window in WINDOWS:
            key = f"thresh_{thresh}_window_{window}"
            
            if key in results:
                r = results[key]
                log(f"  [跳过] 阈值={thresh}, 窗口={window}: 夏普={r['sharpe']:+.3f}")
                continue
            
            t0 = time.time()
            fold_results = []
            i = start_idx
            while i + TRAIN + TEST <= len(dates):
                test_s = dates[i + TRAIN]
                test_e = dates[min(i + TRAIN + TEST - 1, len(dates) - 1)]
                r = run_fold(data, test_s, test_e, REBAL, TOP_N, STOP_LOSS, TAKE_PROFIT, HOLD_MAX,
                            thresh, window)
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
                marker = "✅" if avg_sharpe > 1.0 and pos/nf >= 0.6 else "  "
                log(f"  阈值={thresh:>2}, 窗口={window}: 夏普={avg_sharpe:+.3f}, 收益={avg_ret:+.1f}%, 回撤={avg_dd:+.1f}%, 正fold={pos}/{nf} {marker} ({elapsed:.1f}s)")

    # 输出最终结果
    log(f"\n{'='*80}")
    log(f"最终结果 (按夏普排序)")
    log(f"{'='*80}")
    
    sorted_results = sorted(results.values(), key=lambda x: x['sharpe'], reverse=True)
    log(f"\n{'阈值':>6} {'窗口':>6} {'收益':>10} {'夏普':>8} {'回撤':>8} {'正fold':>8}")
    log("-" * 60)
    
    for r in sorted_results[:20]:
        marker = "✅" if r['sharpe'] > 1.0 and r['pos_rate'] >= 60 else "  "
        log(f"{r['thresh']:>6} {r['window']:>6} {r['ret']:>+9.1f}% {r['sharpe']:>+7.3f} {r['dd']:>+7.1f}% {r['pos_rate']:>6.1f}% {marker}")

    best = sorted_results[0] if sorted_results else None
    if best:
        log(f"\n{'='*80}")
        log(f"最优: 阈值={best['thresh']}, 窗口={best['window']}")
        log(f"  夏普={best['sharpe']:+.3f}, 收益={best['ret']:+.1f}%, 回撤={best['dd']:+.1f}%, 正fold={best['pos_rate']:.1f}%")

if __name__ == '__main__':
    main()
