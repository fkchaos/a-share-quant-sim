#!/usr/bin/env python3
"""v75l: v75j + QQQ + 广度双择时（OR组合）

在v75j基础上叠加两层择时：
1. QQQ前一日跌>3% → 不开新仓
2. 广度<30% → 不开新仓（v75j原有逻辑）
3. QQQ前一日跌1~3% → MAX_HOLDINGS减半

两个信号OR组合：任一触发都执行对应动作。
与v75k的区别：v75k只看QQQ，v75l同时利用广度信号的独立增量。
"""

import numpy as np
import pandas as pd
import os

from scripts.strategies.v75j_liquidity_only import (
    calc_factors_v75j, select_stocks_v75j,
    _calc_breadth, _load_tech_codes
)
from scripts.strategies.v75k_nasdaq_timing import _load_qqq_returns, _get_qqq_signal

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
    
    # 广度过滤（与v75j相同）
    "BREADTH_MA": 20,
    "BREADTH_HIGH": 0.50,
    "BREADTH_LOW": 0.30,
    
    # 选股因子（与v75j相同）
    "W_BREAKOUT": 0.0,
    "W_VOL_SURGE": 0.0,
    "W_LIQUIDITY": 1.0,
    
    # 纳斯达克择时参数
    "QQQ_GATE_STRONG": -0.03,
    "QQQ_GATE_MILD": -0.01,
}


def select_stocks_v75l(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """选股：v75j + QQQ + 广度双择时（OR组合）
    
    逻辑：
    1. QQQ跌>3% → block（不开仓）
    2. 广度<30% → block（不开仓，v75j原有）
    3. QQQ跌1~3% OR 广度<50% → reduce（减半持仓）
    4. 都不触发 → normal（v75j原逻辑）
    """
    if params is None:
        params = DEFAULT_PARAMS
    
    # ── 第一层：QQQ信号 ──
    qqq_signal = _get_qqq_signal(date)
    
    # ── 第二层：广度信号 ──
    breadth = _calc_breadth(close_panel, date, params)
    high_thresh = params.get("BREADTH_HIGH", 0.50)
    low_thresh = params.get("BREADTH_LOW", 0.30)
    
    # ── OR组合逻辑 ──
    # block条件：QQQ强跌 OR 广度极低
    if qqq_signal == 'block' or breadth < low_thresh:
        return []
    
    # reduce条件：QQQ弱跌 OR 广度中低
    need_reduce = (qqq_signal == 'reduce') or (breadth < high_thresh)
    
    max_hold = params.get("MAX_HOLDINGS", 3)
    if need_reduce:
        # 取两者中更严格的
        if qqq_signal == 'reduce':
            max_hold = max(1, max_hold // 2)
        if breadth < high_thresh:
            max_hold = min(max_hold, max(1, int(max_hold * breadth / high_thresh)))
    
    # 正常区域
    if need_reduce:
        p = dict(params)
        p["MAX_HOLDINGS"] = max_hold
        return select_stocks_v75j(factors, date, close_panel, volume_panel, amount_panel,
                                  high_panel, low_panel, open_panel, current_holdings,
                                  p, sold_recently=sold_recently, return_all=return_all)
    
    # 全部正常，走v75j原逻辑
    return select_stocks_v75j(factors, date, close_panel, volume_panel, amount_panel,
                              high_panel, low_panel, open_panel, current_holdings,
                              params, sold_recently=sold_recently, return_all=return_all)


def calc_factors_v75l(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel=None, extra_data=None):
    """计算v75l因子（与v75j完全相同）"""
    return calc_factors_v75j(close_panel, volume_panel, amount_panel,
                             high_panel, low_panel, open_panel, extra_data)
