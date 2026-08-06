#!/usr/bin/env python3
"""v76a: v61b + v75f 组合策略
60%资金配v61b（低换手小票），40%资金配v75f（科技趋势+广度过滤）
"""
import numpy as np
import pandas as pd
from scripts.strategies.v61b_turnover_size import calc_factors_v61b, select_stocks_v61b
from scripts.strategies.v75f_breadth import calc_factors_v75f, select_stocks_v75f

DEFAULT_PARAMS = {
    "STOP_LOSS": -0.08,
    "TAKE_PROFIT": 0.25,
    "HOLD_DAYS_MAX": 10,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.25,
    "MAX_HOLDINGS": 3,
    "REBALANCE_DAYS": 5,
    "W_V61B": 0.60,
    "W_V75F": 0.40,
}


def calc_factors_v76a(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel):
    """计算因子：v61b + v75f"""
    f61b = calc_factors_v61b(close_panel, volume_panel, amount_panel,
                             high_panel, low_panel, open_panel)
    f75f = calc_factors_v75f(close_panel, volume_panel, amount_panel,
                             high_panel, low_panel, open_panel)
    return {"v61b": f61b, "v75f": f75f}


def select_stocks_v76a(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """v76a选股：v61b + v75f 组合"""
    if params is None:
        params = DEFAULT_PARAMS

    p = params
    w61b = p.get("W_V61B", 0.60)
    w75f = p.get("W_V75F", 0.40)

    # v61b选股
    if isinstance(factors, dict):
        f61b = factors.get("v61b")
        f75f = factors.get("v75f")
    else:
        f61b = factors
        f75f = factors

    stocks_61b = select_stocks_v61b(f61b, date, close_panel, volume_panel, amount_panel,
                                    high_panel, low_panel, open_panel, current_holdings,
                                    params, sold_recently=sold_recently)

    stocks_75f = select_stocks_v75f(f75f, date, close_panel, volume_panel, amount_panel,
                                    high_panel, low_panel, open_panel, current_holdings,
                                    params, sold_recently=sold_recently)

    # 合并：v61b选3只，v75f选1-2只
    n61b = max(1, int(p.get("MAX_HOLDINGS", 3) * w61b))
    n75f = max(1, int(p.get("MAX_HOLDINGS", 3) * w75f))

    result = []
    seen = set()

    # v61b部分
    for code, score in stocks_61b[:n61b]:
        if code not in seen:
            result.append((code, round(score * w61b, 4)))
            seen.add(code)

    # v75f部分
    for code, score in stocks_75f[:n75f]:
        if code not in seen:
            result.append((code, round(score * w75f, 4)))
            seen.add(code)

    return result
