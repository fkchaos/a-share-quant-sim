#!/usr/bin/env python3
"""v75j: 流动性因子 + 广度过滤

基于v75f，去掉突破和放量因子（IC<0.03，弱信号），
只保留流动性因子（IC=-0.054，有效）+ 广度过滤。

设计目的：
1. 验证"去掉弱因子后是否更好"
2. 固化因子筛选流程（先IC筛选，再构建策略）

IC分析结果：
- 突破: IC=-0.014, IR=-0.08 → ❌ 不通过（|IC|<0.03）
- 放量: IC=-0.011, IR=-0.06 → ❌ 不通过（|IC|<0.03）
- 流动性: IC=-0.054, IR=-0.31 → ✅ 通过（|IC|>0.03, |IR|>0.3）
- 广度: 择时因子，v75f已验证有效

v75j = 流动性单因子（W_LIQUIDITY=1.0）+ 广度过滤
"""

import numpy as np
import pandas as pd
from scripts.strategies.v75a_tech_momentum import (
    calc_factors_v75a, select_stocks_v75a, 
    _load_industry_map, TECH_INDUSTRIES
)

# 科技板块代码（与v75f相同）
TECH_SECTORS = ['电子', '计算机', '通信', '传媒']
_tech_codes = None


def _load_tech_codes():
    global _tech_codes
    if _tech_codes is not None:
        return _tech_codes
    import sqlite3
    conn = sqlite3.connect('data/quant_stocks.db')
    codes = []
    for sector in TECH_SECTORS:
        rows = conn.execute("SELECT code FROM industry_map WHERE industry=?", (sector,)).fetchall()
        codes.extend([r[0] for r in rows])
    conn.close()
    _tech_codes = list(set(codes))
    return _tech_codes


DEFAULT_PARAMS = {
    # 风控参数（与v75f相同）
    "STOP_LOSS": -0.08,
    "TAKE_PROFIT": 0.25,
    "HOLD_DAYS_MAX": 20,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.35,
    "MAX_HOLDINGS": 3,
    "REBALANCE_DAYS": 10,
    "MAX_STOCK_PRICE": 300,  # 股价上限，超过300的不买
    
    # 择时层参数（广度过滤，与v75f相同）
    "BREADTH_MA": 20,
    "BREADTH_HIGH": 0.50,
    "BREADTH_LOW": 0.30,
    
    # 选股层参数（v75j：只保留流动性因子）
    "W_BREAKOUT": 0.0,    # 去掉（IC=-0.014，不通过）
    "W_VOL_SURGE": 0.0,   # 去掉（IC=-0.011，不通过）
    "W_LIQUIDITY": 1.0,   # 保留（IC=-0.054，通过）
}


def _calc_breadth(close_panel, date, params):
    """计算广度：多少科技股收盘价>MA20（与v75f相同）"""
    codes = _load_tech_codes()
    ma_period = params.get("BREADTH_MA", 20)
    pos = close_panel.index.get_loc(date)
    if isinstance(pos, slice):
        pos = pos.start
    if pos < ma_period:
        return 1.0
    
    above = 0
    total = 0
    for c in codes:
        if c in close_panel.columns:
            arr = close_panel[c].values
            if np.isnan(arr[pos]) or arr[pos] <= 0:
                continue
            total += 1
            ma = np.nanmean(arr[pos-ma_period+1:pos+1])
            if arr[pos] > ma:
                above += 1
    
    return above / total if total > 0 else 1.0


def calc_factors_v75j(close_panel, volume_panel, amount_panel, 
                      high_panel, low_panel, open_panel=None, extra_data=None):
    """计算v75j因子：只用流动性因子（W_LIQUIDITY=1.0）"""
    weights = {
        'W_BREAKOUT': 0.0,
        'W_VOL_SURGE': 0.0,
        'W_LIQUIDITY': 1.0,
    }
    return calc_factors_v75a(close_panel, volume_panel, amount_panel,
                             high_panel, low_panel, open_panel, extra_data,
                             weights=weights)


def select_stocks_v75j(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """选股：流动性因子排序 + 广度过滤"""
    if params is None:
        params = DEFAULT_PARAMS
    
    # 广度过滤（择时层）
    breadth = _calc_breadth(close_panel, date, params)
    high_thresh = params.get("BREADTH_HIGH", 0.50)
    low_thresh = params.get("BREADTH_LOW", 0.30)
    
    if breadth < low_thresh:
        return []
    
    # 线性减仓（中间区域）
    if breadth < high_thresh:
        p = dict(params)
        p["MAX_HOLDINGS"] = max(1, int(params.get("MAX_HOLDINGS", 3) * breadth / high_thresh))
        return select_stocks_v75a(factors, date, close_panel, volume_panel, amount_panel,
                                  high_panel, low_panel, open_panel, current_holdings,
                                  p, sold_recently=sold_recently, return_all=return_all)
    
    # 满仓区域
    return select_stocks_v75a(factors, date, close_panel, volume_panel, amount_panel,
                              high_panel, low_panel, open_panel, current_holdings,
                              params, sold_recently=sold_recently, return_all=return_all)
