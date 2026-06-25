#!/usr/bin/env python3
"""
scripts/strategies/v42_turnover_research.py — v42 换手率因子研究
====================================================
在 v39i 框架基础上，加入真实换手率因子（volume / float_shares），
与量比（turnover_avg = volume / (amount/close)）做对比研究。

研究目标：
1. 换手率 vs 量比 哪个对选股更有效？
2. 换手率和动量是否独立（低相关）？
3. 换手率因子的最优权重是多少？

因子定义：
- mom_5: 5日动量（已有）
- turnover_rate: 真实换手率 = volume / float_shares（新增）
- turnover_avg: 量比 = volume / (amount/close) 5日均值（已有，对比用）

选股逻辑与 v39i 一致，只是评分时加入换手率因子。
"""
import pandas as pd
import numpy as np
from scripts.strategies.v39c_pv_resonance import calc_factors as _calc_factors_base, _score_column


def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None, params=None):
    """
    v42 因子计算：继承 v39c 所有因子 + 新增真实换手率 (volume / float_shares)
    
    真实换手率需要从 stock_pool 表获取 float_shares，通过 params 传入。
    params 中需包含 'float_shares_map': {code: float_shares}
    """
    factors = _calc_factors_base(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel, params)

    # 真实换手率 = volume / float_shares
    float_shares_map = (params or {}).get('float_shares_map', {})
    if float_shares_map:
        eps = 1e-10
        # 构建 float_shares 面板
        fs_panel = pd.DataFrame(
            {code: float_shares_map.get(code, np.nan) for code in close_panel.columns},
            index=close_panel.index,
        )
        factors['turnover_rate'] = volume_panel / (fs_panel + eps)

    return factors

DEFAULT_PARAMS = {
    # ── 风控参数（与 v39i 一致）──
    "STOP_LOSS": -0.05,
    "TAKE_PROFIT": 0.10,
    "HOLD_DAYS_MAX": 5,
    "HOLD_DAYS_EXTEND": 5,
    "HOLD_DAYS_EXTEND_PNL": 0.03,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.125,
    "MAX_HOLDINGS": 8,
    "COOLDOWN_DAYS": 0,
    "MAX_SAME_PREFIX": 0,

    # ── 选股门槛（与 v39i 一致）──
    "MOM_THRESHOLD": 0.05,
    "MOM_THRESHOLD_BEAR": 0.08,
    "PV_CORR_10_MIN": -0.5,
    "PV_CORR_20_MIN": 0.0,
    "BOLL_W_MIN": 0.0,

    # ── 评分权重（研究：换手率 vs 量比）──
    "W_MOM": 0.08,
    "W_PV_CORR": 0.05,
    "W_TURNOVER_RATE": 0.12,     # 真实换手率（新因子）
    "W_TURNOVER_AVG": 0.0,       # 量比（去掉）
    "W_SIZE": 0.30,
    "W_FUND_FLOW": 0.05,
    "W_GAP": 0.05,
    "W_ILLIQ": 0.20,
}


def _get_mom_threshold(factors, date, params):
    """动态 MOM_THRESHOLD：基于全市场 mom_5 中位数判断市场状态"""
    if date not in factors['mom_5'].index:
        return params["MOM_THRESHOLD"]

    m5 = factors['mom_5'].loc[date].dropna()
    if len(m5) == 0:
        return params["MOM_THRESHOLD"]

    median_mom = m5.median()
    if median_mom > 0:
        return params["MOM_THRESHOLD"]
    else:
        return params["MOM_THRESHOLD_BEAR"]


def select_stocks_v42(factors, date, current_holdings=None, params=None,
                      sold_recently=None):
    """v42 选股：加入换手率因子，与量比对比"""
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

    # 量比（turnout_avg = volume / (amount/close)）
    if p.get("W_TURNOVER_AVG", 0) > 0:
        to_scores = _score_column(factors, date, 'turnover_avg', clip_min=0, clip_max=0.05)
        scores += to_scores.reindex(candidates).fillna(0) * p["W_TURNOVER_AVG"]

    # 真实换手率（turnover_rate = volume / float_shares）
    if p.get("W_TURNOVER_RATE", 0) > 0 and 'turnover_rate' in factors:
        tr_scores = _score_column(factors, date, 'turnover_rate', clip_min=0, clip_max=0.3)
        scores += tr_scores.reindex(candidates).fillna(0) * p["W_TURNOVER_RATE"]

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
