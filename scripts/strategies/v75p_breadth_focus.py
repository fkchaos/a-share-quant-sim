#!/usr/bin/env python3
"""v75p: 广度择时信号作为独立因子验证

设计思路：
v75n高收益fold的共同特征是广度高（科技板块整体上涨）。
v75p将广度信号从择时层提升为选股因子——广度高时多选，广度低时少选或不选。

与v75j/v75n的区别：
- v75j/v75n: 广度只用于择时（>低阈值才交易，中间区域线性减仓）
- v75p: 广度同时影响持仓数量和选股偏好——广度越高，持仓越多，越敢选高弹性股

实际上v75j的广度择时已经做了持仓数量调整（breadth < high_thresh时n *= breadth/high_thresh），
v75p进一步：广度低于中位数时完全不交易（更严格的择时），高于中位数时满仓。

因子仍然是流动性单因子，区别在择时参数更激进。
"""

import numpy as np
import pandas as pd
from scripts.strategies.v75j_liquidity_only import (
    calc_factors_v75j, _calc_breadth, _load_tech_codes
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

    # 择时层参数（更激进的广度择时）
    "BREADTH_MA": 20,
    "BREADTH_HIGH": 0.50,
    "BREADTH_LOW": 0.40,   # 比v75j的0.30更高，更严格

    # 选股层参数
    "W_BREAKOUT": 0.0,
    "W_VOL_SURGE": 0.0,
    "W_LIQUIDITY": 1.0,
}


def calc_factors_v75p(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel=None, extra_data=None):
    """因子计算与v75j相同"""
    return calc_factors_v75j(close_panel, volume_panel, amount_panel,
                             high_panel, low_panel, open_panel, extra_data)


def select_stocks_v75p(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """选股：流动性因子 + 更严格的广度择时

    与v75j的区别：BREADTH_LOW从0.30提高到0.40，
    广度低于0.40时不交易（v75j是0.30），减少熊市持仓。
    """
    if params is None:
        params = DEFAULT_PARAMS

    # 广度过滤（择时层）
    breadth = _calc_breadth(close_panel, date, params)
    high_thresh = params.get("BREADTH_HIGH", 0.50)
    low_thresh = params.get("BREADTH_LOW", 0.40)

    if breadth < low_thresh:
        return []

    # 获取流动性因子得分
    if isinstance(factors, dict):
        scores = list(factors.values())[0]
    else:
        scores = factors

    n = params.get('MAX_HOLDINGS', 3)
    display_n = 10 if return_all else n

    # 线性减仓（中间区域）
    if breadth < high_thresh:
        n = max(1, int(n * breadth / high_thresh))

    candidates = scores.head(max(n * 3, display_n)).index.tolist()
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [c for c in candidates if c not in held]

    return [(code, round(scores.get(code, 0), 4)) for code in buy_list[:display_n]]
