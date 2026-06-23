#!/usr/bin/env python3
"""
scripts/strategies/v39d_optimized.py — v39d 优化版
====================================================
基于 v39c 因子 IC 分析（2023-2025）优化参数：

因子权重调整：
- W_MOM: 0.20 → 0.15（IC 为负，降低）
- W_TURNOVER: 0.10 → 0.05（IC 为负，降低）
- W_SIZE: 0.10 → 0.30（最强因子，大幅增加）
- W_FUND_FLOW: 0.15 → 0.05（几乎无效，降低）
- W_GAP: 0.10 → 0.05（负 IC，降低）
- W_ILLIQ: 0.10 → 0.20（有效因子，增加）
- W_PV_CORR: 0.05 → 0.05（不变）

风控参数调整：
- STOP_LOSS: -0.015 → -0.05（放宽，避免被震出）
- TAKE_PROFIT: 0.03 → 0.10（提高，改善盈亏比）
- MAX_DAILY_BUY: 4 → 3（降低换手率）

v39e 进一步优化（基于 v39d 交易行为分析）：
- STOP_LOSS: -0.05 → -0.03（收紧，减少单笔亏损）
- HOLD_DAYS_EXTEND: 5 → 3（缩短延长持有期）
- HOLD_DAYS_EXTEND_PNL: 0.03 → 0.05（提高延长门槛）
- MAX_POSITION: 0.20 → 0.15（降低单股仓位，减少回撤）
- W_MOM: 0.15 → 0.10（IC 为负，继续降低）
- W_SIZE: 0.30 → 0.40（最强因子，继续增加）
"""
import pandas as pd
import numpy as np
from scripts.strategies.v39c_pv_resonance import calc_factors, _score_column

DEFAULT_PARAMS = {
    # ── 风控参数（v39e 优化）──
    "STOP_LOSS": -0.03,           # v39d -0.05 → -0.03（收紧，减少单笔亏损）
    "TAKE_PROFIT": 0.10,          # 保持 0.10
    "HOLD_DAYS_MAX": 5,           # 保持 5
    "HOLD_DAYS_EXTEND": 3,        # v39d 5 → 3（缩短延长持有期，减少被动超时）
    "HOLD_DAYS_EXTEND_PNL": 0.05, # v39d 0.03 → 0.05（提高延长门槛）
    "MAX_DAILY_BUY": 3,           # 保持 3
    "MAX_POSITION": 0.15,         # v39d 0.20 → 0.15（降低单股仓位，减少回撤）
    "MAX_HOLDINGS": 8,
    "COOLDOWN_DAYS": 0,
    "MAX_SAME_PREFIX": 0,

    # ── 选股门槛（不变）──
    "MOM_THRESHOLD": 0.03,
    "PV_CORR_10_MIN": -0.5,
    "PV_CORR_20_MIN": 0.0,
    "BOLL_W_MIN": 0.0,

    # ── 评分权重（v39e 优化）──
    "W_MOM": 0.10,          # v39d 0.15 → 0.10（IC 为负，继续降低）
    "W_PV_CORR": 0.05,      # 不变
    "W_TURNOVER": 0.05,     # 不变
    "W_SIZE": 0.40,         # v39d 0.30 → 0.40（最强因子，继续增加）
    "W_FUND_FLOW": 0.05,    # 不变
    "W_GAP": 0.05,          # 不变
    "W_ILLIQ": 0.20,        # 不变
}


def select_stocks_v39d(factors, date, current_holdings=None, params=None,
                       sold_recently=None):
    """
    v39d 选股：v39c 门槛 + 优化权重
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    if date not in factors['mom_5'].index:
        return []

    m5 = factors['mom_5'].loc[date].dropna()
    candidates = list(m5.index)

    # 硬筛选（与 v39c 完全一致）
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

    # 多因子评分（优化权重）
    scores = pd.Series(0.0, index=candidates)

    # 无 clip 的因子
    for fname, wkey in [('mom_5', 'W_MOM'), ('pv_corr_20', 'W_PV_CORR'),
                         ('size_factor', 'W_SIZE'), ('illiq', 'W_ILLIQ')]:
        if wkey not in p or p[wkey] == 0:
            continue
        f_scores = _score_column(factors, date, fname)
        scores += f_scores.reindex(candidates).fillna(0) * p[wkey]

    # 换手率（clip 到 [0, 0.05]）
    if 'turnover_avg' in factors and p.get("W_TURNOVER", 0) > 0:
        to_scores = _score_column(factors, date, 'turnover_avg', clip_min=0, clip_max=0.05)
        scores += to_scores.reindex(candidates).fillna(0) * p["W_TURNOVER"]

    # gap_ratio（clip 到 [0, 0.05]）
    if 'gap_ratio' in factors and p.get("W_GAP", 0) > 0:
        gap_scores = _score_column(factors, date, 'gap_ratio', clip_min=0, clip_max=0.05)
        scores += gap_scores.reindex(candidates).fillna(0) * p["W_GAP"]

    # fund_flow（clip 到 [0.5, 3.0]）
    if 'fund_flow' in factors and p.get("W_FUND_FLOW", 0) > 0:
        ff_scores = _score_column(factors, date, 'fund_flow', clip_min=0.5, clip_max=3.0)
        scores += ff_scores.reindex(candidates).fillna(0) * p["W_FUND_FLOW"]

    scores = scores.sort_values(ascending=False)
    selected = scores.index[:p["MAX_DAILY_BUY"]]

    return [(code, scores[code]) for code in selected]
