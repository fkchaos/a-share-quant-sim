#!/usr/bin/env python3
"""
scripts/strategies/v29_select.py — v29 价量共振+动量增强选股函数
==============================================================
基于v27 + factorset动量因子融合

新增因子：
- mom_20: 20日动量（IC=0.0139）
- mom_40: 40日动量（IC=0.0167）

融合方式：
- v27核心因子权重85%
- 动量因子权重15%
"""

import pandas as pd
import numpy as np

# 默认参数（与v27保持一致）
DEFAULT_PARAMS = {
    "MOM_THRESHOLD": 0.02,
    "MAX_HOLDINGS": 8,
    "MAX_DAILY_BUY": 4,
    "MAX_POSITION": 0.20,
    "HOLD_DAYS_MAX": 5,
    "STOP_LOSS": -0.015,
    "TAKE_PROFIT": 0.03,
    "MOM_20_WEIGHT": 0.15,  # 动量因子权重
    "MOM_40_WEIGHT": 0.15,
}


def calc_momentum_factors(close_panel, volume_panel):
    """
    计算factorset动量因子
    """
    eps = 1e-10
    
    # 多周期动量
    mom_20 = close_panel.pct_change(20)
    mom_40 = close_panel.pct_change(40)
    mom_60 = close_panel.pct_change(60)
    
    # 标准化
    def zscore(df):
        m = df.mean(axis=1, skipna=True)
        s = df.std(axis=1, skipna=True)
        return (df.sub(m.values[:, None], axis=0)).div(s.values[:, None] + eps, axis=0)
    
    mom_20_z = zscore(mom_20)
    mom_40_z = zscore(mom_40)
    mom_60_z = zscore(mom_60)
    
    return {
        'mom_20': mom_20,
        'mom_40': mom_40,
        'mom_60': mom_60,
        'mom_20_z': mom_20_z,
        'mom_40_z': mom_40_z,
        'mom_60_z': mom_60_z,
    }


def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None, params=None):
    """
    计算 v29 因子（v27 + 动量因子）
    """
    eps = 1e-10
    returns = close_panel.pct_change()
    mom_5 = close_panel.pct_change(5)

    # v27 核心因子
    prev_close = close_panel.shift(1)
    gap_ratio = (open_panel - prev_close) / (prev_close + eps) if open_panel is not None else returns * 0

    avg_amount = amount_panel.rolling(20).mean()
    illiq = 1.0 / (avg_amount / 1e8 + eps)

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

    # 动量因子
    momentum_factors = calc_momentum_factors(close_panel, volume_panel)

    # 合并所有因子
    all_factors = {
        'mom_5': mom_5, 'gap_ratio': gap_ratio, 'illiq': illiq,
        'boll_w': boll_w, 'pv_corr_10': pv_corr_10, 'pv_corr_20': pv_corr_20,
        'delist_risk': delist_risk, 'dr_threshold': dr_threshold,
        **momentum_factors
    }

    return all_factors


def select_stocks_v29(factors, date, current_holdings=None, params=None):
    """
    v29 选股：v27核心逻辑 + 动量因子增强

    融合方式：
    - v27核心评分（动量+量价共振+辅助因子）
    - 动量因子额外加分（mom_20_z, mom_40_z）
    - 总分 = v27_base_score * 0.85 + momentum_score * 0.15
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    mom_threshold = p["MOM_THRESHOLD"]
    mom_20_weight = p["MOM_20_WEIGHT"]
    mom_40_weight = p["MOM_40_WEIGHT"]

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

        # v27核心评分
        score = m * 100

        if date in factors['pv_corr_20'].index and code in factors['pv_corr_20'].columns:
            pv20 = factors['pv_corr_20'].loc[date, code]
            if not pd.isna(pv20) and pv20 > 0:
                score += 0.5

        if date in factors['gap_ratio'].index and code in factors['gap_ratio'].columns:
            gr = factors['gap_ratio'].loc[date, code]
            if not pd.isna(gr) and gr > 0.02:
                score += 0.5

        if date in factors['illiq'].index and code in factors['illiq'].columns:
            il = factors['illiq'].loc[date, code]
            if not pd.isna(il) and il > 0:
                score += 0.8

        if date in factors['boll_w'].index and code in factors['boll_w'].columns:
            bw = factors['boll_w'].loc[date, code]
            if not pd.isna(bw) and bw > 1.2:
                score += 0.3

        # 动量因子额外加分
        momentum_score = 0
        if date in factors['mom_20_z'].index and code in factors['mom_20_z'].columns:
            mom20_z = factors['mom_20_z'].loc[date, code]
            if not pd.isna(mom20_z):
                momentum_score += mom20_z * mom_20_weight

        if date in factors['mom_40_z'].index and code in factors['mom_40_z'].columns:
            mom40_z = factors['mom_40_z'].loc[date, code]
            if not pd.isna(mom40_z):
                momentum_score += mom40_z * mom_40_weight

        # 融合评分：v27核心85% + 动量15%
        total_score = score * 0.85 + momentum_score * 0.15

        cands.append((code, total_score))

    cands.sort(key=lambda x: x[1], reverse=True)

    if current_holdings:
        cands = [(c, s) for c, s in cands if c not in current_holdings]

    return cands[:p["MAX_HOLDINGS"]]


if __name__ == "__main__":
    # 测试代码
    print("v29选股函数已加载")
    print("新增因子: mom_20, mom_40, mom_60")
    print("融合方式: v27核心85% + 动量因子15%")