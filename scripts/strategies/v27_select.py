#!/usr/bin/env python3
"""
scripts/v27_select.py — v27 价量共振选股函数
==============================================
可被 strategy_map.py 动态加载，供模拟盘和回测共同使用。

函数签名统一：
    calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel) -> dict
    select_stocks(factors, date, current_holdings=None) -> list[(code, score)]
"""
import pandas as pd
import numpy as np

# 默认参数（可被覆盖）
DEFAULT_PARAMS = {
    "MOM_THRESHOLD": 0.02,
    "MAX_HOLDINGS": 8,
    "MAX_DAILY_BUY": 8,
    "MAX_POSITION": 0.30,
    "HOLD_DAYS_MAX": 5,
    "STOP_LOSS": -0.015,
    "TAKE_PROFIT": 0.03,
}


def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None, params=None):
    """
    计算 v27 因子（面板级别，向量化）

    返回 dict:
        mom_5, gap_ratio, illiq, boll_w, pv_corr_10, pv_corr_20, delist_risk, dr_threshold
    """
    eps = 1e-10
    returns = close_panel.pct_change()
    mom_5 = close_panel.pct_change(5)

    # gap
    prev_close = close_panel.shift(1)
    gap_ratio = (open_panel - prev_close) / (prev_close + eps) if open_panel is not None else returns * 0

    # illiquidity (小市值代理)
    avg_amount = amount_panel.rolling(20).mean()
    illiq = 1.0 / (avg_amount / 1e8 + eps)

    # 布林带宽
    ma20 = close_panel.rolling(20).mean()
    std20 = close_panel.rolling(20).std()
    boll_w = (4 * std20) / (ma20 + eps)

    # pv_corr_10/20（价量滚动相关系数）
    vol_5 = volume_panel.rolling(5).mean()
    vr = vol_5 / (volume_panel.rolling(20).mean() + eps)

    def _pcorr(window):
        rm = returns.rolling(window).mean()
        vrm = vr.rolling(window).mean()
        cov = ((returns - rm) * (vr - vrm)).rolling(window).mean()
        return cov / (returns.rolling(window).std() * vr.rolling(window).std() + eps)

    pv_corr_10 = _pcorr(10)
    pv_corr_20 = _pcorr(20)

    # 退市风险
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

    return {
        'mom_5': mom_5, 'gap_ratio': gap_ratio, 'illiq': illiq,
        'boll_w': boll_w, 'pv_corr_10': pv_corr_10, 'pv_corr_20': pv_corr_20,
        'delist_risk': delist_risk, 'dr_threshold': dr_threshold,
    }


def select_stocks_v27(factors, date, current_holdings=None, params=None):
    """
    v27 选股：动量 + 量价共振 + 辅助因子

    参数:
        factors: dict — calc_factors() 返回
        date: Timestamp — 选股日期
        current_holdings: dict — 当前持仓（可选，过滤已持有）
        params: dict — 覆盖默认参数（可选）

    返回:
        list[(code, score)] — 按评分降序排列
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    mom_threshold = p["MOM_THRESHOLD"]

    if date not in factors['mom_5'].index:
        return []

    m5 = factors['mom_5'].loc[date].dropna()
    cands = []

    for code in m5.index:
        m = m5[code]
        if m <= mom_threshold:
            continue

        # 排除：量价严重背离
        if date in factors['pv_corr_10'].index and code in factors['pv_corr_10'].columns:
            pv10 = factors['pv_corr_10'].loc[date, code]
            if not pd.isna(pv10) and pv10 < -0.5:
                continue

        # 排除：退市风险
        if date in factors['dr_threshold'].index and code in factors['delist_risk'].columns:
            if factors['delist_risk'].loc[date, code] > factors['dr_threshold'].loc[date]:
                continue

        score = m * 100

        # pv_corr_20 共振加分（量价同步）
        if date in factors['pv_corr_20'].index and code in factors['pv_corr_20'].columns:
            pv20 = factors['pv_corr_20'].loc[date, code]
            if not pd.isna(pv20) and pv20 > 0:
                score += 0.5

        # 跳空加分
        if date in factors['gap_ratio'].index and code in factors['gap_ratio'].columns:
            gr = factors['gap_ratio'].loc[date, code]
            if not pd.isna(gr) and gr > 0.02:
                score += 0.5

        # 非流动性加分（小市值）
        if date in factors['illiq'].index and code in factors['illiq'].columns:
            il = factors['illiq'].loc[date, code]
            if not pd.isna(il) and il > 0:
                score += 0.8

        # 布林带宽加分
        if date in factors['boll_w'].index and code in factors['boll_w'].columns:
            bw = factors['boll_w'].loc[date, code]
            if not pd.isna(bw) and bw > 1.2:
                score += 0.3

        cands.append((code, score))

    cands.sort(key=lambda x: x[1], reverse=True)

    if current_holdings:
        cands = [(c, s) for c, s in cands if c not in current_holdings]

    return cands
