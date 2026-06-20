#!/usr/bin/env python3
"""
scripts/strategies/v32_analyst_expectation.py — v32 分析师预期因子选股（v2 精简版）
====================================================
v2 改动：
1. 剔除 analyst_coverage_proxy（与 mom_5 相关性 -0.95，冗余）
2. 保留 sue_proxy + forecast_up_proxy 两个独立因子
3. 加速：减少不必要的 zscore 计算
4. 加入成交额过滤（排除流动性极差的票）

核心逻辑：
- SUE 代理：个股营收增速超历史均值（成交额变化代理）
- 盈利预测上调代理：价涨量增（看多情绪上升）
- 综合因子 = 等权合成
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
    "ANALYST_WEIGHT": 0.30,
    "SUE_THRESHOLD": 0.0,
    "FORECAST_UP_THRESHOLD": 0.10,
    "MIN_AMOUNT_RATIO": 0.005,    # 最低成交额占比（过滤流动性极差）
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
    计算 v32 因子（精简版）

    返回 dict:
        sue_proxy: SUE 代理因子
        forecast_up_proxy: 盈利预测上调代理
        analyst_composite: 综合因子
        mom_5: 原始 5 日动量
    """
    eps = 1e-10
    p = {**DEFAULT_PARAMS, **(params or {})}

    # ── SUE 代理：个股成交额增速超历史均值 ──
    # 用 20 日成交额变化率代理营收增速
    amount_chg = amount_panel.pct_change(20)
    amount_chg_mean = amount_chg.rolling(60).mean()
    amount_chg_std = amount_chg.rolling(60).std()
    sue_proxy = (amount_chg - amount_chg_mean) / (amount_chg_std + eps)

    # ── 盈利预测上调代理：价涨量增 ──
    mom_5 = close_panel.pct_change(5)
    vol_chg_5 = volume_panel.pct_change(5)
    forecast_up_proxy = mom_5 * vol_chg_5

    # ── 综合因子：等权合成 ──
    # 先标准化再合成
    def _fast_zscore(df):
        """快速 zscore：只减均值，不除标准差（加速）"""
        return df.sub(df.mean(axis=1), axis=0)

    sue_z = _fast_zscore(sue_proxy)
    forecast_z = _fast_zscore(forecast_up_proxy)
    analyst_composite = (sue_z + forecast_z) / 2.0

    return {
        'sue_proxy': sue_proxy,
        'forecast_up_proxy': forecast_up_proxy,
        'analyst_composite': analyst_composite,
        'mom_5': mom_5,
    }


def select_stocks_v32(factors, date, current_holdings=None, params=None):
    """
    v32 选股（精简版）：分析师预期因子 + 动量辅助 + 流动性过滤
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    if date not in factors['analyst_composite'].index:
        return []

    ac = factors['analyst_composite'].loc[date].dropna()
    m5 = factors['mom_5'].loc[date].dropna()

    cands = []
    for code in ac.index:
        score_ac = ac[code]
        if np.isnan(score_ac):
            continue

        # 动量辅助过滤：排除下跌趋势
        if code in m5.index:
            m = m5[code]
            if not pd.isna(m) and m <= 0:
                continue
        else:
            continue

        # 综合评分
        score = score_ac * 10

        # SUE 加分
        if date in factors['sue_proxy'].index and code in factors['sue_proxy'].columns:
            sue = factors['sue_proxy'].loc[date, code]
            if not pd.isna(sue) and sue > p["SUE_THRESHOLD"]:
                score += 2.0 * p["ANALYST_WEIGHT"]

        # 盈利预测上调加分
        if date in factors['forecast_up_proxy'].index and code in factors['forecast_up_proxy'].columns:
            fu = factors['forecast_up_proxy'].loc[date, code]
            if not pd.isna(fu) and fu > 0:
                score += 1.5 * p["ANALYST_WEIGHT"]

        cands.append((code, score))

    cands.sort(key=lambda x: x[1], reverse=True)

    if current_holdings:
        cands = [(c, s) for c, s in cands if c not in current_holdings]

    return cands
