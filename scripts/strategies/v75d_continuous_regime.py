#!/usr/bin/env python3
"""v75d: 连续regime仓位乘数
不再二元开关，而是根据趋势强度动态调整仓位比例。
趋势越强 → 仓位越高；趋势越弱 → 仓位越低。
"""

import numpy as np
import pandas as pd
from scripts.strategies.v75a_tech_momentum import calc_factors_v75a, select_stocks_v75a

DEFAULT_PARAMS = {
    "STOP_LOSS": -0.08,
    "TAKE_PROFIT": 0.30,
    "HOLD_DAYS_MAX": 15,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.35,
    "MAX_HOLDINGS": 3,
    "REBALANCE_DAYS": 10,
    # regime参数
    "REGIME_MA": 50,
    "REGIME_SLOPE_DAYS": 10,
    # 连续regime参数
    "REGIME_MAX_MULT": 1.0,    # 趋势最强时仓位乘数
    "REGIME_MIN_MULT": 0.0,    # 趋势最弱时仓位乘数
    "REGIME_SMOOTH": 0.8,      # 平滑系数（0-1，越大越平滑）
}


def _calc_regime_mult(date, close_panel, params):
    """计算连续regime仓位乘数（0-1）
    
    基于两个维度：
    1. 价格相对MA的位置（0-1，越高越强）
    2. 斜率强度（0-1，越陡越强）
    综合后平滑输出。
    """
    import sqlite3
    # 加载科技板块代码
    conn = sqlite3.connect('data/quant_stocks.db')
    rows = conn.execute("SELECT code FROM industry_map WHERE industry IN ('电子','计算机','通信','传媒')").fetchall()
    conn.close()
    tech_codes = [r[0] for r in rows]
    available = [c for c in tech_codes if c in close_panel.columns]
    
    if not available:
        return 0.5  # 默认中性仓位

    # 科技板块等权指数
    sector = close_panel[available].mean(axis=1).dropna()
    if len(sector) < 60:
        return 0.5

    if date not in sector.index:
        return 0.5

    pos = sector.index.get_loc(date)
    if isinstance(pos, slice):
        pos = pos.start

    vals = sector.values

    # 维度1: MA位置分数（0-1）
    ma_period = params.get("REGIME_MA", 50)
    if pos >= ma_period:
        ma_val = vals[pos - ma_period + 1: pos + 1].mean()
        price = vals[pos]
        # 价格在MA上方多远（标准化到0-1）
        # 价格=MA时得分0.5，价格=MA*1.1时得分1.0，价格=MA*0.9时得分0.0
        ma_score = np.clip((price / ma_val - 0.9) / 0.2, 0, 1)
    else:
        ma_score = 0.5

    # 维度2: 斜率分数（0-1）
    slope_days = params.get("REGIME_SLOPE_DAYS", 10)
    if pos >= slope_days:
        slope = (vals[pos] - vals[pos - slope_days]) / vals[pos - slope_days]
        # 斜率标准化：-5%→0, 0%→0.5, +5%→1.0
        slope_score = np.clip((slope + 0.05) / 0.1, 0, 1)
    else:
        slope_score = 0.5

    # 综合分数（MA权重60%，斜率40%）
    raw_mult = ma_score * 0.6 + slope_score * 0.4

    # 平滑（避免频繁切换）
    smooth = params.get("REGIME_SMOOTH", 0.8)
    # 简单平滑：用前5天的均值
    if pos >= 5:
        recent = [vals[pos-j] for j in range(5)]
        recent_ma = np.mean(recent)
        recent_vals = [vals[pos-j] for j in range(5)]
        # 如果最近5天整体向上，加权
        trend = (recent_vals[0] - recent_vals[-1]) / recent_vals[-1] if recent_vals[-1] > 0 else 0
        trend_score = np.clip((trend + 0.05) / 0.1, 0, 1)
        raw_mult = raw_mult * 0.7 + trend_score * 0.3

    # 映射到 [MIN_MULT, MAX_MULT]
    min_m = params.get("REGIME_MIN_MULT", 0.0)
    max_m = params.get("REGIME_MAX_MULT", 1.0)
    return min_m + raw_mult * (max_m - min_m)


def calc_factors_v75d(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel):
    """计算因子：复用v75a"""
    return calc_factors_v75a(close_panel, volume_panel, amount_panel,
                             high_panel, low_panel, open_panel)


def select_stocks_v75d(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """v75d选股：根据regime乘数动态调整选股数量"""
    if params is None:
        params = DEFAULT_PARAMS

    # 计算regime乘数
    mult = _calc_regime_mult(date, close_panel, params)

    # 如果乘数太低（<0.2），不开新仓
    if mult < 0.2:
        return []

    # 根据乘数调整选股数量
    base_count = params.get("MAX_DAILY_BUY", 3)
    adjusted_count = max(1, int(base_count * mult))
    
    # 调整参数
    adjusted_params = dict(params)
    adjusted_params["MAX_DAILY_BUY"] = adjusted_count
    adjusted_params["MAX_POSITION"] = params.get("MAX_POSITION", 0.35) * mult

    return select_stocks_v75a(factors, date, close_panel, volume_panel, amount_panel,
                              high_panel, low_panel, open_panel, current_holdings,
                              adjusted_params, sold_recently=sold_recently)
