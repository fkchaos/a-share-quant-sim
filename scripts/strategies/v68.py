#!/usr/bin/env python3
"""
v68: v67基础上降低低流动性权重、加大动量权重
测试：弱化小票偏好，强化动量驱动
"""
import pandas as pd
import numpy as np
from scripts.strategies.v67 import calc_factors_v67, DEFAULT_PARAMS as V67_PARAMS
from scripts.strategies.v39g_optimized import _score_column

# 在v67参数基础上调整权重
DEFAULT_PARAMS = {
    **V67_PARAMS,
}

# v68权重调整：降ILLIQ，升MOM
DEFAULT_PARAMS["W_MOM"] = 0.35         # 0.08→0.25，大幅提高动量权重
DEFAULT_PARAMS["W_ILLIQ"] = 0.15       # 0.16→0.05，大幅降低低流动性权重
DEFAULT_PARAMS["W_SIZE"] = 0.35        # 0.35→0.30，略降小市值权重
DEFAULT_PARAMS["W_TWO_DAY_LIMIT"] = 0.35  # 不变
DEFAULT_PARAMS["W_TURNOVER"] = 0.03    # 0.04→0.03
DEFAULT_PARAMS["W_PV_CORR"] = 0.02     # 0.04→0.02
DEFAULT_PARAMS["W_GAP"] = 0.00         # 0.04→0，去掉
DEFAULT_PARAMS["W_FUND_FLOW"] = 0.00   # 0.04→0，去掉


def calc_factors_v68(close_panel, volume_panel, amount_panel,
                     high_panel=None, low_panel=None, open_panel=None,
                     extra_data=None):
    """v68因子 = v67因子 + 3天内涨停因子"""
    factors = calc_factors_v67(close_panel, volume_panel, amount_panel,
                            high_panel, low_panel, open_panel, extra_data)

    # ── 新增：3天内涨停过（替代原来的连续两天涨停）──
    # 涨停阈值
    returns = close_panel.pct_change()
    limit_threshold = 0.095
    limit_up = (returns >= limit_threshold) & (returns <= 0.105)

    # 3天内任一天涨停
    recent_limit_3d = (limit_up.rolling(3, min_periods=1).sum() > 0).astype(float)
    factors['recent_limit_3d'] = recent_limit_3d

    return factors


def select_stocks_v68(factors, date, current_holdings=None, params=None,
                      sold_recently=None, close_panel=None, high_panel=None):
    """v68选股：复用v67逻辑，权重由DEFAULT_PARAMS控制"""
    p = {**DEFAULT_PARAMS, **(params or {})}

    # 情绪检查
    if 'market_sentiment' in factors and date in factors['market_sentiment'].index:
        sent = factors['market_sentiment'].loc[date]
        if pd.notna(sent) and sent < p['SENTIMENT_THRESHOLD']:
            return []

    if date not in factors['mom_5'].index:
        return []

    m5 = factors['mom_5'].loc[date].dropna()
    candidates = list(m5.index)

    # 筛选条件（同v67）
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

    # 涨停过滤
    if p.get("EXCLUDE_LIMIT_UP") and close_panel is not None and high_panel is not None:
        if date in close_panel.index and date in high_panel.index:
            close_today = close_panel.loc[date]
            high_today = high_panel.loc[date]
            candidates = [c for c in candidates
                         if c in close_today.index and c in high_today.index
                         and not (close_today[c] == high_today[c])]

    if not candidates:
        return []

    # 评分（权重由DEFAULT_PARAMS控制）
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

    # 近3天涨停加分（替代原来的连续两天涨停）
    if p.get("W_RECENT_LIMIT_3D", 0) > 0 and 'recent_limit_3d' in factors:
        tdl = factors['recent_limit_3d'].loc[date] if date in factors['recent_limit_3d'].index else pd.Series(0, index=candidates)
        scores += tdl.reindex(candidates).fillna(0) * p["W_RECENT_LIMIT_3D"]

    # 排序选择
    scores = scores.sort_values(ascending=False)
    selected = scores.index[:p["MAX_DAILY_BUY"]]
    return [(code, scores[code]) for code in selected]


if __name__ == '__main__':
    print("v68: v67降低低流动性权重+加大动量权重")
    print(f"W_MOM: {DEFAULT_PARAMS['W_MOM']}, W_ILLIQ: {DEFAULT_PARAMS['W_ILLIQ']}")
