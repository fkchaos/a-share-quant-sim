#!/usr/bin/env python3
"""
scripts/strategies/v32_analyst_expectation.py — v32 分析师预期因子选股
====================================================
基于券商金工研报（广发/华泰/新浪）构建的分析师预期因子：
1. SUE（标准化超预期盈余）
2. 分析师异常覆盖
3. 盈利预测上调
4. 分析师综合因子

核心逻辑：
- 与 v27 量价因子低相关（不同信号源）
- 捕捉基本面情绪面的 alpha 信号
- 作为独立评分维度与 v27 加权融合

可被 strategy_map.py 动态加载。

函数签名统一：
    calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel, params) -> dict
    select_stocks(factors, date, current_holdings=None, params=None) -> list[(code, score)]
"""
import pandas as pd
import numpy as np

# ── 默认参数 ──
DEFAULT_PARAMS = {
    # 风控（与 v27 一致）
    "STOP_LOSS": -0.02,
    "TAKE_PROFIT": 0.05,
    "MAX_HOLDINGS": 8,
    "MAX_DAILY_BUY": 4,
    "MAX_POSITION": 0.20,
    "HOLD_DAYS_MAX": 5,
    "HOLD_DAYS_MIN": 1,
    "HOLD_DAYS_EXTEND": 7,
    "HOLD_DAYS_EXTEND_PNL": 0.03,
    # 分析师预期因子参数
    "ANALYST_WEIGHT": 0.30,        # 分析师因子在综合评分中的权重
    "SUE_THRESHOLD": 0.0,         # SUE 阈值（只保留超预期的）
    "FORECAST_UP_THRESHOLD": 0.10,  # 盈利预测上调比例阈值
    "ANALYST_COVERAGE_MIN": 5,     # 最少分析师覆盖数
    # 市场状态
    "REGIME_ENABLED": True,
    "REGIME_MA_PERIOD": 20,
    "REGIME_SLOPE_DAYS": 5,
    "REGIME_BULL_ALLOC": 1.0,
    "REGIME_SIDEWAYS_ALLOC": 0.7,
    "REGIME_BEAR_ALLOC": 0.3,
}


def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None, params=None):
    """
    计算 v32 分析师预期因子

    注意：此因子依赖外部数据（一致预期/分析师覆盖），
    当前实现使用代理变量（基于行情数据近似），
    后续接入 Tushare 一致预期接口后替换。

    返回 dict:
        sue_proxy, forecast_up_proxy, analyst_coverage_proxy,
        analyst_composite, mom_5, pv_corr_20
    """
    eps = 1e-10
    p = {**DEFAULT_PARAMS, **(params or {})}

    # ── 代理因子 1：SUE 代理（用盈利超预期近似） ──
    # 实际 SUE = (实际净利润 - 一致预期) / 标准差
    # 代理：用近期营收增速超历史均值来近似
    returns = close_panel.pct_change()
    rev_proxy = amount_panel.pct_change(20)  # 成交额变化代理营收增速
    rev_mean = rev_proxy.rolling(60).mean()
    rev_std = rev_proxy.rolling(60).std()
    sue_proxy = (rev_proxy - rev_mean) / (rev_std + eps)

    # ── 代理因子 2：盈利预测上调代理 ──
    # 实际：近 3 个月分析师调高 EPS 的比例
    # 代理：价格动量 + 成交量同步放大（看多情绪上升）
    mom_5 = close_panel.pct_change(5)
    vol_up = volume_panel.pct_change(5)
    forecast_up_proxy = mom_5 * vol_up  # 价涨量增 = 看多情绪上升

    # ── 代理因子 3：分析师异常覆盖代理 ──
    # 实际：回归取残差剔除市值/换手率影响
    # 代理：换手率异常低但收益异常高（被低估但关注度低）
    avg_vol = volume_panel.rolling(20).mean()
    vol_ratio = volume_panel / (avg_vol + eps)
    ret_vs_vol = returns.rolling(5).mean() / (vol_ratio + eps)
    analyst_coverage_proxy = -ret_vs_vol  # 低换手+高收益 = 异常覆盖信号

    # ── 分析师综合因子 ──
    # 等权合成三个代理因子（标准化后）
    def _zscore(df):
        m = df.mean(axis=1)
        s = df.std(axis=1)
        return (df.sub(m, axis=0)).div(s + eps, axis=0)

    sue_z = _zscore(sue_proxy)
    forecast_z = _zscore(forecast_up_proxy)
    coverage_z = _zscore(analyst_coverage_proxy)

    analyst_composite = (sue_z + forecast_z + coverage_z) / 3.0

    # ── 保留 v27 核心因子用于辅助过滤 ──
    vol_5 = volume_panel.rolling(5).mean()
    vr = vol_5 / (volume_panel.rolling(20).mean() + eps)

    def _pcorr(window):
        rm = returns.rolling(window).mean()
        vrm = vr.rolling(window).mean()
        cov = ((returns - rm) * (vr - vrm)).rolling(window).mean()
        return cov / (returns.rolling(window).std() * vr.rolling(window).std() + eps)

    pv_corr_20 = _pcorr(20)

    return {
        'sue_proxy': sue_proxy,
        'forecast_up_proxy': forecast_up_proxy,
        'analyst_coverage_proxy': analyst_coverage_proxy,
        'analyst_composite': analyst_composite,
        'mom_5': mom_5,
        'pv_corr_20': pv_corr_20,
    }


def select_stocks_v32(factors, date, current_holdings=None, params=None):
    """
    v32 选股：分析师预期因子 + 动量辅助

    参数:
        factors: dict — calc_factors() 返回
        date: Timestamp — 选股日期
        current_holdings: dict — 当前持仓（可选）
        params: dict — 覆盖默认参数

    返回:
        list[(code, score)] — 按评分降序排列
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    analyst_weight = p["ANALYST_WEIGHT"]
    sue_threshold = p["SUE_THRESHOLD"]

    if date not in factors['analyst_composite'].index:
        return []

    ac = factors['analyst_composite'].loc[date].dropna()
    cands = []

    for code in ac.index:
        score_ac = ac[code]
        if np.isnan(score_ac):
            continue

        # 分析师综合因子评分（归一化到 0-10 范围）
        score = score_ac * 10  # 放大到可比较范围

        # 动量辅助过滤：mom_5 需 > 0（不要求 v27 那么高）
        if date in factors['mom_5'].index and code in factors['mom_5'].columns:
            m5 = factors['mom_5'].loc[date, code]
            if not pd.isna(m5) and m5 <= 0:
                continue  # 排除下跌趋势

        # 价量共振辅助过滤
        if date in factors['pv_corr_20'].index and code in factors['pv_corr_20'].columns:
            pv20 = factors['pv_corr_20'].loc[date, code]
            if not pd.isna(pv20) and pv20 < -0.5:
                continue  # 排除量价严重背离

        # SUE 加分
        if date in factors['sue_proxy'].index and code in factors['sue_proxy'].columns:
            sue = factors['sue_proxy'].loc[date, code]
            if not pd.isna(sue) and sue > sue_threshold:
                score += 2.0 * analyst_weight

        # 盈利预测上调加分
        if date in factors['forecast_up_proxy'].index and code in factors['forecast_up_proxy'].columns:
            fu = factors['forecast_up_proxy'].loc[date, code]
            if not pd.isna(fu) and fu > 0:
                score += 1.5 * analyst_weight

        cands.append((code, score))

    cands.sort(key=lambda x: x[1], reverse=True)

    if current_holdings:
        cands = [(c, s) for c, s in cands if c not in current_holdings]

    return cands
