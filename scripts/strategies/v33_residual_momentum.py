#!/usr/bin/env python3
"""
scripts/strategies/v33_residual_momentum.py — v33 残差动量选股（v2 双因子版）
====================================================
v2 改动：
1. 双因子残差模型：剥离市场 Beta + 行业 Beta（市值分组代理行业）
2. 残差 = 个股收益 - β_mkt × 市场 - β_ind × 行业
3. 更纯净的 alpha 信号

核心逻辑：
- 对每只股票做 252 日滚动回归：个股 = α + β_mkt × 市场 + β_ind × 行业 + ε
- 市场 = 全市场等权收益
- 行业 = 市值分组收益（大盘/中盘/小盘）
- 残差动量 = 过去 126 日累积残差
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
    "EXCLUDE_STAR": True,
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
    计算 v33 残差动量因子（双因子模型）

    返回 dict:
        resid_mom: 残差动量
        alpha: 个股 alpha
        mom_5: 原始 5 日动量
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    eps = 1e-10

    returns = close_panel.pct_change()
    window = p["RESID_WINDOW"]
    lookback = p["RESID_LOOKBACK"]

    # ── 市场收益 ──
    mkt_daily = returns.mean(axis=1)

    # ── 行业分组（市值代理）──
    avg_amount = amount_panel.rolling(20).mean()
    rank = avg_amount.rank(axis=1, pct=True)

    # 大盘(rank>0.67), 中盘(0.33~0.67), 小盘(<0.33)
    is_large = rank > 0.67
    is_small = rank <= 0.33

    # 行业收益：大盘组 vs 小盘组
    large_ret = returns.where(is_large).mean(axis=1)
    small_ret = returns.where(is_small).mean(axis=1)
    # 行业因子 = 大盘 - 小盘（SMB 因子代理）
    smb_daily = large_ret - small_ret

    # ── 滚动 OLS 提取残差（双因子）──
    # r_i = α + β_mkt × r_mkt + β_smb × SMB + ε
    # 简化：用 rolling cov 近似
    def rolling_beta(factor_series, window):
        """计算 rolling beta = cov(r_i, factor) / var(factor)"""
        mean_ri = returns.rolling(window).mean()
        mean_f = factor_series.rolling(window).mean()
        mean_prod = (returns * factor_series.values[:, None]).rolling(window).mean()
        cov = mean_prod - mean_ri * mean_f.values[:, None]
        var_f = factor_series.rolling(window).var()
        return cov / (var_f.values[:, None] + eps)

    beta_mkt = rolling_beta(mkt_daily, window)
    beta_smb = rolling_beta(smb_daily, window)

    # 残差 = 个股收益 - β_mkt × 市场 - β_smb × SMB
    residuals = (returns
                 - beta_mkt * mkt_daily.values[:, None]
                 - beta_smb * smb_daily.values[:, None])

    # 残差动量
    resid_mom = residuals.rolling(lookback).sum()

    # OLS 截距（alpha）
    alpha = (returns.rolling(window).mean()
             - beta_mkt * mkt_daily.rolling(window).mean().values[:, None]
             - beta_smb * smb_daily.rolling(window).mean().values[:, None])

    mom_5 = close_panel.pct_change(5)

    return {
        'resid_mom': resid_mom,
        'alpha': alpha,
        'mom_5': mom_5,
    }


def select_stocks_v33(factors, date, current_holdings=None, params=None):
    """
    v33 选股（双因子版）：残差动量 + 原始动量辅助
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

        if p.get("EXCLUDE_STAR", True) and code.startswith('688'):
            continue

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
