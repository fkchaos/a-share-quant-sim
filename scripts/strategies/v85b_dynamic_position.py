#!/usr/bin/env python3
"""v85b: 广度动态仓位管理 + v75n选股

基于v75n（含科创板+流动性因子+广度过滤），核心改进：
- v75n的广度择时是固定阈值（>0.30交易，<0.30空仓，中间线性减仓）
- v85b改为连续函数：MAX_HOLDINGS = f(breadth)，广度越高持仓越多

设计逻辑：
- 广度>0.60: 满仓3只（高conviction）
- 广度0.40~0.60: 线性 1~3只
- 广度0.20~0.40: 线性 0~1只（极低仓位试探）
- 广度<0.20: 空仓

这比v75n的二元择时（>0.30交易/<0.30空仓）更精细，
能在高conviction时期集中持仓放大收益。
"""

import numpy as np
import pandas as pd
from scripts.strategies.v75j_liquidity_only import (
    calc_factors_v75j, _calc_breadth, _load_tech_codes
)

DEFAULT_PARAMS = {
    # 风控参数
    "STOP_LOSS": -0.08,
    "TAKE_PROFIT": 0.25,
    "HOLD_DAYS_MAX": 20,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.35,
    "MAX_HOLDINGS": 3,
    "REBALANCE_DAYS": 10,
    "MAX_STOCK_PRICE": 300,

    # 广度动态仓位参数
    "BREADTH_MA": 20,
    "BREADTH_FULL": 0.60,    # 满仓阈值
    "BREADTH_HALF": 0.40,    # 半仓阈值
    "BREADTH_MIN": 0.20,     # 最低阈值（低于此空仓）

    # 选股层参数
    "W_BREAKOUT": 0.0,
    "W_VOL_SURGE": 0.0,
    "W_LIQUIDITY": 1.0,
}


def calc_factors_v85b(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel=None, extra_data=None):
    """因子计算与v75j相同"""
    return calc_factors_v75j(close_panel, volume_panel, amount_panel,
                             high_panel, low_panel, open_panel, extra_data)


def _dynamic_holdings(breadth, params):
    """根据广度动态计算持仓数量
    
    广度 → 持仓数映射：
    <0.20 → 0（空仓）
    0.20~0.40 → 0~1（线性）
    0.40~0.60 → 1~3（线性）
    >0.60 → 3（满仓）
    """
    b_full = params.get("BREADTH_FULL", 0.60)
    b_half = params.get("BREADTH_HALF", 0.40)
    b_min = params.get("BREADTH_MIN", 0.20)
    max_n = params.get("MAX_HOLDINGS", 3)

    if breadth < b_min:
        return 0
    elif breadth < b_half:
        # 0~1 线性
        return max(1, int(max_n * (breadth - b_min) / (b_half - b_min)))
    elif breadth < b_full:
        # 1~3 线性
        return max(1, int(max_n * (breadth - b_half) / (b_full - b_half)))
    else:
        return max_n


def select_stocks_v85b(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """选股：流动性因子 + 广度动态仓位"""
    if params is None:
        params = DEFAULT_PARAMS

    # 广度计算
    breadth = _calc_breadth(close_panel, date, params)

    # 动态持仓数
    n = _dynamic_holdings(breadth, params)
    display_n = 10 if return_all else max(n, 3)

    if n == 0:
        return []

    # 获取流动性因子得分
    if isinstance(factors, dict):
        scores = list(factors.values())[0]
    else:
        scores = factors

    candidates = scores.head(max(n * 3, display_n)).index.tolist()
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [c for c in candidates if c not in held]

    return [(code, round(scores.get(code, 0), 4)) for code in buy_list[:display_n]]
