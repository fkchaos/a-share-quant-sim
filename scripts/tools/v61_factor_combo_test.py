#!/usr/bin/env python3
"""v61 因子组合批量测试 — zz1800 内多因子组合"""
import sys, os
sys.path.insert(0, '/root/a-share-quant-sim')
import sqlite3, numpy as np, pandas as pd

print("[1] 加载数据...")
conn = sqlite3.connect('data/quant_stocks.db', timeout=30)
conn.execute('PRAGMA journal_mode=WAL')
codes_df = pd.read_sql_query('SELECT code, float_shares FROM stock_pool_zz1800', conn)
codes = codes_df['code'].tolist()
fs_map = dict(zip(codes_df['code'], codes_df['float_shares']))

placeholders = ','.join(['?']*len(codes))
sql = f"""SELECT code, date, open, high, low, close, volume, amount
          FROM daily_kline WHERE code IN ({placeholders})
          AND date >= '2020-12-01' AND date <= '2026-06-29'
          ORDER BY code, date"""
df = pd.read_sql_query(sql, conn, params=codes)
conn.close()
df['date'] = pd.to_datetime(df['date'])
df['ret'] = df.groupby('code')['close'].pct_change()
df['float_shares'] = df['code'].map(fs_map)
df['turnover'] = df['volume'] / df['float_shares']
df['market_cap'] = df['close'] * df['float_shares']

print(f"    {df['code'].nunique()} stocks, {df['date'].nunique()} days")

# Pivot
close = df.pivot(index='date', columns='code', values='close')
turnover = df.pivot(index='date', columns='code', values='turnover')
ret = df.pivot(index='date', columns='code', values='ret')
volume = df.pivot(index='date', columns='code', values='volume')
mcap = df.pivot(index='date', columns='code', values='market_cap')

# 预计算因子面板
print("[1b] 预计算因子面板...")
turn_5 = turnover.rolling(5, min_periods=3).mean()
turn_20 = turnover.rolling(20, min_periods=10).mean()
turn_std = turnover.rolling(20, min_periods=10).std()
mom_5 = close / close.shift(5) - 1
mom_10 = close / close.shift(10) - 1
vol_20 = ret.rolling(20, min_periods=10).std()
amt_20 = volume.rolling(20, min_periods=10).mean()
size_panel = mcap  # 负向

# 因子定义: 返回 Series (index=code)
def factor_turn_5(date):
    return -turn_5.loc[date] if date in turn_5.index else None

def factor_turn_20(date):
    return -turn_20.loc[date] if date in turn_20.index else None

def factor_size(date):
    return -size_panel.loc[date] if date in size_panel.index else None

def factor_mom_5(date):
    return mom_5.loc[date] if date in mom_5.index else None

def factor_mom_10(date):
    return mom_10.loc[date] if date in mom_10.index else None

def factor_vol_20(date):
    return -vol_20.loc[date] if date in vol_20.index else None

def factor_amount_20(date):
    return -amt_20.loc[date] if date in amt_20.index else None

# 组合定义: (name, [factor_functions], [weights])
COMBOS = [
    ("单因子: 换手5日",        [factor_turn_5],           [1.0]),
    ("单因子: 换手20日",       [factor_turn_20],          [1.0]),
    ("单因子: 小市值",         [factor_size],             [1.0]),
    ("单因子: 动量5日",        [factor_mom_5],            [1.0]),
    ("单因子: 低波动",         [factor_vol_20],           [1.0]),
    ("双因子: 换手5+小市值",   [factor_turn_5, factor_size], [0.5, 0.5]),
    ("双因子: 换手20+小市值",  [factor_turn_20, factor_size],[0.5, 0.5]),
    ("双因子: 换手5+动量5",    [factor_turn_5, factor_mom_5],[0.5, 0.5]),
    ("双因子: 小市值+动量5",   [factor_size, factor_mom_5],  [0.5, 0.5]),
    ("三因子: 换手5+小市值+动量5", [factor_turn_5, factor_size, factor_mom_5], [0.33, 0.33, 0.34]),
    ("三因子: 换手20+小市值+低波动", [factor_turn_20, factor_size, factor_vol_20], [0.33, 0.33, 0.34]),
    ("四因子: 换手5+换手20+小市值+动量5", [factor_turn_5, factor_turn_20, factor_size, factor_mom_5], [0.25, 0.25, 0.25, 0.25]),
    ("四因子: 换手5+小市值+动量5+低波动", [factor_turn_5, factor_size, factor_mom_5, factor_vol_20], [0.25, 0.25, 0.25, 0.25]),
    ("五因子: 全部", [factor_turn_5, factor_turn_20, factor_size, factor_mom_5, factor_vol_20], [0.2, 0.2, 0.2, 0.2, 0.2]),
]

# 回测函数
def backtest(date_range, factors_list, weights, top_n=5, rebal=5, init_cash=200000):
    cash = init_cash
    holdings = {}
    nav_list = []
    days_since = rebal

    for date in date_range:
        # 计算净值
        val = cash
        for code, pos in holdings.items():
            if code in close.columns and date in close.index:
                p = close.at[date, code]
                if not np.isnan(p):
                    val += pos['shares'] * p
        nav_list.append(val)

        days_since += 1
        if days_since >= rebal:
            days_since = 0
            # 评分
            scores = pd.Series(0.0, index=close.columns)
            for fn, w in zip(factors_list, weights):
                f = fn(date)
                if f is None:
                    continue
                valid = f.dropna()
                if len(valid) < 50:
                    continue
                ranked = valid.rank(ascending=True, pct=True)
                scores[ranked.index] += w * ranked

            # 过滤
            valid_codes = [c for c in scores.dropna().index
                          if close.at[date, c] > 0 and turnover.at[date, c] > 0]
            scores = scores[valid_codes].sort_values(ascending=False)
            target = scores.head(top_n).index.tolist()

            # 卖出
            for code in list(holdings.keys()):
                if code not in target:
                    if code in close.columns and date in close.index:
                        p = close.at[date, code]
                        if not np.isnan(p):
                            cash += holdings[code]['shares'] * p * 0.9987
                    del holdings[code]

            # 买入
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
                                    holdings[code] = {'shares': shares, 'cost': p}

    nav = pd.Series(nav_list, index=date_range)
    total = (nav.iloc[-1] / nav.iloc[0] - 1) * 100
    ann = (nav.iloc[-1] / nav.iloc[0]) ** (252 / len(nav)) - 1
    daily_ret = nav.pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    dd = (nav / nav.cummax() - 1).min() * 100
    return total, ann * 100, sharpe, dd, nav

# 运行
dates = sorted(close.index)
start = next(i for i, d in enumerate(dates) if d >= pd.Timestamp('2021-01-04'))
end = next(i for i, d in enumerate(dates) if d >= pd.Timestamp('2026-06-26'))
test_dates = dates[start:end+1]

print(f"\n[2] 测试 {len(COMBOS)} 种因子组合...")
print(f"{'组合名':<35} {'总收益':>8} {'年化':>8} {'夏普':>6} {'回撤':>8}")
print("-" * 75)

results = []
for name, fns, ws in COMBOS:
    total, ann, sharpe, dd, nav = backtest(test_dates, fns, ws)
    results.append((name, total, ann, sharpe, dd))
    print(f"{name:<35} {total:>+7.2f}% {ann:>+7.2f}% {sharpe:>6.3f} {dd:>7.2f}%")

# 排序
print(f"\n{'='*75}")
print("按夏普排序:")
results.sort(key=lambda x: x[3], reverse=True)
for name, total, ann, sharpe, dd in results:
    marker = " ★" if sharpe > 0.3 else ""
    print(f"  {name:<35} 夏普={sharpe:+.3f}  年化={ann:+.2f}%  回撤={dd:.2f}%{marker}")

print(f"\n参考: v39g 夏普=1.164, 年化=+16.02%, 回撤=-14.0%")
print(f"参考: BigQuant 原版 夏普=1.16, 年化=+38.24%, 回撤=-31.20%")
