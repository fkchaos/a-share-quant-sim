#!/usr/bin/env python3
"""v75n: v75j + 放开科创板/北交所

基于v75j，区别是选股不排除688/689。
v75j通过v75a选股，v75a内部硬编码排除688/689。
v75n自己实现选股逻辑，跳过该过滤。

因子和广度过滤逻辑与v75j完全一致。
"""

import numpy as np
import pandas as pd
from scripts.strategies.v75j_liquidity_only import (
    calc_factors_v75j, _calc_breadth, _load_tech_codes,
    DEFAULT_PARAMS as V75J_PARAMS,
)

DEFAULT_PARAMS = dict(V75J_PARAMS)


def calc_factors_v75n(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel=None, extra_data=None):
    """因子计算与v75j完全相同（流动性单因子）"""
    return calc_factors_v75j(close_panel, volume_panel, amount_panel,
                             high_panel, low_panel, open_panel, extra_data)


def select_stocks_v75n(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """选股：流动性因子排序 + 广度过滤，不排除科创板(688/689)

    与v75j的区别：v75j调用v75a选股（内含688过滤），
    v75n直接排名，688/689参与排序。
    """
    if params is None:
        params = DEFAULT_PARAMS

    # 广度过滤（与v75j相同）
    breadth = _calc_breadth(close_panel, date, params)
    high_thresh = params.get("BREADTH_HIGH", 0.50)
    low_thresh = params.get("BREADTH_LOW", 0.30)

    if breadth < low_thresh:
        return []

    n = params.get('MAX_HOLDINGS', 3)
    display_n = 10 if return_all else n

    if isinstance(factors, dict):
        scores = list(factors.values())[0]
    else:
        scores = factors

    # 线性减仓（中间区域）
    if breadth < high_thresh:
        n = max(1, int(n * breadth / high_thresh))

    # 不过滤688/689 —— 这是v75n与v75j的唯一区别
    candidates = scores.head(max(n * 3, display_n)).index.tolist()
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [c for c in candidates if c not in held]

    return [(code, round(scores.get(code, 0), 4)) for code in buy_list[:display_n]]
