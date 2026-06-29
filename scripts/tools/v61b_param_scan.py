#!/usr/bin/env python3
"""v61b 参数优化 — 多维度扫描测试"""
import sys
sys.path.insert(0, '/root/a-share-quant-sim')
import sqlite3, numpy as np, pandas as pd

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
df['ret'] = df.groupby('code')['close'].pct_change()
df['float_shares'] = df['code'].map(fs_map)
df['turnover'] = df['volume'] * 100 / df['float_shares']
df['market_cap'] = df['close'] * df['float_shares']

close = df.pivot(index='date', columns='code', values='close')
turnover = df.pivot(index='date', columns='code', values='turnover')
ret = df.pivot(index='date', columns='code', values='ret')
mcap = df.pivot(index='date', columns='code', values='market_cap')

# 预计算不同周期的换手率均值
turn_3 = turnover.rolling(3, min_periods=2).mean()
turn_5 = turnover.rolling(5, min_periods=3).mean()
turn_10 = turnover.rolling(10, min_periods=5).mean()
turn_20 = turnover.rolling(20, min_periods=10).mean()

# 动量因子
mom_3 = close / close.shift(3) - 1
mom_5 = close / close.shift(5) - 1
mom_10 = close / close.shift(10) - 1

# 波动率因子
vol_5 = ret.rolling(5, min_periods=3).std()
vol_10 = ret.rolling(10, min_periods=5).std()

print(f"    {close.shape[0]} days, {close.shape[1]} stocks")

# WF 参数
TRAIN = 252
TEST = 126
STEP = 63
TOP_N = 5
INIT_CASH = 200000

dates = sorted(close.index)
start = next(i for i, d in enumerate(dates) if d >= pd.Timestamp('2021-01-01'))

def run_fold(test_start, test_end, combo_fn, rebal, top_n, sl, tp, hold_max):
    """跑一个fold"""
    test_dates = [d for d in dates if test_start <= d <= test_end]
    if len(test_dates) < 10:
        return None
    
    cash = INIT_CASH
    holdings = {}
    nav_list = []
    days_since = rebal
    
    for date in test_dates:
        val = cash
        to_sell = []
        for code, pos in holdings.items():
            if code in close.columns:
                p = close.at[date, code]
                if not np.isnan(p):
                    val += pos['shares'] * p
                    # 检查止损止盈
                    pnl = (p - pos['cost']) / pos['cost']
                    if pnl <= sl or pnl >= tp:
                        to_sell.append(code)
                        continue
                    # 检查持有天数
                    pos['days'] = pos.get('days', 0) + 1
                    if pos['days'] >= hold_max:
                        to_sell.append(code)
        for code in to_sell:
            p = close.at[date, code]
            if not np.isnan(p):
                cash += holdings[code]['shares'] * p * 0.9987
            del holdings[code]
        
        nav_list.append({'date': date, 'nav': val})
        days_since += 1
        
        if days_since >= rebal:
            days_since = 0
            factors = combo_fn(date)
            if factors is None or factors[0] is None:
                continue
            
            scores = pd.Series(0.0, index=close.columns)
            for f in factors:
                valid = f.dropna()
                if len(valid) < 50:
                    continue
                ranked = valid.rank(ascending=True, pct=True)
                scores[ranked.index] += ranked
            
            valid_codes = [c for c in scores.dropna().index
                          if close.at[date, c] > 0 and turnover.at[date, c] > 0]
            scores = scores[valid_codes].sort_values(ascending=False)
            target = scores.head(top_n).index.tolist()
            
            # 卖出不在目标中的
            for code in list(holdings.keys()):
                if code not in target:
                    if code in close.columns:
                        p = close.at[date, code]
                        if not np.isnan(p):
                            cash += holdings[code]['shares'] * p * 0.9987
                    del holdings[code]
            
            # 买入新股票
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
    
    if not nav_list:
        return None
    
    nav = pd.Series([n['nav'] for n in nav_list], index=[n['date'] for n in nav_list])
    total = (nav.iloc[-1] / nav.iloc[0] - 1) * 100
    daily_ret = nav.pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    dd = (nav / nav.cummax() - 1).min() * 100
    return {'total': total, 'sharpe': sharpe, 'dd': dd, 'n_days': len(nav)}

# 定义测试组合
COMBOS = {
    # === 方案A: 因子权重测试 ===
    'A1: 换手60/市值40': lambda d: (-turn_5.loc[d]*0.6, -mcap.loc[d]*0.4),
    'A2: 换手70/市值30': lambda d: (-turn_5.loc[d]*0.7, -mcap.loc[d]*0.3),
    'A3: 换手40/市值60': lambda d: (-turn_5.loc[d]*0.4, -mcap.loc[d]*0.6),
    'A4: 换手30/市值70': lambda d: (-turn_5.loc[d]*0.3, -mcap.loc[d]*0.7),
    
    # === 方案B: 换手率周期测试 ===
    'B1: 换手3日+市值': lambda d: (-turn_3.loc[d], -mcap.loc[d]),
    'B2: 换手10日+市值': lambda d: (-turn_10.loc[d], -mcap.loc[d]),
    'B3: 换手20日+市值': lambda d: (-turn_20.loc[d], -mcap.loc[d]),
    
    # === 方案C: 添加动量因子 ===
    'C1: 换手+市值+动量3': lambda d: (-turn_5.loc[d], -mcap.loc[d], mom_3.loc[d]),
    'C2: 换手+市值+动量10': lambda d: (-turn_5.loc[d], -mcap.loc[d], mom_10.loc[d]),
    
    # === 方案D: 添加低波因子 ===
    'D1: 换手+市值+低波5': lambda d: (-turn_5.loc[d], -mcap.loc[d], -vol_5.loc[d]),
    'D2: 换手+市值+低波10': lambda d: (-turn_5.loc[d], -mcap.loc[d], -vol_10.loc[d]),
}

# 测试不同参数组合
PARAM_SETS = [
    # (rebal, top_n, sl, tp, hold_max, label)
    (5, 5, -0.10, 0.20, 5, 'v61原始'),
    (3, 5, -0.10, 0.20, 3, '调仓3天'),
    (7, 5, -0.10, 0.20, 7, '调仓7天'),
    (5, 3, -0.10, 0.20, 5, '持仓3只'),
    (5, 8, -0.10, 0.20, 5, '持仓8只'),
    (5, 5, -0.08, 0.15, 5, '止损-8%/止盈15%'),
    (5, 5, -0.12, 0.25, 5, '止损-12%/止盈25%'),
]

print(f"\n[2] 参数扫描测试")
print(f"{'='*80}")

results = []

for combo_name, combo_fn in COMBOS.items():
    for params in PARAM_SETS:
        rebal, top_n, sl, tp, hold_max, param_label = params
        
        fold_results = []
        fold_idx = 0
        i = start
        
        while i + TRAIN + TEST <= len(dates):
            test_s = dates[i + TRAIN]
            test_e = dates[min(i + TRAIN + TEST - 1, len(dates) - 1)]
            r = run_fold(test_s, test_e, combo_fn, rebal, top_n, sl, tp, hold_max)
            if r:
                fold_results.append(r)
            fold_idx += 1
            i += STEP
        
        if fold_results:
            avg_ret = np.mean([f['total'] for f in fold_results])
            avg_sharpe = np.mean([f['sharpe'] for f in fold_results])
            avg_dd = np.mean([f['dd'] for f in fold_results])
            pos = sum(1 for f in fold_results if f['sharpe'] > 0)
            nf = len(fold_results)
            
            results.append({
                'combo': combo_name,
                'params': param_label,
                'ret': avg_ret,
                'sharpe': avg_sharpe,
                'dd': avg_dd,
                'pos_rate': pos/nf*100,
                'n_folds': nf,
            })

# 按夏普排序输出
results.sort(key=lambda x: x['sharpe'], reverse=True)

print(f"\n{'组合':<25} {'参数':<20} {'收益':>8} {'夏普':>8} {'回撤':>8} {'正fold':>8}")
print("-" * 80)

for r in results[:20]:  # Top 20
    marker = "✅" if r['sharpe'] > 2.0 and r['pos_rate'] >= 90 else "  "
    print(f"{r['combo']:<25} {r['params']:<20} {r['ret']:>+7.2f}% {r['sharpe']:>+7.3f} {r['dd']:>+7.1f}% {r['pos_rate']:>6.1f}% {marker}")

print(f"\n{'='*80}")
print(f"参考: v61原始: 夏普=2.342, 正fold=93.75%")
