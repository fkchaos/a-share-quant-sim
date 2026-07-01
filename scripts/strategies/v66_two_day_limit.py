#!/usr/bin/env python3
"""
v66_two_day_limit.py — 连续两天涨停情绪因子策略
====================================================
基于v39g，加入连续两天涨停作为加分因子

核心逻辑：
1. 保持v39g的选股和评分逻辑
2. 新增"连续两天涨停"作为加分因子（W_TWO_DAY_LIMIT）
3. 连续两天涨停的股票获得额外加分

来源：v39g + 连续两天涨停情绪因子
"""

import pandas as pd
import numpy as np
from scripts.strategies.v39g_optimized import calc_factors, _score_column, DEFAULT_PARAMS as V39G_PARAMS

# 在v39g参数基础上新增连续两天涨停权重
DEFAULT_PARAMS = {
    **V39G_PARAMS,
    "W_TWO_DAY_LIMIT": 0.35,  # 连续两天涨停权重
}

# 重新分配权重，总和保持1.0
DEFAULT_PARAMS["W_MOM"] = 0.08
DEFAULT_PARAMS["W_PV_CORR"] = 0.04
DEFAULT_PARAMS["W_TURNOVER"] = 0.04
DEFAULT_PARAMS["W_SIZE"] = 0.35
DEFAULT_PARAMS["W_FUND_FLOW"] = 0.04
DEFAULT_PARAMS["W_GAP"] = 0.04
DEFAULT_PARAMS["W_ILLIQ"] = 0.16
DEFAULT_PARAMS["W_TWO_DAY_LIMIT"] = 0.35  # 新增


def calc_factors_v66(close_panel, volume_panel, amount_panel, 
                     high_panel=None, low_panel=None, open_panel=None,
                     extra_data=None):
    """计算v66因子 = v39g因子 + 连续两天涨停因子"""
    
    # 先计算v39g的所有因子（包含two_day_limit）
    factors = calc_factors(close_panel, volume_panel, amount_panel,
                          high_panel, low_panel, open_panel, extra_data)
    
    return factors


def select_stocks_v66(factors, date, current_holdings=None, params=None,
                      sold_recently=None):
    """v66选股 = v39g选股 + 连续两天涨停加分"""
    p = {**DEFAULT_PARAMS, **(params or {})}
    
    if date not in factors['mom_5'].index:
        return []
    
    m5 = factors['mom_5'].loc[date].dropna()
    candidates = list(m5.index)
    
    # v39g的筛选条件
    candidates = [c for c in candidates if m5[c] > p["MOM_THRESHOLD"]]
    if date in factors['pv_corr_10'].index:
        pv10 = factors['pv_corr_10'].loc[date]
        candidates = [c for c in candidates if c in pv10.index and pv10[c] >= p["PV_CORR_10_MIN"]]
    if date in factors['dr_threshold'].index and date in factors['delist_risk'].index:
        dr_t = factors['dr_threshold'].loc[date]
        candidates = [c for c in candidates
                      if c not in factors['delist_risk'].columns
                      or factors['delist_risk'].loc[date, c] <= dr_t]
    if current_holdings:
        candidates = [c for c in candidates if c not in current_holdings]
    if sold_recently:
        candidates = [c for c in candidates if c not in sold_recently]
    if not candidates:
        return []
    
    # 评分
    scores = pd.Series(0.0, index=candidates)
    
    # v39g的评分因子
    for fname, wkey in [('mom_5', 'W_MOM'), ('pv_corr_20', 'W_PV_CORR'),
                         ('size_factor', 'W_SIZE'), ('illiq', 'W_ILLIQ')]:
        if p.get(wkey, 0) > 0:
            f_scores = _score_column(factors, date, fname)
            scores += f_scores.reindex(candidates).fillna(0) * p[wkey]
    
    if p.get("W_TURNOVER", 0) > 0:
        to_scores = _score_column(factors, date, 'turnover_avg', clip_min=0, clip_max=0.05)
        scores += to_scores.reindex(candidates).fillna(0) * p["W_TURNOVER"]
    if p.get("W_GAP", 0) > 0:
        gap_scores = _score_column(factors, date, 'gap_ratio', clip_min=0, clip_max=0.05)
        scores += gap_scores.reindex(candidates).fillna(0) * p["W_GAP"]
    if p.get("W_FUND_FLOW", 0) > 0:
        ff_scores = _score_column(factors, date, 'fund_flow', clip_min=0.5, clip_max=3.0)
        scores += ff_scores.reindex(candidates).fillna(0) * p["W_FUND_FLOW"]
    
    # 新增：连续两天涨停加分
    if p.get("W_TWO_DAY_LIMIT", 0) > 0 and 'two_day_limit' in factors:
        tdl = factors['two_day_limit'].loc[date] if date in factors['two_day_limit'].index else pd.Series(0, index=candidates)
        scores += tdl.reindex(candidates).fillna(0) * p["W_TWO_DAY_LIMIT"]
    
    # 排序选择
    scores = scores.sort_values(ascending=False)
    selected = scores.index[:p["MAX_DAILY_BUY"]]
    return [(code, scores[code]) for code in selected]


if __name__ == '__main__':
    print("v66 连续两天涨停情绪因子策略")
    print(f"默认参数: {DEFAULT_PARAMS}")
