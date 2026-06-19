#!/usr/bin/env python3
"""
v20c 流动性筛选专项分析
========================
分析不同流动性阈值的排除效果，找出最优范围。
"""
import sys, os, numpy as np, pandas as pd

from core.db import load_panel_from_db

panels, codes = load_panel_from_db(need_hl=True)
amount_panel = panels[2]

# 最新日期的 20 日均额
avg_amount = amount_panel.rolling(20).mean()
latest = avg_amount.iloc[-1].dropna()  # 单位：元

print(f"分析日期: {avg_amount.index[-1].date()}")
print(f"总股票数: {len(latest)}")
print()

# 当前配置
cur_min = 300e4  # 300万
cur_max = 10000e4  # 1亿
cur_pass = latest[(latest > cur_min) & (latest < cur_max)]
print(f"当前配置 ({cur_min/1e4:.0f}万 ~ {cur_max/1e4:.0f}万):")
print(f"  通过: {len(cur_pass)} 只 ({len(cur_pass)/len(latest):.1%})")
print(f"  排除: {len(latest) - len(cur_pass)} 只")
print()

# 分位数分布
print("成交额分位数分布（万元）：")
for p in [5, 10, 20, 25, 30, 40, 50, 60, 70, 75, 80, 90, 95]:
    v = np.percentile(latest, p) / 1e4
    print(f"  P{p:2d}: {v:8.0f}万")

print()

# 不同阈值组合的效果
thresholds = [
    (100e4, 20000e4, "100万~2亿"),
    (200e4, 15000e4, "200万~1.5亿"),
    (300e4, 10000e4, "300万~1亿（当前）"),
    (300e4, 20000e4, "300万~2亿"),
    (500e4, 10000e4, "500万~1亿"),
    (500e4, 20000e4, "500万~2亿"),
    (1000e4, 10000e4, "1000万~1亿"),
    (1000e4, 20000e4, "1000万~2亿"),
]

print("不同阈值组合的通过率：")
for lo, hi, desc in thresholds:
    Pass = latest[(latest > lo) & (latest < hi)]
    exclude_low = (latest <= lo).sum()
    exclude_high = (latest >= hi).sum()
    print(f"  {desc:20s}: 通过 {len(Pass):3d} 只 ({len(Pass)/len(latest):5.1%}) "
          f"| 过低排除 {exclude_low:3d} | 过高排除 {exclude_high:3d}")

print()

# 当前被排除的股票示例（成交额最低和最高各 10 只）
print("当前被排除的股票（成交额最低 10 只，万元）：")
excluded_low = latest[latest <= cur_min].nsmallest(10)
for code, amt in excluded_low.items():
    print(f"  {code}: {amt/1e4:8.0f}万")

print()
print("当前被排除的股票（成交额最高 10 只，万元）：")
excluded_high = latest[latest >= cur_max].nlargest(10)
for code, amt in excluded_high.items():
    print(f"  {code}: {amt/1e4:8.0f}万")

print()

# 模拟盘实际选股情况：看看最近 5 天每天流动性筛选后有多少候选
print("最近 5 天流动性筛选通过数量：")
for i in range(-5, 0):
    day_amount = avg_amount.iloc[i]
    day_str = str(avg_amount.index[i].date())
    n_pass = ((day_amount > cur_min) & (day_amount < cur_max)).sum()
    n_total = day_amount.notna().sum()
    print(f"  {day_str}: {n_pass}/{n_total} 只 ({n_pass/n_total:.1%})")
