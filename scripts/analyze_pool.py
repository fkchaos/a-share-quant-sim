#!/usr/bin/env python3
"""分析选股池市值分布"""
import sys, os
sys.path.insert(0, '.')
sys.path.insert(0, 'scripts')

from v13_small_mid_short import load_small_cap_panel, V13Config
from v20_tail_pick import V20Config
import pandas as pd

close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = load_small_cap_panel()

latest_date = close_panel.index[-1]
avg_amount = amount_panel.rolling(20).mean() / 1e4  # 万元
day_amount = avg_amount.loc[latest_date].dropna()

print(f"=== 选股池市值分布分析 ({latest_date.date()}) ===")
print(f"总股票池: {len(day_amount)} 只")

# v13 流动性范围
v13_mask = (day_amount > V13Config.min_liquidity) & (day_amount < V13Config.max_liquidity)
v13_pool = day_amount[v13_mask].sort_values()
print(f"\nv13 流动性范围: {V13Config.min_liquidity}w-{V13Config.max_liquidity}w")
print(f"v13 候选池: {len(v13_pool)} 只")
if len(v13_pool) > 0:
    print(f"  最小: {v13_pool.min():.0f}w | 最大: {v13_pool.max():.0f}w | 中位: {v13_pool.median():.0f}w")
    small = v13_pool[v13_pool < 2000]
    mid = v13_pool[(v13_pool >= 2000) & (v13_pool < 5000)]
    large = v13_pool[v13_pool >= 5000]
    print(f"  小盘(<2000w): {len(small)} ({len(small)/len(v13_pool)*100:.0f}%)")
    print(f"  中盘(2000-5000w): {len(mid)} ({len(mid)/len(v13_pool)*100:.0f}%)")
    print(f"  大盘(>5000w): {len(large)} ({len(large)/len(v13_pool)*100:.0f}%)")

# v20 流动性范围
v20_mask = (day_amount > V20Config.min_liquidity) & (day_amount < V20Config.max_liquidity)
v20_pool = day_amount[v20_mask].sort_values()
print(f"\nv20 流动性范围: {V20Config.min_liquidity}w-{V20Config.max_liquidity}w")
print(f"v20 候选池: {len(v20_pool)} 只")
if len(v20_pool) > 0:
    print(f"  最小: {v20_pool.min():.0f}w | 最大: {v20_pool.max():.0f}w | 中位: {v20_pool.median():.0f}w")
    small2 = v20_pool[v20_pool < 2000]
    mid2 = v20_pool[(v20_pool >= 2000) & (v20_pool < 5000)]
    large2 = v20_pool[v20_pool >= 5000]
    print(f"  小盘(<2000w): {len(small2)} ({len(small2)/len(v20_pool)*100:.0f}%)")
    print(f"  中盘(2000-5000w): {len(mid2)} ({len(mid2)/len(v20_pool)*100:.0f}%)")
    print(f"  大盘(>5000w): {len(large2)} ({len(large2)/len(v20_pool)*100:.0f}%)")
