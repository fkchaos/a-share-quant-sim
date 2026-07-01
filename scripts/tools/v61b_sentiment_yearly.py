#!/usr/bin/env python3
"""v61b + market_sentiment 分年验证（用原始阈值，非分位数）"""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/root/a-share-quant-sim')
import sqlite3, numpy as np, pandas as pd

def load_data():
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

    return {'close': close, 'turnover': turnover, 'mcap': mcap}

def calc_market_sentiment(close, window):
    """计算market_sentiment"""
    daily_ret = close.pct_change()
    is_limit = ((daily_ret >= 0.095) & (daily_ret <= 0.105)).astype(float).fillna(0)
    two_day_limit = (is_limit.shift(1).fillna(0) == 1) & (is_limit == 1)
    daily_limit_count = two_day_limit.astype(float).sum(axis=1)
    sentiment = daily_limit_count.rolling(window).mean()
    return sentiment, daily_limit_count

def run_v61b_with_sentiment(data, sentiment, test_start, test_end, threshold, mode):
    """v61b + sentiment过滤回测"""
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
    REBAL = 5
    TOP_N = 5
    SL = -0.08
    TP = 0.25
    HOLD_MAX = 5

    def calc_scores(date):
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

    def sell(code, date):
        nonlocal cash
        if code in close.columns:
            p = close.at[date, code]
            if not np.isnan(p):
                cash += holdings[code]['shares'] * p * 0.9987
        del holdings[code]

    def buy_new(date):
        nonlocal cash
        
        # sentiment过滤
        if date in sentiment.index:
            sent = sentiment.loc[date]
            if not np.isnan(sent):
                if mode == 'hot' and sent >= threshold:
                    return
                elif mode == 'cold' and sent <= threshold:
                    return
        
        scores = calc_scores(date)
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

    return {'total': total, 'sharpe': sharpe, 'dd': dd, 'nav': nav}

def main():
    print("加载数据...")
    data = load_data()
    close = data['close']
    
    # 测试不同配置
    configs = [
        (10, 2, 'hot'),
        (15, 2, 'hot'),
        (20, 2, 'hot'),
        (15, 3, 'hot'),
        (20, 3, 'hot'),
        (10, 1, 'hot'),
        (15, 1, 'hot'),
    ]
    
    for window, threshold, mode in configs:
        print(f"\n{'='*80}")
        print(f"window={window}, threshold={threshold}, mode={mode}")
        print(f"{'='*80}")
        
        sentiment, raw_count = calc_market_sentiment(close, window)
        
        # 分年回测
        yearly_results = {}
        for year in range(2021, 2027):
            year_dates = [d for d in sorted(close.index) if d.year == year]
            if len(year_dates) < 20:
                continue
            
            test_start = year_dates[0]
            test_end = year_dates[-1]
            
            result = run_v61b_with_sentiment(data, sentiment, test_start, test_end, threshold, mode)
            if result:
                yearly_results[year] = result
        
        # 汇总
        print(f"{'年份':<6} {'收益':>10} {'夏普':>8} {'回撤':>8}")
        print("-" * 40)
        
        total_sharpes = []
        for year, r in sorted(yearly_results.items()):
            print(f"{year:<6} {r['total']:>+9.1f}% {r['sharpe']:>+7.3f} {r['dd']:>+7.1f}%")
            total_sharpes.append(r['sharpe'])
        
        if total_sharpes:
            avg_sharpe = np.mean(total_sharpes)
            pos_years = sum(1 for s in total_sharpes if s > 0)
            print(f"\n平均夏普: {avg_sharpe:+.3f}, 正年: {pos_years}/{len(total_sharpes)}")

if __name__ == '__main__':
    main()
