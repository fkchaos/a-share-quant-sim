#!/usr/bin/env python3
"""
scripts/strategies/v44_flow_momentum.py — v44 资金流+动量策略
====================================================
基于A股资金流效应（v39d中fund_flow权重最高0.15），结合动量+质量过滤。

核心逻辑：
- 资金流（fund_flow）：5日资金净流入，权重最高0.35
- 动量（mom_5）：5日动量，权重0.25
- 质量过滤：低波动率（20日vol<4%）
- 规模：中小盘偏好（size_factor）
- 换手率：适度换手

与v39d的区别：
1. 加入低波动率过滤（v39d没有vol过滤）
2. 资金流权重从0.15提升到0.35
3. 加入换手率质量评分

版本历史：
- v44a: 初始版，资金流+动量+低波
"""
import pandas as pd
import numpy as np

DEFAULT_PARAMS = {
    # ── 风控参数 ──
    "STOP_LOSS": -0.05,
    "TAKE_PROFIT": 0.10,
    "HOLD_DAYS_MAX": 5,
    "HOLD_DAYS_EXTEND": 7,
    "HOLD_DAYS_EXTEND_PNL": 0.03,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.20,
    "MAX_HOLDINGS": 8,
    "COOLDOWN_DAYS": 0,
    "MAX_SAME_PREFIX": 0,

    # ── 选股池参数 ──
    "MARKET_CAP_MIN": 0,
    "MARKET_CAP_MAX": float('inf'),
    "EXCLUDE_ST": True,
    "EXCLUDE_NEW": True,
    "EXCLUDE_LIMIT_UP": True,
    "EXCLUDE_SUSPENDED": True,

    # ── 调仓参数 ──
    "REBALANCE_DAY": "daily",
    "MONTHS_EMPTY": [],

    # ── 评分权重 ──
    "W_FLOW": 0.35,               # 资金流（核心）
    "W_MOM": 0.25,                # 动量
    "W_SIZE": 0.15,               # 规模（小盘）
    "W_TURNOVER": 0.10,           # 换手率
    "W_GAP": 0.05,                # 跳空缺口
    "W_ILLIQ": 0.10,              # 非流动性

    # ── 因子参数 ──
    "VOLATILITY_PERIOD": 20,
    "VOLATILITY_MAX": 0.04,       # 日波动率上限4%（年化≈63%）
    "FLOW_CLIP_MAX": 3.0,         # 资金流截断3倍
    "MOM_THRESHOLD": 0.03,        # 动量门槛3%
    "TURNOVER_CLIP_MAX": 0.05,
    "SIZE_CLIP_MAX": 5e10,
}


def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None, params=None, extra_data=None):
    """
    v44 因子计算：资金流+动量+质量
    """
    p = params or {}
    factors = {}

    # ── 因子1: mom_5（5日动量）──
    if close_panel is not None and len(close_panel) > 0:
        factors['mom_5'] = close_panel.pct_change(periods=5)

    # ── 因子2: fund_flow（资金流 = 价量共振强度）──
    # 资金流 = 涨幅 × 换手率（衡量资金流入强度）
    if close_panel is not None and volume_panel is not None and len(close_panel) > 0:
        mom_5 = close_panel.pct_change(periods=5)
        # 用成交量变化近似资金流
        vol_change = volume_panel.pct_change(periods=5)
        # 资金流 = 动量 × 成交量变化（价量共振）
        factors['fund_flow'] = mom_5 * vol_change.clip(lower=-2, upper=3)

    # ── 因子3: volatility（20日波动率，越低越好）──
    if close_panel is not None and len(close_panel) > 0:
        daily_ret = close_panel.pct_change(periods=1)
        factors['volatility'] = daily_ret.rolling(
            window=p.get('VOLATILITY_PERIOD', 20), min_periods=10
        ).std()

    # ── 因子4: turnover_rate（换手率）──
    float_shares_map = p.get('float_shares_map', {})
    if volume_panel is not None and len(volume_panel) > 0:
        if float_shares_map:
            float_series = pd.Series(float_shares_map).reindex(volume_panel.columns).fillna(0)
            factors['turnover_rate'] = volume_panel / float_series.replace(0, np.nan)
        else:
            if amount_panel is not None and close_panel is not None:
                factors['turnover_rate'] = volume_panel * close_panel / amount_panel.replace(0, np.nan)
            else:
                factors['turnover_rate'] = volume_panel

    # ── 因子5: size_factor（市值，越小越好）──
    if close_panel is not None and len(close_panel) > 0:
        if float_shares_map:
            float_series = pd.Series(float_shares_map).reindex(close_panel.columns).fillna(0)
            factors['size_factor'] = close_panel.mul(float_series, axis=1)
        else:
            if volume_panel is not None:
                factors['size_factor'] = close_panel * np.sqrt(volume_panel)
            else:
                factors['size_factor'] = close_panel

    # ── 因子6: gap_ratio（跳空缺口 = 开盘跳空 / 昨日收盘）──
    if open_panel is not None and close_panel is not None and len(close_panel) > 0:
        prev_close = close_panel.shift(1)
        factors['gap_ratio'] = (open_panel - prev_close) / prev_close.replace(0, np.nan)

    # ── 因子7: illiq（非流动性 = |收益率| / 成交额）──
    if close_panel is not None and amount_panel is not None and len(close_panel) > 0:
        daily_ret = close_panel.pct_change(periods=1)
        factors['illiq'] = daily_ret.abs() / amount_panel.replace(0, np.nan)

    return factors


def select_stocks_v44(factors, date, current_holdings=None, params=None,
                      sold_recently=None, extra_data=None):
    """
    v44 选股：资金流+动量+低波质量
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    # ── 获取候选池 ──
    if 'mom_5' not in factors or date not in factors['mom_5'].index:
        return []

    mom = factors['mom_5'].loc[date].dropna()

    # ── 硬筛选 ──
    # 1. 动量 > 3%（有明显上涨趋势）
    candidates = list(mom[mom > p.get('MOM_THRESHOLD', 0.03)].index)

    # 2. 低波动过滤（日波动率 < 4%）
    if 'volatility' in factors and date in factors['volatility'].index:
        vol = factors['volatility'].loc[date].dropna()
        candidates = [c for c in candidates if c in vol.index and vol[c] <= p.get('VOLATILITY_MAX', 0.04)]

    # ── 排除当前持仓 ──
    if current_holdings:
        candidates = [c for c in candidates if c not in current_holdings]
    if sold_recently:
        candidates = [c for c in candidates if c not in sold_recently]

    if not candidates:
        return []

    # ── 评分 ──
    scores = pd.Series(0.0, index=candidates)

    # 资金流因子（核心）
    if p.get("W_FLOW", 0) > 0 and 'fund_flow' in factors:
        flow_scores = _score_column(factors, date, 'fund_flow', clip_min=0,
                                     clip_max=p.get("FLOW_CLIP_MAX", 3.0))
        scores += flow_scores.reindex(candidates).fillna(0) * p["W_FLOW"]

    # 动量因子
    if p.get("W_MOM", 0) > 0 and 'mom_5' in factors:
        mom_scores = _score_column(factors, date, 'mom_5', clip_min=0)
        scores += mom_scores.reindex(candidates).fillna(0) * p["W_MOM"]

    # 规模因子（越小越好）
    if p.get("W_SIZE", 0) > 0 and 'size_factor' in factors:
        cap_scores = _score_column(factors, date, 'size_factor',
                                    clip_min=0, clip_max=p["SIZE_CLIP_MAX"])
        scores += (1 - cap_scores.reindex(candidates).fillna(0)) * p["W_SIZE"]

    # 换手率
    if p.get("W_TURNOVER", 0) > 0 and 'turnover_rate' in factors:
        tr_scores = _score_column(factors, date, 'turnover_rate',
                                   clip_min=0, clip_max=p["TURNOVER_CLIP_MAX"])
        scores += tr_scores.reindex(candidates).fillna(0) * p["W_TURNOVER"]

    # 跳空缺口
    if p.get("W_GAP", 0) > 0 and 'gap_ratio' in factors:
        gap_scores = _score_column(factors, date, 'gap_ratio', clip_min=0, clip_max=0.05)
        scores += gap_scores.reindex(candidates).fillna(0) * p["W_GAP"]

    # 非流动性
    if p.get("W_ILLIQ", 0) > 0 and 'illiq' in factors:
        illiq_scores = _score_column(factors, date, 'illiq', clip_min=0)
        scores += illiq_scores.reindex(candidates).fillna(0) * p["W_ILLIQ"]

    # ── 排序取前N ──
    scores = scores.sort_values(ascending=False)
    n_buy = min(p["MAX_DAILY_BUY"], len(scores))
    selected = scores.index[:n_buy]

    return [(code, scores[code]) for code in selected]


def _score_column(factors, date, col, clip_min=None, clip_max=None):
    """
    将因子值归一化到 [0, 1]（clip 后 percentile 归一化）
    """
    if col not in factors or date not in factors[col].index:
        return pd.Series(dtype=float)

    s = factors[col].loc[date].dropna()
    if len(s) == 0:
        return pd.Series(dtype=float)

    if clip_min is not None:
        s = s.clip(lower=clip_min)
    if clip_max is not None:
        s = s.clip(upper=clip_max)

    if len(s) > 1:
        s = s.rank(pct=True, method='average')
    else:
        s = pd.Series(0.5, index=s.index)

    return s
