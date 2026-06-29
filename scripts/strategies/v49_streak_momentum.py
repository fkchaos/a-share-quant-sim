#!/usr/bin/env python3
"""
scripts/strategies/v49_streak_momentum.py — v49 连板动量策略
============================================================
核心思路：
  不追买正在连板的股票（打板策略），而是筛选"有连板记忆 + 当前动量确认"的股票。
  连板辨识度因子识别历史上出现过连板的股票（连板记忆），
  配合动量因子确认当前有上涨动能但尚未进入连板状态。

与 v39i 的区别：
  - v39i：纯动量+多因子评分，不区分连板记忆
  - v49：以连板辨识度为核心筛选条件（前30%），再用动量+质量因子评分

与 v46a 的区别：
  - v46a：行业动量过滤（已证伪，负增量）
  - v49：连板记忆过滤（独立于行业，纯价量信号）

选股门槛：
  - 连板辨识度 > 0（有连板记忆）
  - mom_5 > 3%（有动量）
  - 非当前连板中（streak_risk = 0）
  - 非ST/*ST/退市

风控：
  - STOP_LOSS: -5%, TAKE_PROFIT: +10%
  - HOLD_DAYS_MAX: 5, 浮盈≥3%延长到10天
  - MAX_POSITION: 12.5%, MAX_DAILY_BUY: 5
"""
import pandas as pd
import numpy as np
from scripts.strategies.v39c_pv_resonance import calc_factors, _score_column
from core.streak_factor import compute_streak_factor, compute_streak_risk


DEFAULT_PARAMS = {
    # ── 风控参数 ──
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

    # ── 连板因子参数 ──
    "STREAK_DECAY_DAYS": 252,      # 衰减窗口（1年）
    "STREAK_PCTILE": 70,           # 连板辨识度分位数门槛（前30%）

    # ── 选股门槛 ──
    "MOM_THRESHOLD": 0.03,         # 最低5日动量 3%
    "MOM_THRESHOLD_BEAR": 0.05,    # 熊市门槛更高

    # ── 评分权重 ──
    "W_STREAK": 0.20,              # 连板辨识度权重
    "W_MOM": 0.25,                 # 动量权重
    "W_PV_CORR": 0.05,             # 量价共振
    "W_SIZE": 0.20,                # 规模因子
    "W_FUND_FLOW": 0.05,           # 资金流
    "W_GAP": 0.05,                 # 跳空
    "W_ILLIQ": 0.20,               # 非流动性
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
        return params["MOM_THRESHOLD"]       # 牛市：3%
    else:
        return params["MOM_THRESHOLD_BEAR"]  # 熊市：5%


def select_stocks_v49(factors, date, current_holdings=None, params=None,
                      sold_recently=None, panels=None):
    """
    v49 连板动量选股：
    1. 计算连板辨识度因子（streak_factor）
    2. 筛选连板辨识度前30% + mom_5 > 阈值 + 非当前连板中
    3. 多因子评分排序
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    if date not in factors['mom_5'].index:
        return []

    mom_threshold = _get_mom_threshold(factors, date, p)

    # ── Step 1: 动量初筛 ──
    m5 = factors['mom_5'].loc[date].dropna()
    candidates = [c for c in m5.index if m5[c] > mom_threshold]

    if not candidates:
        return []

    # ── Step 2: 连板辨识度筛选 ──
    close_panel = panels[0] if panels else None
    if close_panel is not None and date in close_panel.index:
        streak_scores = compute_streak_factor(
            panels, date=date, decay_days=p["STREAK_DECAY_DAYS"]
        )
        streak_scores = streak_scores.dropna()

        if len(streak_scores) > 0:
            # 取前30%（分位数门槛）
            threshold_score = streak_scores.quantile(p["STREAK_PCTILE"] / 100.0)
            streak_pass = streak_scores[streak_scores >= threshold_score].index
            candidates = [c for c in candidates if c in streak_pass]

    if not candidates:
        return []

    # ── Step 3: 排除当前连板中的高风险股 ──
    if close_panel is not None and date in close_panel.index:
        streak_risk = compute_streak_risk(panels, date=date)
        streak_risk = streak_risk.dropna()
        candidates = [c for c in candidates if streak_risk.get(c, 0) == 0]

    if not candidates:
        return []

    # ── Step 4: 排除 ST/退市 ──
    if 'delist_risk' in factors and date in factors['delist_risk'].index:
        dr_t = factors['dr_threshold'].loc[date] if 'dr_threshold' in factors else 0
        candidates = [c for c in candidates
                      if c not in factors['delist_risk'].columns
                      or factors['delist_risk'].loc[date, c] <= dr_t]

    if current_holdings:
        candidates = [c for c in candidates if c not in current_holdings]
    if sold_recently:
        candidates = [c for c in candidates if c not in sold_recently]

    if not candidates:
        return []

    # ── Step 5: 多因子评分 ──
    scores = pd.Series(0.0, index=candidates)

    # 连板辨识度（核心因子）
    if close_panel is not None and date in close_panel.index:
        streak_s = compute_streak_factor(panels, date=date, decay_days=p["STREAK_DECAY_DAYS"])
        streak_s = _score_column_from_raw(streak_s, candidates)
        scores += streak_s * p["W_STREAK"]

    # 动量
    scores += _score_column(factors, date, 'mom_5').reindex(candidates).fillna(0) * p["W_MOM"]

    # 量价共振
    if p.get("W_PV_CORR", 0) > 0:
        scores += _score_column(factors, date, 'pv_corr_20').reindex(candidates).fillna(0) * p["W_PV_CORR"]

    # 规模
    if p.get("W_SIZE", 0) > 0:
        scores += _score_column(factors, date, 'size_factor').reindex(candidates).fillna(0) * p["W_SIZE"]

    # 资金流
    if p.get("W_FUND_FLOW", 0) > 0:
        scores += _score_column(factors, date, 'fund_flow', clip_min=0.5, clip_max=3.0).reindex(candidates).fillna(0) * p["W_FUND_FLOW"]

    # 跳空
    if p.get("W_GAP", 0) > 0:
        scores += _score_column(factors, date, 'gap_ratio', clip_min=0, clip_max=0.05).reindex(candidates).fillna(0) * p["W_GAP"]

    # 非流动性
    if p.get("W_ILLIQ", 0) > 0:
        scores += _score_column(factors, date, 'illiq').reindex(candidates).fillna(0) * p["W_ILLIQ"]

    # ── Step 6: 排序取Top ──
    scores = scores.sort_values(ascending=False)
    selected = scores.index[:p["MAX_DAILY_BUY"]]

    return [(code, scores[code]) for code in selected]


def _score_column_from_raw(raw_series, candidates):
    """
    对原始因子值做 z-score 标准化（与 _score_column 一致）
    """
    s = raw_series.reindex(candidates).dropna()
    if len(s) == 0:
        return pd.Series(0.0, index=candidates)
    mean = s.mean()
    std = s.std()
    if std > 0:
        scored = (s - mean) / std
    else:
        scored = pd.Series(0.0, index=s.index)
    return scored.reindex(candidates).fillna(0)
