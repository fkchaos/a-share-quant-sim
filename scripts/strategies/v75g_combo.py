#!/usr/bin/env python3
"""v75g: v75f + 波动率缩放
结合两个最优过滤方法：
1. 市场广度过滤（v75f）：板块健康度
2. 波动率缩放（v75e）：仓位动态调整
"""
import numpy as np
import pandas as pd
from scripts.strategies.v75f_breadth import calc_factors_v75f, select_stocks_v75f

DEFAULT_PARAMS = {
    "STOP_LOSS": -0.08,
    "TAKE_PROFIT": 0.30,
    "HOLD_DAYS_MAX": 15,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.35,
    "MAX_HOLDINGS": 3,
    "REBALANCE_DAYS": 10,
    "VOL_LOOKBACK": 20,
    "VOL_TARGET": 0.30,
}


def _calc_vol_mult(date, close_panel, params):
    """计算波动率缩放乘数"""
    import sqlite3
    conn = sqlite3.connect('data/quant_stocks.db')
    rows = conn.execute("SELECT code FROM industry_map WHERE industry IN ('电子','计算机','通信','传媒')").fetchall()
    conn.close()
    tech_codes = [r[0] for r in rows]
    available = [c for c in tech_codes if c in close_panel.columns]

    if not available:
        return 0.5

    sector = close_panel[available].mean(axis=1).dropna()
    if len(sector) < 60:
        return 0.5

    if date not in sector.index:
        return 0.5

    pos = sector.index.get_loc(date)
    if isinstance(pos, slice):
        pos = pos.start

    lookback = params.get("VOL_LOOKBACK", 20)
    target_vol = params.get("VOL_TARGET", 0.30)

    if pos < lookback:
        return 0.5

    vals = sector.values
    returns = np.diff(vals[pos-lookback:pos+1]) / vals[pos-lookback:pos]
    current_vol = np.std(returns) * np.sqrt(252)

    if current_vol <= 0:
        return 0.5

    raw_mult = target_vol / current_vol
    return np.clip(raw_mult, 0.2, 1.0)


def calc_factors_v75g(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel):
    """计算因子：复用v75f"""
    return calc_factors_v75f(close_panel, volume_panel, amount_panel,
                             high_panel, low_panel, open_panel)


def select_stocks_v75g(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """v75g选股：v75f + 波动率缩放"""
    if params is None:
        params = DEFAULT_PARAMS

    mult = _calc_vol_mult(date, close_panel, params)

    base_count = params.get("MAX_HOLDINGS", 3)
    adjusted_count = max(1, int(base_count * mult))

    adjusted_params = dict(params)
    adjusted_params["MAX_HOLDINGS"] = adjusted_count

    return select_stocks_v75f(factors, date, close_panel, volume_panel, amount_panel,
                              high_panel, low_panel, open_panel, current_holdings,
                              adjusted_params, sold_recently=sold_recently)
