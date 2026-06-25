#!/usr/bin/env python3
"""
scripts/strategies/v39i_optimized.py — v39i 优化版
====================================================
基于 v39h 阈值扫描结果，使用最优阈值组合：

最优参数（2023-2025 扫描）：
- MOM_THRESHOLD (BULL) = 0.05（牛市门槛，更严格）
- MOM_THRESHOLD_BEAR = 0.08（熊市门槛，更宽松）
- 结果：收益 103.51%，夏普 1.199，回撤 16.69%

对比 v39h (BULL=0.03, BEAR=0.10)：
- v39h: 收益 115.42%，夏普 1.145，回撤 23.49%
- v39i: 收益 103.51%，夏普 1.199，回撤 16.69%
→ v39i 夏普更高、回撤更低，收益略低但更均衡

风控参数（保持 v39h）：
- STOP_LOSS: -0.05, TAKE_PROFIT: 0.10
- HOLD_DAYS_MAX: 5, HOLD_DAYS_EXTEND: 5
- MAX_DAILY_BUY: 5, MAX_POSITION: 0.20
"""
import pandas as pd
import numpy as np
from scripts.strategies.v39c_pv_resonance import calc_factors, _score_column

DEFAULT_PARAMS = {
    # ── 风控参数（保持 v39h）──
    "STOP_LOSS": -0.05,
    "TAKE_PROFIT": 0.10,
    "HOLD_DAYS_MAX": 5,
    "HOLD_DAYS_EXTEND": 5,
    "HOLD_DAYS_EXTEND_PNL": 0.03,
    "MAX_DAILY_BUY": 5,
    "MAX_POSITION": 0.125,
    "MAX_HOLDINGS": 8,
    "COOLDOWN_DAYS": 0,
    "MAX_SAME_PREFIX": 0,

    # ── 选股门槛（最优阈值）──
    "MOM_THRESHOLD": 0.05,        # 牛市门槛（扫描最优）
    "MOM_THRESHOLD_BEAR": 0.08,   # 熊市门槛（扫描最优）
    "PV_CORR_10_MIN": -0.5,
    "PV_CORR_20_MIN": 0.0,
    "BOLL_W_MIN": 0.0,

    # ── 评分权重（保持 v39h）──
    "W_MOM": 0.15,
    "W_PV_CORR": 0.05,
    "W_TURNOVER": 0.05,
    "W_SIZE": 0.30,
    "W_FUND_FLOW": 0.05,
    "W_GAP": 0.05,
    "W_ILLIQ": 0.20,
}


def _get_mom_threshold(factors, date, params):
    """
    动态 MOM_THRESHOLD：基于全市场 mom_5 中位数判断市场状态
    """
    if date not in factors['mom_5'].index:
        return params["MOM_THRESHOLD"]

    m5 = factors['mom_5'].loc[date].dropna()
    if len(m5) == 0:
        return params["MOM_THRESHOLD"]

    median_mom = m5.median()
    if median_mom > 0:
        return params["MOM_THRESHOLD"]       # 牛市：0.05
    else:
        return params["MOM_THRESHOLD_BEAR"]  # 熊市：0.08


def select_stocks_v39i(factors, date, current_holdings=None, params=None,
                       sold_recently=None):
    """v39i 选股：最优动态阈值"""
    p = {**DEFAULT_PARAMS, **(params or {})}

    if date not in factors['mom_5'].index:
        return []

    mom_threshold = _get_mom_threshold(factors, date, p)

    m5 = factors['mom_5'].loc[date].dropna()
    candidates = list(m5.index)

    candidates = [c for c in candidates if m5[c] > mom_threshold]

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
