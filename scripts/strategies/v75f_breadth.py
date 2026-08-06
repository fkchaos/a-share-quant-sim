#!/usr/bin/env python3
"""v75f: 市场广度过滤
多少科技股在MA20以上。广度>50%允许交易，<30%空仓。
"""
import numpy as np
import pandas as pd
from scripts.strategies.v75a_tech_momentum import calc_factors_v75a, select_stocks_v75a

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
    "STOP_LOSS": -0.08, "TAKE_PROFIT": 0.30,
    "HOLD_DAYS_MAX": 15, "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.35, "MAX_HOLDINGS": 3,
    "REBALANCE_DAYS": 10,
    "BREADTH_MA": 20,
    "BREADTH_HIGH": 0.50,
    "BREADTH_LOW": 0.30,
}

def _calc_breadth(close_panel, date, params):
    """计算广度：多少科技股收盘价>MA20"""
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

def calc_factors_v75f(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel, extra_data=None):
    return calc_factors_v75a(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel, extra_data)

def select_stocks_v75f(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    if params is None:
        params = DEFAULT_PARAMS
    breadth = _calc_breadth(close_panel, date, params)
    high_thresh = params.get("BREADTH_HIGH", 0.50)
    low_thresh = params.get("BREADTH_LOW", 0.30)
    if breadth < low_thresh:
        return []
    if breadth < high_thresh:
        p = dict(params)
        p["MAX_HOLDINGS"] = max(1, int(params.get("MAX_HOLDINGS", 3) * breadth / high_thresh))
        return select_stocks_v75a(factors, date, close_panel, volume_panel, amount_panel,
                                  high_panel, low_panel, open_panel, current_holdings,
                                  p, sold_recently=sold_recently, return_all=return_all)
    return select_stocks_v75a(factors, date, close_panel, volume_panel, amount_panel,
                              high_panel, low_panel, open_panel, current_holdings,
                              params, sold_recently=sold_recently, return_all=return_all)
