#!/usr/bin/env python3
"""
scripts/v39_pv_resonance.py — v39 价量共振选股（高频版）
====================================================
v38 问题：选股条件太严，交易频率太低（~400次 vs v27 的 1812次）

v39 核心改进：
1. 放宽硬筛选门槛，提高候选数量
2. 动量阈值 5% → 3%
3. 量价共振 0.10 → 0.05
4. 布林带宽 0.2 → 0.15
5. 成交额 500万 → 300万
6. MAX_DAILY_BUY 8 → 10
7. 保持 v27 的套利参数（TP=3%, SL=-1.5%）
"""
import pandas as pd
import numpy as np

DEFAULT_PARAMS = {
    # ── 风控参数（与 v27 一致）──
    "STOP_LOSS": -0.015,
    "TAKE_PROFIT": 0.03,
    "HOLD_DAYS_MAX": 5,
    "HOLD_DAYS_EXTEND": 5,
    "HOLD_DAYS_EXTEND_PNL": 0.03,
    "MAX_DAILY_BUY": 10,
    "MAX_POSITION": 0.20,
    "MAX_HOLDINGS": 10,
    "COOLDOWN_DAYS": 0,
    "MAX_SAME_PREFIX": 0,

    # ── 硬筛选阈值（放宽以提高交易频率）──
    "MOM_THRESHOLD": 0.03,        # 动量 > 3%（v38: 5%）
    "PV_CORR_20_MIN": 0.05,        # 量价共振 > 0.05（v38: 0.10）
    "TURNOVER_MIN": 0.003,        # 换手率 > 0.3%（v38: 0.5%）
    "MIN_AMOUNT_DAYS": 3000000,   # 日均成交额 > 300 万（v38: 500万）
    "BOLL_W_MIN": 0.15,           # 布林带宽 > 0.15（v38: 0.2）

    # ── 评分权重（通过硬筛后排序）──
    "W_MOM": 0.30,
    "W_PV_CORR": 0.25,
    "W_TURNOVER": 0.15,
    "W_SIZE": 0.10,
    "W_FUND_FLOW": 0.20,
}


def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None, params=None):
    """计算 v39 因子面板（与 v38 相同）"""
    eps = 1e-10
    returns = close_panel.pct_change()
    mom_5 = close_panel.pct_change(5)

    prev_close = close_panel.shift(1)
    gap_ratio = (open_panel - prev_close) / (prev_close + eps) if open_panel is not None else returns * 0

    ma20 = close_panel.rolling(20).mean()
    std20 = close_panel.rolling(20).std()
    boll_w = (4 * std20) / (ma20 + eps)

    vol_5 = volume_panel.rolling(5).mean()
    vr = vol_5 / (volume_panel.rolling(20).mean() + eps)

    def _pcorr(window):
        rm = returns.rolling(window).mean()
        vrm = vr.rolling(window).mean()
        cov = ((returns - rm) * (vr - vrm)).rolling(window).mean()
        return cov / (returns.rolling(window).std() * vr.rolling(window).std() + eps)

    pv_corr_10 = _pcorr(10)
    pv_corr_20 = _pcorr(20)

    price_level = close_panel.rolling(20).mean()
    price_trend = close_panel.pct_change(20)
    vol_shrink = vol_5 / (volume_panel.rolling(20).mean() + eps)
    vol_current = returns.rolling(5).std()
    vol_hist = returns.rolling(60).std()
    vol_abnormal = vol_current / (vol_hist + eps)

    def _zscore(df):
        m = df.mean(axis=1)
        s = df.std(axis=1)
        return (df.sub(m, axis=0)).div(s + eps, axis=0)

    delist_risk = (-_zscore(price_level) + -_zscore(price_trend) +
                   -_zscore(vol_shrink) + _zscore(vol_abnormal)) / 4.0
    dr_threshold = delist_risk.quantile(0.9, axis=1)

    amount_5d = amount_panel.rolling(5).mean()
    vol_expansion = vol_5 / (volume_panel.rolling(20).mean() + eps)

    turnover = volume_panel / (amount_panel / (close_panel + eps) + eps)
    turnover_avg = turnover.rolling(5).mean()

    est_market_cap = amount_panel.rolling(20).mean() * 20
    size_factor = 1.0 / (np.log(est_market_cap / 1e8 + 1) / 10 + eps)

    path_vol_5 = returns.rolling(5).std()
    mom_quality = mom_5 / (path_vol_5 * np.sqrt(5) + eps)

    up_days = returns.copy()
    down_days = returns.copy()
    up_days[returns <= 0] = np.nan
    down_days[returns >= 0] = np.nan
    up_vol = up_days * volume_panel
    down_vol = down_days.abs() * volume_panel
    up_vol_sum = up_vol.rolling(10).sum()
    down_vol_sum = down_vol.rolling(10).sum()
    fund_flow = up_vol_sum / (down_vol_sum + eps)

    return {
        'mom_5': mom_5, 'gap_ratio': gap_ratio,
        'boll_w': boll_w, 'pv_corr_10': pv_corr_10, 'pv_corr_20': pv_corr_20,
        'delist_risk': delist_risk, 'dr_threshold': dr_threshold,
        'amount_5d': amount_5d, 'vol_expansion': vol_expansion,
        'turnover_avg': turnover_avg,
        'size_factor': size_factor,
        'mom_quality': mom_quality,
        'fund_flow': fund_flow,
    }


def _score_column(factors, date, col, clip_min=None, clip_max=None):
    if date not in factors[col].index:
        return pd.Series(dtype=float)
    s = factors[col].loc[date].dropna()
    if clip_min is not None:
        s = s.clip(lower=clip_min)
    if clip_max is not None:
        s = s.clip(upper=clip_max)
    if s.max() == s.min():
        return pd.Series(0.5, index=s.index)
    return (s - s.min()) / (s.max() - s.min())


def select_stocks_v39(factors, date, current_holdings=None, params=None,
                      sold_recently=None):
    """
    v39 选股：硬筛选 + 评分排序（放宽门槛版）
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    if date not in factors['mom_5'].index:
        return []

    m5 = factors['mom_5'].loc[date].dropna()
    candidates = list(m5.index)

    # 1. 动量 > 3%
    candidates = [c for c in candidates if m5[c] > p["MOM_THRESHOLD"]]

    # 2. 量价共振 pv_corr_20 > 0.05
    if date in factors['pv_corr_20'].index:
        pv20 = factors['pv_corr_20'].loc[date]
        candidates = [c for c in candidates if c in pv20.index and pv20[c] > p["PV_CORR_20_MIN"]]

    # 3. 换手率 > 0.3%
    if date in factors['turnover_avg'].index:
        to = factors['turnover_avg'].loc[date]
        candidates = [c for c in candidates if c in to.index and to[c] > p["TURNOVER_MIN"]]

    # 4. 流动性 > 300万
    if date in factors['amount_5d'].index:
        amt = factors['amount_5d'].loc[date]
        candidates = [c for c in candidates if c in amt.index and amt[c] >= p["MIN_AMOUNT_DAYS"]]

    # 5. 布林带宽 > 0.15
    if date in factors['boll_w'].index:
        bw = factors['boll_w'].loc[date]
        candidates = [c for c in candidates if c in bw.index and bw[c] >= p["BOLL_W_MIN"]]

    # 6. 排除退市风险
    if date in factors['dr_threshold'].index and date in factors['delist_risk'].index:
        dr_t = factors['dr_threshold'].loc[date]
        candidates = [c for c in candidates
                      if c not in factors['delist_risk'].columns
                      or factors['delist_risk'].loc[date, c] <= dr_t]

    # 7. 排除已持有
    if current_holdings:
        candidates = [c for c in candidates if c not in current_holdings]

    if not candidates:
        return []

    # ── 评分排序 ──
    scores = pd.Series(0.0, index=candidates)

    mom_scores = _score_column(factors, date, 'mom_5')
    scores += mom_scores.reindex(candidates).fillna(0) * p["W_MOM"]

    pv_scores = _score_column(factors, date, 'pv_corr_20')
    scores += pv_scores.reindex(candidates).fillna(0) * p["W_PV_CORR"]

    to_scores = _score_column(factors, date, 'turnover_avg', clip_min=0, clip_max=0.05)
    scores += to_scores.reindex(candidates).fillna(0) * p["W_TURNOVER"]

    sf_scores = _score_column(factors, date, 'size_factor')
    scores += sf_scores.reindex(candidates).fillna(0) * p["W_SIZE"]

    ff_scores = _score_column(factors, date, 'fund_flow', clip_min=0.5, clip_max=3.0)
    scores += ff_scores.reindex(candidates).fillna(0) * p["W_FUND_FLOW"]

    scores = scores.sort_values(ascending=False)
    selected = scores.index[:p["MAX_DAILY_BUY"]]

    return [(code, scores[code]) for code in selected]
