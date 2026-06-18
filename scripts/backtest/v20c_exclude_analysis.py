#!/usr/bin/env python3
"""
v20c 选股排除分析
=================
分析每个维度排除了多少股票，找出选股率低的根因。
"""
import sys, os, numpy as np, pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.db import load_panel_from_db
from scripts.strategies.v20_tail_pick import V20Config, calc_tail_pick_factors

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"))

# 加载最近 60 天数据（足够计算所有因子）
panels, codes = load_panel_from_db(need_hl=True)
close_panel = panels[0]
volume_panel = panels[1]
amount_panel = panels[2]
high_panel = panels[3]
low_panel = panels[4]

# 计算因子
factors = calc_tail_pick_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)

# 取最近一个交易日
date = close_panel.index[-1]
print(f"分析日期: {date.date()}")
print(f"总股票数: {close_panel.shape[1]}")
print()

# 流动性筛选
avg_amount = amount_panel.rolling(20).mean() / 1e4
day_amount = avg_amount.loc[date]
liquid_mask = (day_amount > V20Config.min_liquidity) & (day_amount < V20Config.max_liquidity)
liquid_stocks = set(day_amount[liquid_mask].dropna().index)
print(f"流动性筛选后: {len(liquid_stocks)} 只")
print(f"  排除: {close_panel.shape[1] - len(liquid_stocks)} 只（成交额不在 300万-1亿 范围）")
print()

# 获取当日因子
vol_ratio = factors['vol_ratio'].loc[date].dropna()
range_ratio = factors['range_ratio'].loc[date].dropna()
amount_ratio = factors['amount_ratio'].loc[date].dropna()
price_vs_ma5 = factors['price_vs_ma5'].loc[date].dropna()
recent_limit_up = factors['recent_limit_up'].loc[date].dropna()

# 统计各维度排除情况
stats = {
    'vol_hard_exclude': 0,      # vr > 1.5 硬性排除
    'vol_no_score': 0,          # vr > 1.0 不得分
    'range_no_score': 0,        # rr > 1.0 不得分
    'amount_no_score': 0,       # ar 不在 0.5-3.0 范围
    'pm_no_score': 0,           # pm < 0.98 不得分
    'lu_no_score': 0,           # lu = 0 无加分
    'score_zero': 0,            # 最终 score = 0
    'score_positive': 0,        # score > 0 入选
}

score_details = []
for code in liquid_stocks:
    if code not in vol_ratio.index:
        continue

    vr = vol_ratio.get(code, 999)
    rr = range_ratio.get(code, 999)
    ar = amount_ratio.get(code, 0)
    pm = price_vs_ma5.get(code, 0)
    lu = recent_limit_up.get(code, 0)

    # 硬性排除
    if vr > V20Config.vol_vs_avg_max * 1.5:
        stats['vol_hard_exclude'] += 1
        continue
    if ar < V20Config.amount_vs_avg_min * 0.3:
        stats['amount_no_score'] += 1
        continue

    # 各维度得分
    vol_score = max(0, 3.0 - vr * 2.0) if vr < 1.5 else 0
    range_score = (1.0 - rr) * 2.0 if rr < 1.0 else (max(0, (1.2 - rr) / 0.2 * 0.5) if rr < 1.2 else 0)
    amount_score = max(0, 1.0 - abs(ar - 1.0) * 0.8) if 0.15 < ar < 3.0 else 0
    pm_score = min((pm - 1.0) * 5.0, 1.0) if pm > 1.0 else (0.2 if pm > 0.98 else 0)
    lu_score = 0.8 if lu > 0 else 0

    total = vol_score + range_score + amount_score + pm_score + lu_score

    if vol_score == 0:
        stats['vol_no_score'] += 1
    if range_score == 0:
        stats['range_no_score'] += 1
    if amount_score == 0:
        stats['amount_no_score'] += 1
    if pm_score == 0:
        stats['pm_no_score'] += 1
    if lu_score == 0:
        stats['lu_no_score'] += 1

    if total > 0:
        stats['score_positive'] += 1
    else:
        stats['score_zero'] += 1

    score_details.append({
        'code': code,
        'vol': vr, 'vol_score': vol_score,
        'range': rr, 'range_score': range_score,
        'amount': ar, 'amount_score': amount_score,
        'pm': pm, 'pm_score': pm_score,
        'lu': lu, 'lu_score': lu_score,
        'total': total,
    })

print("各维度排除统计（在流动性筛选后的股票中）：")
print(f"  硬性排除（vol > 1.5）:     {stats['vol_hard_exclude']} 只")
print(f"  vol 不得分（> 1.0）:       {stats['vol_no_score']} 只")
print(f"  range 不得分（> 1.0）:     {stats['range_no_score']} 只")
print(f"  amount 不得分:             {stats['amount_no_score']} 只")
print(f"  pm 不得分（< 0.98）:       {stats['pm_no_score']} 只")
print(f"  lu 无加分（无涨停史）:     {stats['lu_no_score']} 只")
print()
print(f"  最终 score > 0（入选）:    {stats['score_positive']} 只")
print(f"  最终 score = 0（排除）:    {stats['score_zero']} 只")
print(f"  选股率: {stats['score_positive']}/{len(liquid_stocks)} = {stats['score_positive']/max(len(liquid_stocks),1):.1%}")
print()

# 分析 score=0 的原因
print("score=0 的股票各维度得分情况（前 20 只）：")
zero_scores = [d for d in score_details if d['total'] == 0]
for d in zero_scores[:20]:
    print(f"  {d['code']}: vol={d['vol']:.2f}({d['vol_score']:.1f}) "
          f"range={d['range']:.2f}({d['range_score']:.1f}) "
          f"amount={d['amount']:.2f}({d['amount_score']:.1f}) "
          f"pm={d['pm']:.3f}({d['pm_score']:.1f}) "
          f"lu={d['lu']:.0f}({d['lu_score']:.1f})")

print()
print("入选股票评分（按总分降序）：")
positive_scores = sorted([d for d in score_details if d['total'] > 0], key=lambda x: x['total'], reverse=True)
for d in positive_scores:
    print(f"  {d['code']}: total={d['total']:.2f} vol={d['vol']:.2f}({d['vol_score']:.1f}) "
          f"range={d['range']:.2f}({d['range_score']:.1f}) "
          f"amount={d['amount']:.2f}({d['amount_score']:.1f}) "
          f"pm={d['pm']:.3f}({d['pm_score']:.1f}) "
          f"lu={d['lu']:.0f}({d['lu_score']:.1f})")
