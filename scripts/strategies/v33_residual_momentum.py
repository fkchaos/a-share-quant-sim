#!/usr/bin/env python3
"""
scripts/strategies/v33_residual_momentum.py — v33 残差动量选股
====================================================
核心逻辑：
- 对每只股票做 252 日滚动回归：个股 = α + β_mkt × 市场 + ε
- 提取残差（个股超越市场解释的部分）
- 对残差做 126 日（6月）累积，得到残差动量因子
- 残差动量 > 0 表示个股有独立 alpha

注意：排除科创板（688开头），避免流动性集中问题
"""
import pandas as pd
import numpy as np

DEFAULT_PARAMS = {
    "STOP_LOSS": -0.02,
    "TAKE_PROFIT": 0.05,
    "MAX_HOLDINGS": 8,
    "MAX_DAILY_BUY": 4,
    "MAX_POSITION": 0.20,
    "HOLD_DAYS_MAX": 5,
    "HOLD_DAYS_MIN": 1,
    "HOLD_DAYS_EXTEND": 7,
    "HOLD_DAYS_EXTEND_PNL": 0.03,
    "RESID_WINDOW": 252,
    "RESID_LOOKBACK": 126,
    "MOM_THRESHOLD": 0.0,
    "EXCLUDE_STAR": True,       # 排除科创板
    "REGIME_ENABLED": True,
    "REGIME_MA_PERIOD": 20,
    "REGIME_SLOPE_DAYS": 5,
    "REGIME_BULL_ALLOC": 1.0,
    "REGIME_SIDEWAYS_ALLOC": 0.7,
    "REGIME_BEAR_ALLOC": 0.3,
}


def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel,
                 open_panel=None, params=None):
    """
    计算 v33 残差动量因子

    返回 dict:
        resid_mom: 残差动量
        alpha: 个股 alpha（OLS 截距近似）
        beta_mkt: 市场 Beta
        mom_5: 原始 5 日动量（辅助对比）
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    eps = 1e-10

    returns = close_panel.pct_change()
    window = p["RESID_WINDOW"]
    lookback = p["RESID_LOOKBACK"]

    # 市场收益（等权全市场均值）
    mkt_daily = returns.mean(axis=1)

    # 滚动 OLS 提取残差（单因子模型）
    rolling_mean_ri = returns.rolling(window).mean()
    rolling_mean_rm = mkt_daily.rolling(window).mean()
    rolling_mean_product = (returns * mkt_daily.values[:, None]).rolling(window).mean()
    cov_with_mkt = rolling_mean_product - rolling_mean_ri * rolling_mean_rm.values[:, None]
    var_mkt = mkt_daily.rolling(window).var()
    beta_mkt = cov_with_mkt / (var_mkt.values[:, None] + eps)

    # 残差 = 个股收益 - β × 市场收益
    residuals = returns - beta_mkt * mkt_daily.values[:, None]

    # 残差动量 = 过去 lookback 个交易日的累积残差
    resid_mom = residuals.rolling(lookback).sum()

    # OLS 截距近似
    alpha = rolling_mean_ri - beta_mkt * rolling_mean_rm.values[:, None]

    # 辅助：5 日原始动量
    mom_5 = close_panel.pct_change(5)

    return {
        'resid_mom': resid_mom,
        'alpha': alpha,
        'beta_mkt': beta_mkt,
        'mom_5': mom_5,
    }


def select_stocks_v33(factors, date, current_holdings=None, params=None):
    """
    v33 选股：残差动量 + 原始动量辅助 + 流动性过滤

    逻辑：
    1. resid_mom > 0（有正 alpha）
    2. mom_5 > -3%（排除短期大跌）
    3. 排除科创板（688开头）— 流动性差
    4. 综合评分：残差动量为主
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    if date not in factors['resid_mom'].index:
        return []

    rm = factors['resid_mom'].loc[date].dropna()
    m5 = factors['mom_5'].loc[date].dropna()

    cands = []
    for code in rm.index:
        resid = rm[code]
        if resid <= p["MOM_THRESHOLD"]:
            continue

        # 排除科创板（流动性差，避免集中）
        if p.get("EXCLUDE_STAR", True) and code.startswith('688'):
            continue

        # 原始动量辅助过滤
        if code in m5.index:
            m = m5[code]
            if np.isnan(m) or m < -0.03:
                continue
        else:
            m = 0

        score = resid * 100 + max(m, 0) * 50
        cands.append((code, score))

    cands.sort(key=lambda x: x[1], reverse=True)

    if current_holdings:
        cands = [(c, s) for c, s in cands if c not in current_holdings]

    return cands
