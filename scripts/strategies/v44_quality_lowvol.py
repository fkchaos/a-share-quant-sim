#!/usr/bin/env python3
"""
scripts/strategies/v44_quality_lowvol.py — v44 质量+低波小市值策略
====================================================
基于A股质量因子和低波动率效应，聚焦中小盘（20-100亿）。

核心逻辑：
- 质量因子：ROE/盈利质量（用动量+换手率质量代理）
- 低波动率：20日收益波动率越低越好
- 规模因子：市值越小越好（20-100亿区间）
- 风控：-8%止损，15%止盈，最长持有10天

参考：
- 2024年低波动因子表现优秀（防御年）
- 2025年质量+规模因子共振（进攻年）
- 2026年市场风格不明，质量+低波组合攻守兼备

版本历史：
- v44a: 初始版本，质量+低波+规模三因子
"""
import pandas as pd
import numpy as np

DEFAULT_PARAMS = {
    # ── 风控参数 ──
    "STOP_LOSS": -0.08,
    "TAKE_PROFIT": 0.15,          # 15%止盈
    "HOLD_DAYS_MAX": 10,           # 最长持有10天
    "HOLD_DAYS_EXTEND": 5,         # 盈利可延长5天
    "HOLD_DAYS_EXTEND_PNL": 0.03,  # 盈利3%可延长
    "MAX_DAILY_BUY": 3,            # 每天最多买3只
    "MAX_POSITION": 0.125,         # 单只上限12.5%
    "MAX_HOLDINGS": 8,             # 最多持有8只
    "COOLDOWN_DAYS": 0,
    "MAX_SAME_PREFIX": 0,

    # ── 选股池参数 ──
    "MARKET_CAP_MIN": 1e10,         # 最小市值100亿
    "MARKET_CAP_MAX": 5e11,         # 最大市值500亿（中大盘）
    "EXCLUDE_ST": True,
    "EXCLUDE_NEW": True,           # 排除上市<60天
    "EXCLUDE_LIMIT_UP": True,      # 排除涨停
    "EXCLUDE_SUSPENDED": True,     # 排除停牌

    # ── 调仓参数 ──
    "REBALANCE_DAY": "daily",       # 每日调仓（灵活应对）
    "MONTHS_EMPTY": [],            # 不空仓（质量策略不需要空仓）

    # ── 评分权重 ──
    "W_QUALITY": 0.35,             # 质量因子（ROE代理）
    "W_LOWVOL": 0.30,             # 低波动率
    "W_SIZE": 0.20,               # 规模（越小越好）
    "W_TURNOVER": 0.15,           # 换手率质量

    # ── 因子参数 ──
    "VOLATILITY_PERIOD": 20,       # 20日波动率
    "QUALITY_PERIOD": 20,          # 20日动量（代理质量/趋势）
    "TURNOVER_CLIP_MAX": 0.08,    # 换手率截断8%
    "SIZE_CLIP_MAX": 1e10,        # 市值截断100亿
    "VOLATILITY_CLIP_MAX": 0.05,  # 日波动率截断5%（年化≈80%）
}


def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None, params=None, extra_data=None):
    """
    v44 因子计算：质量+低波+规模

    参数：
        close_panel: DataFrame — 收盘价面板（date × code）
        volume_panel: DataFrame — 成交量面板
        amount_panel: DataFrame — 成交额面板
        high_panel: DataFrame — 最高价面板
        low_panel: DataFrame — 最低价面板
        open_panel: DataFrame — 开盘价面板（可选）
        params: dict — 策略参数（含 float_shares_map 等）
        extra_data: dict — 额外数据

    返回：
        factors: dict — {factor_name: {date: Series(index=code)}}
    """
    p = params or {}
    factors = {}

    # ── 因子1: quality（质量因子，用20日动量代理）──
    # 20日动量代表中期趋势质量，比5日动量更稳定
    if close_panel is not None and len(close_panel) > 0:
        factors['quality'] = close_panel.pct_change(periods=p.get('QUALITY_PERIOD', 20))

    # ── 因子2: volatility（20日波动率，越低越好）──
    if close_panel is not None and len(close_panel) > 0:
        # 日收益率
        daily_ret = close_panel.pct_change(periods=1)
        # 20日滚动波动率（标准差）
        vol = daily_ret.rolling(window=p.get('VOLATILITY_PERIOD', 20), min_periods=10).std()
        factors['volatility'] = vol

    # ── 因子3: turnover_rate（换手率 = volume / float_shares）──
    float_shares_map = p.get('float_shares_map', {})
    if volume_panel is not None and len(volume_panel) > 0:
        if float_shares_map:
            float_series = pd.Series(float_shares_map).reindex(volume_panel.columns).fillna(0)
            # 腾讯 volume 单位是股，float_shares 单位也是股
            turnover = volume_panel / float_series.replace(0, np.nan)
            factors['turnover_rate'] = turnover
        else:
            # 无 float_shares：用近似换手率
            if amount_panel is not None and close_panel is not None:
                approx_turnover = volume_panel * close_panel / amount_panel.replace(0, np.nan)
                factors['turnover_rate'] = approx_turnover
            else:
                factors['turnover_rate'] = volume_panel

    # ── 因子4: market_cap（市值 = close * float_shares）──
    if close_panel is not None and len(close_panel) > 0:
        if float_shares_map:
            float_series = pd.Series(float_shares_map).reindex(close_panel.columns).fillna(0)
            factors['market_cap'] = close_panel.mul(float_series, axis=1)
        else:
            # 无 float_shares：用 close * sqrt(volume) 作为市值近似排序
            if volume_panel is not None:
                factors['market_cap'] = close_panel * np.sqrt(volume_panel)
            else:
                factors['market_cap'] = close_panel

    return factors


def select_stocks_v44(factors, date, current_holdings=None, params=None,
                      sold_recently=None, extra_data=None):
    """
    v44 选股：质量+低波+规模

    输入：
        factors: dict — 因子数据 {factor_name: {date: Series}}
        date: 当前日期
        current_holdings: list — 当前持仓代码
        params: dict — 策略参数
        sold_recently: set — 近期卖出的股票
        extra_data: dict — 额外数据

    输出：
        list of (code, score) — 选中的股票和评分
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    # ── 获取候选池 ──
    if 'quality' not in factors or date not in factors['quality'].index:
        return []

    # ── 硬筛选 ──
    # 1. 动量 < -3%（超跌，等待反弹）
    quality = factors['quality'].loc[date].dropna()
    mom = quality
    mom_filtered = mom[mom < -0.03]
    candidates = list(mom_filtered.index)

    # 2. 排除高波动（日波动率>5%）——保留低波质量股
    if 'volatility' in factors and date in factors['volatility'].index:
        vol = factors['volatility'].loc[date].dropna()
        candidates = [c for c in candidates if c in vol.index and vol[c] <= p.get('VOLATILITY_CLIP_MAX', 0.05)]

    # ── 市值过滤 ──
    if 'market_cap' in factors and date in factors['market_cap'].index:
        cap = factors['market_cap'].loc[date].dropna()
        candidates = [c for c in candidates if c in cap.index]
        candidates = [c for c in candidates
                      if p["MARKET_CAP_MIN"] <= cap[c] <= p["MARKET_CAP_MAX"]]

    # ── 排除当前持仓 ──
    if current_holdings:
        candidates = [c for c in candidates if c not in current_holdings]
    if sold_recently:
        candidates = [c for c in candidates if c not in sold_recently]

    if not candidates:
        return []

    # ── 评分 ──
    scores = pd.Series(0.0, index=candidates)

    # 质量因子（20日动量，越大越好）
    if p.get("W_QUALITY", 0) > 0 and 'quality' in factors:
        q_scores = _score_column(factors, date, 'quality', clip_min=0)
        scores += q_scores.reindex(candidates).fillna(0) * p["W_QUALITY"]

    # 低波动率（越小越好，所以用负向评分）
    if p.get("W_LOWVOL", 0) > 0 and 'volatility' in factors:
        vol_scores = _score_column(factors, date, 'volatility', clip_min=0,
                                    clip_max=p.get("VOLATILITY_CLIP_MAX", 0.05))
        # 反向：波动越小分越高 → 用 (1 - clip_normalize)
        scores += (1 - vol_scores.reindex(candidates).fillna(0)) * p["W_LOWVOL"]

    # 规模因子（越小越好）
    if p.get("W_SIZE", 0) > 0 and 'market_cap' in factors:
        cap_scores = _score_column(factors, date, 'market_cap',
                                    clip_min=0, clip_max=p["SIZE_CLIP_MAX"])
        # 反向：市值越小分越高
        scores += (1 - cap_scores.reindex(candidates).fillna(0)) * p["W_SIZE"]

    # 换手率质量（适中最好：0.5%-5%区间得分高）
    if p.get("W_TURNOVER", 0) > 0 and 'turnover_rate' in factors:
        tr_scores = _score_column(factors, date, 'turnover_rate',
                                   clip_min=0, clip_max=p["TURNOVER_CLIP_MAX"])
        scores += tr_scores.reindex(candidates).fillna(0) * p["W_TURNOVER"]

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

    # Percentile rank 归一化到 [0, 1]
    if len(s) > 1:
        s = s.rank(pct=True, method='average')
    else:
        s = pd.Series(0.5, index=s.index)

    return s
