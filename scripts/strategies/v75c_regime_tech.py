#!/usr/bin/env python3
"""v75c: v75a + 科技板块趋势门控
用科技板块等权指数（电子/计算机/通信/传媒）的MA+斜率判断牛市。
上证指数不在DB中，改用自有数据。
"""

import sqlite3
import numpy as np
import pandas as pd
from scripts.strategies.v75a_tech_momentum import calc_factors_v75a, select_stocks_v75a

# 科技板块代码（和v75a一致）
_tech_codes = None

def _load_tech_codes():
    global _tech_codes
    if _tech_codes is not None:
        return _tech_codes
    conn = sqlite3.connect('data/quant_stocks.db')
    rows = conn.execute("SELECT code FROM industry_map WHERE industry IN ('电子','计算机','通信','传媒')").fetchall()
    conn.close()
    _tech_codes = [r[0] for r in rows]
    return _tech_codes

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
    "REGIME_SLOPE_THRESHOLD": 0.0,
}


def _check_regime(date, close_panel):
    """判断科技板块是否处于牛市（板块等权指数 > MA + 斜率>0）"""
    tech_codes = _load_tech_codes()
    available = [c for c in tech_codes if c in close_panel.columns]
    if not available:
        return True  # 数据缺失默认允许

    # 科技板块等权指数
    sector = close_panel[available].mean(axis=1)
    sector = sector.dropna()
    if len(sector) < 50:
        return True

    if date not in sector.index:
        return True

    pos = sector.index.get_loc(date)
    if isinstance(pos, slice):
        pos = pos.start

    ma_period = 50
    slope_days = 10

    if pos < ma_period:
        return True

    vals = sector.values
    ma = vals[pos - ma_period + 1: pos + 1].mean()
    price = vals[pos]

    above_ma = price > ma

    if pos >= slope_days:
        slope = (vals[pos] - vals[pos - slope_days]) / vals[pos - slope_days]
    else:
        slope = 0.0

    return above_ma and slope > 0.0


def calc_factors_v75c(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel):
    """计算v75a因子（v75c复用v75a因子）"""
    return calc_factors_v75a(close_panel, volume_panel, amount_panel,
                             high_panel, low_panel, open_panel)


def select_stocks_v75c(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """v75c选股：科技板块牛市时调用v75a选股，否则空仓"""
    if params is None:
        params = DEFAULT_PARAMS

    # regime门控
    if not _check_regime(date, close_panel):
        return []

    return select_stocks_v75a(factors, date, close_panel, volume_panel, amount_panel,
                              high_panel, low_panel, open_panel, current_holdings,
                              params, sold_recently=sold_recently)
