#!/usr/bin/env python3
"""BigQuant 换手率3因子 独立回测 (不走 WF 框架)"""
import sys, os
sys.path.insert(0, '/root/a-share-quant-sim')
import sqlite3, numpy as np, pandas as pd
from datetime import datetime

print("[1] 加载数据...")
conn = sqlite3.connect('data/quant_stocks.db', timeout=30)
conn.execute('PRAGMA journal_mode=WAL')

# 加载 zz1800 K线 + float_shares
codes_df = pd.read_sql_query('SELECT code, float_shares FROM stock_pool_zz1800', conn)
codes = codes_df['code'].tolist()
fs_map = dict(zip(codes_df['code'], codes_df['float_shares']))

placeholders = ','.join(['?']*len(codes))
sql = f"""SELECT code, date, open, high, low, close, volume
          FROM daily_kline WHERE code IN ({placeholders})
          AND date >= '2020-12-01' AND date <= '2026-06-29'
          ORDER BY code, date"""
df = pd.read_sql_query(sql, conn, params=codes)
conn.close()

df['date'] = pd.to_datetime(df['date'])
df['ret'] = df.groupby('code')['close'].pct_change()

# 计算换手率
df['float_shares'] = df['code'].map(fs_map)
df['turnover'] = df['volume'] / df['float_shares']

print(f"    {df['code'].nunique()} stocks, {df['date'].nunique()} days")

# Pivot
close = df.pivot(index='date', columns='code', values='close')
turnover = df.pivot(index='date', columns='code', values='turnover')
ret = df.pivot(index='date', columns='code', values='ret')

# 因子: 5日均换手率, 20日均换手率, 20日换手率波动
turn_5 = turnover.rolling(5, min_periods=3).mean()
turn_20 = turnover.rolling(20, min_periods=10).mean()
turn_std = turnover.rolling(20, min_periods=10).std()

print("[2] 运行回测...")

# 回测参数
START = '2021-01-04'
END = '2026-06-26'
REBAL_DAYS = 5
TOP_N = 5
INIT_CASH = 200000  # 与我们账户2一致

dates = sorted(close.index)
start_idx = next(i for i, d in enumerate(dates) if d >= pd.Timestamp(START))
end_idx = next(i for i, d in enumerate(dates) if d >= pd.Timestamp(END))

# 初始化
cash = INIT_CASH
holdings = {}  # code -> {shares, cost}
daily_nav = []
trades = []
days_since_rebal = REBAL_DAYS  # 首日立即调仓

for i in range(start_idx, end_idx + 1):
    date = dates[i]
    daily_value = cash

    # 计算持仓市值
    for code, pos in list(holdings.items()):
        p = close.at[date, code] if code in close.columns and not np.isnan(close.at[date, code]) else pos['cost']
        daily_value += pos['shares'] * p

    daily_nav.append({'date': date, 'nav': daily_value, 'cash': cash, 'n_hold': len(holdings)})

    # 检查是否需要调仓
    days_since_rebal += 1
    if days_since_rebal >= REBAL_DAYS:
        days_since_rebal = 0

        # 获取当日因子值
        t5 = turn_5.loc[date] if date in turn_5.index else None
        t20 = turn_20.loc[date] if date in turn_20.index else None
        ts = turn_std.loc[date] if date in turn_std.index else None

        if t5 is None or t20 is None or ts is None:
            continue

        # 等权评分 (负向: 低换手=高分)
        scores = pd.Series(0.0, index=close.columns)
        for f in [t5, t20, ts]:
            valid = f.dropna()
            if len(valid) > 0:
                ranked = valid.rank(ascending=True, pct=True)
                scores[ranked.index] += (1 - ranked)

        # 过滤: 有值 + 价格 > 0
        valid_codes = scores.dropna().index
        valid_codes = [c for c in valid_codes if close.at[date, c] > 0 and turnover.at[date, c] > 0]
        scores = scores[valid_codes].sort_values(ascending=False)

        # 选前 N 只
        target = scores.head(TOP_N).index.tolist()

        # 卖出不在目标中的
        for code in list(holdings.keys()):
            if code not in target:
                if code in close.columns and not np.isnan(close.at[date, code]):
                    sell_price = close.at[date, code]
                    sell_amount = holdings[code]['shares'] * sell_price
                    cash += sell_amount * (1 - 0.0013)  # 卖出手续费
                    trades.append({'date': date, 'code': code, 'action': 'SELL',
                                   'shares': holdings[code]['shares'], 'price': sell_price})
                del holdings[code]

        # 买入新标的 (等权)
        n_buy = len(target) - len(holdings)
        if n_buy > 0 and cash > 0:
            per_stock = cash * 0.95 / n_buy  # 留5%现金
            for code in target:
                if code not in holdings and code in close.columns:
                    buy_price = close.at[date, code]
                    if buy_price > 0:
                        shares = int(per_stock / buy_price / 100) * 100
                        if shares > 0:
                            cost = shares * buy_price * (1 + 0.0003)  # 买入手续费
                            cash -= cost
                            holdings[code] = {'shares': shares, 'cost': buy_price}
                            trades.append({'date': date, 'code': code, 'action': 'BUY',
                                           'shares': shares, 'price': buy_price})

# 计算收益
nav_df = pd.DataFrame(daily_nav)
nav_df['date'] = pd.to_datetime(nav_df['date'])
nav_df.set_index('date', inplace=True)
nav_df['ret'] = nav_df['nav'].pct_change()

total_ret = (nav_df['nav'].iloc[-1] / nav_df['nav'].iloc[0] - 1) * 100
annual_ret = (nav_df['nav'].iloc[-1] / nav_df['nav'].iloc[0]) ** (252 / len(nav_df)) - 1
sharpe = nav_df['ret'].mean() / nav_df['ret'].std() * np.sqrt(252) if nav_df['ret'].std() > 0 else 0
max_dd = (nav_df['nav'] / nav_df['nav'].cummax() - 1).min() * 100
win_days = (nav_df['ret'] > 0).sum()
total_days = nav_df['ret'].dropna().shape[0]

print(f"\n{'='*50}")
print(f"BigQuant 换手率3因子 复现回测")
print(f"{'='*50}")
print(f"区间: {START} ~ {END}")
print(f"初始资金: ¥{INIT_CASH:,.0f}")
print(f"持仓数: {TOP_N}, 调仓周期: {REBAL_DAYS}天")
print(f"手续费: 买万三 + 卖千一")
print(f"{'='*50}")
print(f"总收益:  {total_ret:+.2f}%")
print(f"年化:    {annual_ret*100:+.2f}%")
print(f"夏普:    {sharpe:.3f}")
print(f"最大回撤: {max_dd:.2f}%")
print(f"日胜率:  {win_days}/{total_days} ({win_days/total_days*100:.1f}%)")
print(f"交易次数: {len(trades)}")
print(f"{'='*50}")

# BigQuant 对比
print(f"\nBigQuant 原版: 年化38.24%, 夏普1.16, 回撤31.20%")
print(f"我们的复现:   年化{annual_ret*100:.2f}%, 夏普{sharpe:.3f}, 回撤{abs(max_dd):.2f}%")

# 保存 NAV
nav_df.to_csv('/root/alpha-research/reports/v61_turnover_3factor_nav.csv')
print(f"\n[save] -> /root/alpha-research/reports/v61_turnover_3factor_nav.csv")
