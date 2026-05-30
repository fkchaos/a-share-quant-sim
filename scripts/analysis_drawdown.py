#!/usr/bin/env python3
"""分析当前策略的回撤来源"""
import pandas as pd
import numpy as np

nav = pd.read_csv("/root/data/backtest_results/20260530_125622/nav_v3_optimized.csv")
nav_dates = pd.to_datetime(nav.iloc[:, 1], format='mixed')
nav_series = pd.Series(nav.iloc[:, 0].values, index=nav_dates)

rets = nav_series.pct_change().dropna()
cummax = nav_series.cummax()
drawdown = (nav_series - cummax) / cummax

max_dd_date = drawdown.idxmin()
peak_date = nav_series.loc[:max_dd_date].idxmax()
print(f"最大回撤区间: {peak_date.strftime('%Y-%m-%d')} -> {max_dd_date.strftime('%Y-%m-%d')}")
print(f"回撤幅度: {drawdown.loc[max_dd_date]:.2%}")

dd_top5 = drawdown.nsmallest(5).sort_index()
print(f"\n五大回撤谷底（按时序）:")
for date, dd in dd_top5.items():
    print(f"  {date.strftime('%Y-%m-%d')}  DD={dd:.2%}")

monthly = pd.read_csv("/root/data/backtest_results/20260530_125622/monthly_returns_v3_optimized.csv", index_col=0)
all_rets = monthly.values.flatten()
all_rets = all_rets[~np.isnan(all_rets) & (all_rets != 0)]

print(f"\n月度收益分布（{len(all_rets)} 个月）:")
print(f"  正/负: {sum(all_rets > 0)}/{sum(all_rets < 0)}")
print(f"  均值: {np.mean(all_rets):.2%}  std: {np.std(all_rets):.2%}")
print(f"  最大涨/跌: {np.max(all_rets):.2%} / {np.min(all_rets):.2%}")

print(f"\n各年度:")
for col in monthly.columns:
    vals = monthly[col].dropna()
    vals = vals[vals != 0]
    if len(vals) > 0:
        yr_ret = (1 + vals).prod() - 1
        print(f"  {col}: 年化={yr_ret:+.1%}  月均={vals.mean():.2%}  正/总={sum(vals > 0)}/{len(vals)}")

print(f"\n日收益统计:")
print(f"  年化波动: {rets.std() * np.sqrt(252):.2%}")
print(f"  正收益日: {(rets > 0).mean():.1%}")
print(f"  最大日涨/跌: {rets.max():.2%} / {rets.min():.2%}")
