#!/usr/bin/env python3
"""v84a: v75n + 纯科技板块过滤

基于v75n（含科创板+流动性因子+广度过滤），增加一层约束：
选股限定在科技板块（电子/计算机/通信/传媒）内，按流动性排序。

设计思路：
v75n高收益fold的买入100%是科创板科技龙头（寒武纪/中芯/海光），
v75n在全市场选流动性最高的，可能选到非科技股分散收益。
v84a限定科技板块内选，集中持仓科技+科创板。

与v75a/v75j的区别：
- v75a/v75j: 科技板块过滤+排除科创板
- v75n: 不限板块+含科创板
- v84a: 限定科技板块+含科创板
"""

import numpy as np
import pandas as pd
from scripts.strategies.v75j_liquidity_only import (
    calc_factors_v75j, _calc_breadth, _load_tech_codes, TECH_SECTORS
)

DEFAULT_PARAMS = {
    # 风控参数（与v75j相同）
    "STOP_LOSS": -0.08,
    "TAKE_PROFIT": 0.25,
    "HOLD_DAYS_MAX": 20,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.35,
    "MAX_HOLDINGS": 3,
    "REBALANCE_DAYS": 10,
    "MAX_STOCK_PRICE": 300,

    # 择时层参数（广度过滤，与v75j相同）
    "BREADTH_MA": 20,
    "BREADTH_HIGH": 0.50,
    "BREADTH_LOW": 0.30,

    # 选股层参数（流动性因子，与v75j相同）
    "W_BREAKOUT": 0.0,
    "W_VOL_SURGE": 0.0,
    "W_LIQUIDITY": 1.0,
}


def calc_factors_v84a(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel=None, extra_data=None):
    """计算v84a因子：流动性因子（与v75j相同，但选股时限定科技板块）"""
    return calc_factors_v75j(close_panel, volume_panel, amount_panel,
                             high_panel, low_panel, open_panel, extra_data)


def select_stocks_v84a(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """选股：流动性因子排序 + 科技板块限定 + 广度过滤

    与v75n的区别：v75n在全市场选流动性最高的，v84a限定科技板块内选。
    """
    if params is None:
        params = DEFAULT_PARAMS

    # 广度过滤（择时层）
    breadth = _calc_breadth(close_panel, date, params)
    high_thresh = params.get("BREADTH_HIGH", 0.50)
    low_thresh = params.get("BREADTH_LOW", 0.30)

    if breadth < low_thresh:
        return []

    # 获取流动性因子得分
    if isinstance(factors, dict):
        scores = list(factors.values())[0]
    else:
        scores = factors

    # 限定科技板块（核心区别：v75n不排除任何板块，v84a只选科技）
    tech_codes = set(_load_tech_codes())
    scores = scores[scores.index.isin(tech_codes)]

    n = params.get('MAX_HOLDINGS', 3)
    display_n = 10 if return_all else n

    # 线性减仓（中间区域）
    if breadth < high_thresh:
        n = max(1, int(n * breadth / high_thresh))

    candidates = scores.head(max(n * 3, display_n)).index.tolist()
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [c for c in candidates if c not in held]

    return [(code, round(scores.get(code, 0), 4)) for code in buy_list[:display_n]]
