#!/usr/bin/env python3
"""
scripts/strategies/v39f_optimized.py — v39f 优化版
====================================================
基于 v39e 交易行为分析修正优化方向：

v39e 问题：止损从 -5% 收紧到 -3% 导致被反复震出，止盈占比下降

v39f 修正：
- STOP_LOSS: -0.03 → -0.05（保持 v39d 的 -5%，不要收紧）
- TAKE_PROFIT: 0.10 → 0.05（降低止盈，更容易触发）
- HOLD_DAYS_EXTEND: 3 → 5（保持 v39d 的 5，不要缩短）
- HOLD_DAYS_EXTEND_PNL: 0.05 → 0.03（降低延长门槛）
- MAX_DAILY_BUY: 3 → 4（增加买入机会）
- MAX_POSITION: 0.15 → 0.20（保持 v39d 的 20%）
- W_MOM: 0.10（保持 v39e 的 0.10）
- W_SIZE: 0.40（保持 v39e 的 0.40）
"""
import pandas as pd
import numpy as np
from scripts.strategies.v39c_pv_resonance import calc_factors, _score_column

DEFAULT_PARAMS = {
    # ── 风控参数（v39f 修正）──
    "STOP_LOSS": -0.05,           # 保持 v39d 的 -0.05（不要收紧）
    "TAKE_PROFIT": 0.05,          # v39d 0.10 → 0.05（降低止盈，更容易触发）
    "HOLD_DAYS_MAX": 5,           # 保持 5
    "HOLD_DAYS_EXTEND": 5,        # 保持 v39d 的 5（不要缩短）
    "HOLD_DAYS_EXTEND_PNL": 0.03, # 保持 v39d 的 0.03
    "MAX_DAILY_BUY": 4,           # v39d 3 → 4（增加买入机会）
    "MAX_POSITION": 0.20,         # 保持 v39d 的 0.20
    "MAX_HOLDINGS": 8,
    "COOLDOWN_DAYS": 0,
    "MAX_SAME_PREFIX": 0,

    # ── 选股门槛──
    "MOM_THRESHOLD": 0.03,
    "PV_CORR_10_MIN": -0.5,
    "PV_CORR_20_MIN": 0.0,
    "BOLL_W_MIN": 0.0,

    # ── 评分权重（v39f = v39e 权重）──
    "W_MOM": 0.10,
    "W_PV_CORR": 0.05,
    "W_TURNOVER": 0.05,
    "W_SIZE": 0.40,
    "W_FUND_FLOW": 0.05,
    "W_GAP": 0.05,
    "W_ILLIQ": 0.20,
}


def select_stocks_v39f(factors, date, current_holdings=None, params=None,
                       sold_recently=None):
    """v39f 选股"""
    p = {**DEFAULT_PARAMS, **(params or {})}

    if date not in factors['mom_5'].index:
        return []

    m5 = factors['mom_5'].loc[date].dropna()
    candidates = list(m5.index)

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

    scores = pd.Series(0.0, index=candidates)

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

    scores = scores.sort_values(ascending=False)
    selected = scores.index[:p["MAX_DAILY_BUY"]]
    return [(code, scores[code]) for code in selected]
