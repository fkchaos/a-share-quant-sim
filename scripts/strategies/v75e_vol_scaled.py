#!/usr/bin/env python3
"""v75e: 波动率缩放仓位
基于学术文献：Momentum Has Its Moments (Moskowitz等)
原理：仓位∝1/波动率，高波动期减仓，低波动期满仓。
目标波动率 = 最近60天平均波动率的中位数。
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
    # 波动率缩放参数
    "VOL_LOOKBACK": 20,       # 波动率计算窗口
    "VOL_TARGET": 0.30,       # 目标年化波动率
    "VOL_MAX_MULT": 1.0,      # 最大仓位乘数
    "VOL_MIN_MULT": 0.2,      # 最小仓位乘数（不会完全空仓）
}


def _calc_vol_mult(date, close_panel, params):
    """计算波动率缩放乘数
    
    原理：mult = target_vol / current_vol
    高波动→mult小→少买；低波动→mult大→多买
    """
    import sqlite3
    # 加载科技板块代码
    conn = sqlite3.connect('data/quant_stocks.db')
    rows = conn.execute("SELECT code FROM industry_map WHERE industry IN ('电子','计算机','通信','传媒')").fetchall()
    conn.close()
    tech_codes = [r[0] for r in rows]
    available = [c for c in tech_codes if c in close_panel.columns]

    if not available:
        return 0.5

    # 科技板块等权指数
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

    # 计算最近N天的收益率
    vals = sector.values
    returns = np.diff(vals[pos-lookback:pos+1]) / vals[pos-lookback:pos]
    
    # 当前波动率（年化）
    current_vol = np.std(returns) * np.sqrt(252)

    if current_vol <= 0:
        return 0.5

    # 波动率缩放：mult = target / current
    raw_mult = target_vol / current_vol

    # 限制范围
    min_m = params.get("VOL_MIN_MULT", 0.2)
    max_m = params.get("VOL_MAX_MULT", 1.0)
    return np.clip(raw_mult, min_m, max_m)


def calc_factors_v75e(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel):
    """计算因子：复用v75a"""
    return calc_factors_v75a(close_panel, volume_panel, amount_panel,
                             high_panel, low_panel, open_panel)


def select_stocks_v75e(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """v75e选股：根据波动率动态调整仓位"""
    if params is None:
        params = DEFAULT_PARAMS

    # 计算波动率乘数
    mult = _calc_vol_mult(date, close_panel, params)

    # 根据乘数调整持仓数量
    base_count = params.get("MAX_HOLDINGS", 3)
    adjusted_count = max(1, int(base_count * mult))

    adjusted_params = dict(params)
    adjusted_params["MAX_HOLDINGS"] = adjusted_count

    return select_stocks_v75a(factors, date, close_panel, volume_panel, amount_panel,
                              high_panel, low_panel, open_panel, current_holdings,
                              adjusted_params, sold_recently=sold_recently)
