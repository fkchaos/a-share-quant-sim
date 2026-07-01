#!/usr/bin/env python3
"""v61c + 情绪择时因子 WF参数扫描"""
import sys, os, json, time, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/root/a-share-quant-sim')
import sqlite3, numpy as np, pandas as pd

RESULT_FILE = '/root/a-share-quant-sim/scripts/tools/v61c_sentiment_results.json'
LOG_FILE = '/root/a-share-quant-sim/scripts/tools/v61c_sentiment_scan.log'

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

def calc_factors(data):
    """计算所有情绪因子"""
    close = data['close']
    turnover = data['turnover']
    daily_ret = close.pct_change()
    
    factors = {}
    
    # avg_amplitude: 平均振幅（IC=+0.224）
    close_range = close.rolling(5).max() - close.rolling(5).min()
    factors['avg_amplitude'] = (close_range / close.rolling(5).mean()).mean(axis=1)
    
    # volatility_20d: 20日波动率（IC=+0.167）
    factors['volatility_20d'] = daily_ret.rolling(20, min_periods=10).std().mean(axis=1)
    
    # vol_change: 波动率变化（IC=+0.161）
    vol_20 = daily_ret.rolling(20).std().mean(axis=1)
    vol_60 = daily_ret.rolling(60).std().mean(axis=1)
    factors['vol_change'] = vol_20 / vol_60.replace(0, np.nan)
    
    # breadth_ma5: 站上5日均线比例（IC=-0.123，反向）
    ma5 = close.rolling(5, min_periods=3).mean()
    factors['breadth_ma5'] = (close > ma5).sum(axis=1) / close.shape[1]
    
    # return_dispersion: 收益离散度（IC=+0.110）
    factors['return_dispersion'] = daily_ret.rolling(20, min_periods=10).std().mean(axis=1)
    
    # avg_return_5d: 5日平均收益（IC=-0.174，反向）
    factors['avg_return_5d'] = daily_ret.rolling(5, min_periods=3).mean().mean(axis=1)
    
    # v61b基础因子
    factors['turn_5'] = turnover.rolling(5, min_periods=3).mean()
    
    return factors

def calc_scores(date, data, factors):
    """v61b选股评分"""
    close = data['close']
    turnover = data['turnover']
    mcap = data['mcap']
    
    t5 = factors['turn_5'].loc[date] if date in factors['turn_5'].index else None
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

def run_fold(data, factors, test_start, test_end, params):
    """单fold回测"""
    close = data['close']
    turnover = data['turnover']
    dates = sorted(close.index)
    test_dates = [d for d in dates if test_start <= d <= test_end]
    if len(test_dates) < 10:
        return None

    INIT_CASH = 200000
    cash = INIT_CASH
    holdings = {}
    nav_list = []
    days_since = params['REBALANCE_DAYS']
    
    # 情绪因子
    sentiment_factor = params['SENTIMENT_FACTOR']
    sentiment_mode = params['SENTIMENT_MODE']
    sentiment_threshold = params['SENTIMENT_THRESHOLD']
    sentiment_window = params['SENTIMENT_WINDOW']
    sf = factors.get(sentiment_factor)

    def sell(code, date):
        nonlocal cash
        if code in close.columns:
            p = close.at[date, code]
            if not np.isnan(p):
                cash += holdings[code]['shares'] * p * 0.9987
        del holdings[code]

    def buy_new(date):
        nonlocal cash
        
        # 情绪过滤
        if sf is not None and date in sf.index:
            recent = sf.loc[:date].tail(sentiment_window)
            if len(recent) >= sentiment_window:
                current_pct = (recent < sf.loc[date]).mean()
                
                if sentiment_mode == 'hot' and current_pct < sentiment_threshold:
                    return
                elif sentiment_mode == 'cold' and current_pct > sentiment_threshold:
                    return
        
        scores = calc_scores(date, data, factors)
        if len(scores) == 0:
            return
        target = scores.head(params['MAX_HOLDINGS']).index.tolist()
        
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
                    if pnl <= params['STOP_LOSS'] or pnl >= params['TAKE_PROFIT']:
                        to_sell.append(code)
                        continue
                    pos['days'] = pos.get('days', 0) + 1
                    if pos['days'] >= params['HOLD_DAYS_MAX']:
                        to_sell.append(code)

        for code in to_sell:
            sell(code, date)

        nav_list.append({'date': date, 'nav': val})
        days_since += 1

        if days_since >= params['REBALANCE_DAYS'] or len(to_sell) > 0:
            days_since = 0 if days_since >= params['REBALANCE_DAYS'] else days_since
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
    factors = calc_factors(data)
    dates = sorted(data['close'].index)
    start_idx = next(i for i, d in enumerate(dates) if d >= pd.Timestamp('2021-01-01'))

    TRAIN = 252
    TEST = 126
    STEP = 63
    
    # 基础参数
    base_params = {
        'STOP_LOSS': -0.08,
        'TAKE_PROFIT': 0.25,
        'HOLD_DAYS_MAX': 5,
        'REBALANCE_DAYS': 5,
        'MAX_HOLDINGS': 5,
    }

    log(f"\n[2] v61c + 情绪择时 参数扫描")
    log(f"{'='*80}")

    # 扫描因子
    FACTORS = ['avg_amplitude', 'volatility_20d', 'vol_change', 'breadth_ma5', 'return_dispersion', 'avg_return_5d']
    THRESHOLDS = [0.3, 0.4, 0.5, 0.6, 0.7]
    WINDOWS = [10, 15, 20]
    MODES = [('cold', True), ('hot', False)]

    results = load_results()
    total_configs = len(FACTORS) * len(MODES) * len(THRESHOLDS) * len(WINDOWS)
    done_configs = len(results)
    log(f"    已完成: {done_configs}/{total_configs}")

    for factor_name in FACTORS:
        for mode_name, cold_mode in MODES:
            for thresh in THRESHOLDS:
                for window in WINDOWS:
                    key = f"{factor_name}_{mode_name}_{thresh}_{window}"
                    
                    if key in results:
                        r = results[key]
                        log(f"  [跳过] {factor_name} {mode_name} 阈值={thresh}, 窗口={window}: 夏普={r['sharpe']:+.3f}")
                        continue
                    
                    params = base_params.copy()
                    params['SENTIMENT_FACTOR'] = factor_name
                    params['SENTIMENT_MODE'] = mode_name
                    params['SENTIMENT_THRESHOLD'] = thresh
                    params['SENTIMENT_WINDOW'] = window
                    
                    t0 = time.time()
                    fold_results = []
                    i = start_idx
                    while i + TRAIN + TEST <= len(dates):
                        test_s = dates[i + TRAIN]
                        test_e = dates[min(i + TRAIN + TEST - 1, len(dates) - 1)]
                        r = run_fold(data, factors, test_s, test_e, params)
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
                            'factor': factor_name, 'mode': mode_name, 'thresh': thresh, 'window': window,
                            'ret': round(avg_ret, 2), 'sharpe': round(avg_sharpe, 3),
                            'dd': round(avg_dd, 1), 'pos_rate': round(pos/nf*100, 1),
                            'n_folds': nf,
                        }

                        save_results(results)
                        elapsed = time.time() - t0
                        log(f"  [{factor_name}] {mode_name} 阈值={thresh}, 窗口={window}: 夏普={avg_sharpe:+.3f}, 收益={avg_ret:+.1f}%, 回撤={avg_dd:+.1f}%, 正fold={pos}/{nf} ({elapsed:.1f}s)")

    # 输出最终结果
    log(f"\n{'='*80}")
    log(f"最终结果 (按夏普排序，top 30)")
    log(f"{'='*80}")
    
    sorted_results = sorted(results.values(), key=lambda x: x['sharpe'], reverse=True)
    log(f"\n{'因子':<20} {'模式':<6} {'阈值':>6} {'窗口':>6} {'收益':>10} {'夏普':>8} {'回撤':>8} {'正fold':>8}")
    log("-" * 90)
    
    for r in sorted_results[:30]:
        marker = "✅" if r['sharpe'] > 2.0 and r['pos_rate'] >= 90 else "  "
        log(f"{r['factor']:<20} {r['mode']:<6} {r['thresh']:>6.1f} {r['window']:>6} {r['ret']:>+9.1f}% {r['sharpe']:>+7.3f} {r['dd']:>+7.1f}% {r['pos_rate']:>6.1f}% {marker}")

    # 最优
    if sorted_results:
        best = sorted_results[0]
        log(f"\n{'='*80}")
        log(f"最优: {best['factor']} {best['mode']} 阈值={best['thresh']}, 窗口={best['window']}")
        log(f"  夏普={best['sharpe']:+.3f}, 收益={best['ret']:+.1f}%, 回撤={best['dd']:+.1f}%, 正fold={best['pos_rate']:.1f}%")

if __name__ == '__main__':
    main()
