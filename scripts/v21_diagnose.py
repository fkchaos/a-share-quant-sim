#!/usr/bin/env python3
"""v21 诊断：分析行业轮动 + 反转因子冲突"""
import sys, os
import numpy as np
import pandas as pd

sys.path.insert(0, '.')
from core.db import load_panel_from_db, load_industry_map

tpl, codes = load_panel_from_db('2023-01-01', '2026-05-31', need_open=True, need_hl=True)
close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
high_panel, low_panel = tpl[4], tpl[5]
industry_map = load_industry_map()

# 获取最新日期的行业轮动信息
date = close_panel.index[-1]
valid = [c for c in close_panel.columns if c in industry_map]
mom_20 = close_panel[valid].pct_change(20).loc[date].dropna()
mom_5 = close_panel[valid].pct_change(5).loc[date].dropna()

ind_series = pd.Series({c: industry_map[c] for c in valid})

# 行业动量排名
ind_mom = {}
for ind in ind_series.unique():
    codes_in = ind_series[ind_series == ind].index.tolist()
    codes_in = [c for c in codes_in if c in mom_20.index]
    if len(codes_in) >= 3:
        ind_mom[ind] = mom_20[codes_in].mean()

print("="*60)
print("诊断1: 行业轮动选出的 top 行业中，股票的跌幅分布")
print("="*60)

# v21 选出 top 5 行业
top_inds = sorted(ind_mom, key=ind_mom.get, reverse=True)[:5]
print(f"Top 5 行业(动量): {top_inds}")

# 这些行业内，有多少满足 v13 反转条件（跌幅>2%）？
rev_5 = close_panel[valid].pct_change(5).loc[date].dropna()
top_codes = []
for ind in top_inds:
    top_codes.extend(ind_series[ind_series == ind].index.tolist())
top_codes = [c for c in top_codes if c in rev_5.index]

declining = sum(1 for c in top_codes if rev_5.get(c, 0) < -0.02)
print(f"\nTop 行业股票数: {len(top_codes)}")
print(f"其中跌幅>2%: {declining} ({declining/len(top_codes)*100:.1f}%)")

# 全市场对比
all_declining = sum(1 for c in valid if rev_5.get(c, 0) < -0.02)
print(f"\n全市场: {len(valid)} 只, 跌幅>2%: {all_declining} ({all_declining/len(valid)*100:.1f}%)")

print("\n结论: 行业轮动选出的 top 动量行业中，跌够2%的股票更少 → 反转因子失效")

print("\n" + "="*60)
print("诊断2: 改进方案对比")
print("="*60)

# 方案A: 行业轮动选出 top 行业内，降低反转阈值
print("\n方案A: top 行业内，反转阈值从-2%降到-1%")
a_declining = sum(1 for c in top_codes if rev_5.get(c, 0) < -0.01)
print(f"  满足条件的: {a_declining}/{len(top_codes)} ({a_declining/len(top_codes)*100:.1f}%)")

# 方案B: 行业内相对反转（行业内跌幅排名）
print("\n方案B: 行业内相对反转（行业内跌幅前30%）")
for ind in top_inds[:3]:
    codes_in = [c for c in ind_series[ind_series == ind].index.tolist() if c in rev_5.index]
    if len(codes_in) > 0:
        thresh = rev_5[codes_in].quantile(0.3)
        n_match = sum(1 for c in codes_in if rev_5.get(c, 0) <= thresh)
        print(f"  {ind}: {len(codes_in)}只, 30%分位={thresh*100:.1f}%, 满足{n_match}只")

# 方案C: 行业动量 + 行业内 alpha（不依赖绝对跌幅）
print("\n方案C: 只看行业动量排名，不要求绝对跌幅")
sorted_inds = sorted(ind_mom.items(), key=lambda x: x[1], reverse=True)
print("  行业动量排名:")
for i, (ind, v) in enumerate(sorted_inds[:10]):
    codes_in = ind_series[ind_series == ind].index.tolist()
    n_up = sum(1 for c in codes_in if c in mom_20.index and mom_20[c] > 0)
    print(f"    {i+1}. {ind}: {v*100:.1f}% (上涨{n_up}/{len(codes_in)})")

print("\n建议:")
print("  行业轮动+反转因子本质冲突:")
print("  - 行业动量选上涨行业 → 里面大多数股票在涨")
print("  - v13 反转因子选下跌股票 → 要求在跌")
print("  - 交集很小 → 偶尔选到也是极端值 → 高止损率")
print()
print("  解决方案:")
print("  1. 行业轮动只作协熊过滤：排除最差行业，全市场选股")
print("  2. 行业内相对排名：不看绝对跌幅，看行业内跌幅排名")
print("  3. 行业轮动+动量因子：行业动量+个股动量（放弃反转）")
