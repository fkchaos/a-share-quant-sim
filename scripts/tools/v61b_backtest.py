#!/usr/bin/env python3
"""v61b 标准回测（固定参数）"""
import sys
sys.path.insert(0, '/root/a-share-quant-sim')
import sqlite3, numpy as np, pandas as pd

def load_data():
    print("[1] 加载数据...")
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

    print(f"    {close.shape[0]} days, {close.shape[1]} stocks")
    return {'close': close, 'turnover': turnover, 'mcap': mcap, 'turn_5': turn_5}

def calc_scores(date, data):
    """计算选股分数"""
    close = data['close']
    turnover = data['turnover']
    turn_5 = data['turn_5']
    mcap = data['mcap']
    
    t5 = turn_5.loc[date]
    sz = mcap.loc[date]
    
    scores = pd.Series(0.0, index=close.columns)
    for f in (-t5, -sz):
        valid = f.dropna()
        if len(valid) >= 50:
            ranked = valid.rank(ascending=True, pct=True)
            scores[ranked.index] += ranked
    
    valid_codes = [c for c in scores.dropna().index
                  if close.at[date, c] > 0 and turnover.at[date, c] > 0]
    return scores[valid_codes].sort_values(ascending=False)

def run_fold(data, test_start, test_end, rebal, top_n, sl, tp, hold_max):
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
    days_since = rebal

    def sell(code, date):
        nonlocal cash
        if code in close.columns:
            p = close.at[date, code]
            if not np.isnan(p):
                cash += holdings[code]['shares'] * p * 0.9987
        del holdings[code]

    def buy_new(date):
        """买入新股票直到满仓"""
        nonlocal cash
        scores = calc_scores(date, data)
        target = scores.head(top_n).index.tolist()
        
        # 先卖不在目标中的
        for code in list(holdings.keys()):
            if code not in target:
                sell(code, date)
        
        # 再买新的
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

        # 执行卖出
        for code in to_sell:
            sell(code, date)

        nav_list.append({'date': date, 'nav': val})
        days_since += 1

        # 调仓日或有卖出时，都重新计算买入
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
    
    # 分年分析
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

def main():
    data = load_data()
    dates = sorted(data['close'].index)
    start_idx = next(i for i, d in enumerate(dates) if d >= pd.Timestamp('2021-01-01'))

    # 固定参数（v61b最优参数）
    TRAIN = 252
    TEST = 126
    STEP = 63
    REBAL = 5
    TOP_N = 5
    STOP_LOSS = -0.08
    TAKE_PROFIT = 0.25
    HOLD_MAX = 5

    print(f"\n[2] v61b 标准回测")
    print(f"    参数: 止损={STOP_LOSS:.0%}, 止盈={TAKE_PROFIT:.0%}, 持仓={HOLD_MAX}天")
    print(f"    WF: train={TRAIN}, test={TEST}, step={STEP}")
    print(f"{'='*60}")

    fold_results = []
    i = start_idx
    while i + TRAIN + TEST <= len(dates):
        test_s = dates[i + TRAIN]
        test_e = dates[min(i + TRAIN + TEST - 1, len(dates) - 1)]
        r = run_fold(data, test_s, test_e, REBAL, TOP_N, STOP_LOSS, TAKE_PROFIT, HOLD_MAX)
        if r:
            fold_results.append(r)
            print(f"  Fold {len(fold_results):>2}: 夏普={r['sharpe']:>+.3f}, 收益={r['total']:>+7.2f}%, 回撤={r['dd']:>+6.1f}%")
        i += STEP

    if fold_results:
        avg_ret = np.mean([f['total'] for f in fold_results])
        avg_sharpe = np.mean([f['sharpe'] for f in fold_results])
        avg_dd = np.mean([f['dd'] for f in fold_results])
        pos = sum(1 for f in fold_results if f['sharpe'] > 0)
        nf = len(fold_results)

        print(f"\n{'='*60}")
        print(f"WF 结果汇总 ({nf} folds)")
        print(f"{'='*60}")
        print(f"  平均收益: {avg_ret:>+.2f}%")
        print(f"  平均夏普: {avg_sharpe:>+.3f}")
        print(f"  平均回撤: {avg_dd:>+.1f}%")
        print(f"  正fold:   {pos}/{nf} ({pos/nf*100:.1f}%)")
        
        # 分年统计
        yearly_all = {}
        for f in fold_results:
            for year, y in f.get('yearly', {}).items():
                if year not in yearly_all:
                    yearly_all[year] = []
                yearly_all[year].append(y)
        
        if yearly_all:
            print(f"\n  分年统计:")
            for year in sorted(yearly_all.keys()):
                ys = yearly_all[year]
                yr = np.mean([y['ret'] for y in ys])
                ys_sharpe = np.mean([y['sharpe'] for y in ys])
                ydd = np.mean([y['dd'] for y in ys])
                print(f"    {year}: 收益={yr:>+.1f}%, 夏普={ys_sharpe:>+.3f}, 回撤={ydd:>+.1f}%")

if __name__ == '__main__':
    main()
