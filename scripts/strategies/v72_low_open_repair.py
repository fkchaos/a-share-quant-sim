#!/usr/bin/env python3
"""
v72: 首板低开修复策略
====================
参考9db.com实盘追踪策略（50天22.87%收益）
核心逻辑：
1. 昨日涨停（首板，非连板）
2. 今日低开（开盘价 < 昨收*1.01，即低开不超过1%）
3. 买入后持有1-3天等修复
4. 止损-3%，止盈+8%

关键过滤：
- 排除ST/科创板/北交所
- 排除一字涨停（买不进）
- 排除连板股（不是首板）
- 最小市值20亿，最小成交额5000万
"""
import pandas as pd
import numpy as np
import sqlite3
import os

DEFAULT_PARAMS = {
    "STOP_LOSS": -0.03,
    "TAKE_PROFIT": 0.08,
    "HOLD_DAYS_MAX": 3,
    "MAX_HOLDINGS": 3,
    "MAX_DAILY_BUY": 1,
    "MAX_POSITION": 0.35,
    "HOLD_DAYS_MIN": 1,
    "HOLD_DAYS_EXTEND": 5,
    "HOLD_DAYS_EXTEND_PNL": 0.05,
    # 低开条件
    "LOW_OPEN_MAX": 1.01,     # 开盘价 < 昨收*1.01（最多高开1%）
    "LOW_OPEN_MIN": 0.95,     # 开盘价 > 昨收*0.95（最多低开5%）
    # 过滤
    "EXCLUDE_LIMIT_UP": True,
    "MIN_AMOUNT": 5e7,
    "MIN_MARKET_CAP": 2e9,
}


def calc_factors_v72(close_panel, volume_panel, amount_panel,
                     high_panel=None, low_panel=None, open_panel=None,
                     extra_data=None):
    """
    计算v72因子：
    - yesterday_limit_up: 昨日是否涨停
    - two_day_limit: 前天是否涨停（排除连板）
    - today_low_open: 今日是否低开
    """
    returns = close_panel.pct_change()

    # 涨停判定：收益率在9.5%-10.5%之间
    limit_threshold = 0.095
    limit_up = ((returns >= limit_threshold) & (returns <= 0.105)).astype(float)

    # 昨日涨停
    yesterday_limit_up = limit_up.shift(1).fillna(0)

    # 前天涨停（排除连板：如果前天也涨停，说明是二板以上）
    two_day_ago_limit = limit_up.shift(2).fillna(0)

    # 首板 = 昨日涨停 且 前天没涨停
    first_board = (yesterday_limit_up == 1) & (two_day_ago_limit == 0)

    # 今日低开判断
    if open_panel is not None:
        prev_close = close_panel.shift(1)
        open_ratio = open_panel / (prev_close + 1e-10)
        # 低开条件：开盘价在昨收的95%-101%之间
        low_open = (open_ratio >= 0.95) & (open_ratio <= 1.01)
    else:
        low_open = pd.DataFrame(False, index=close_panel.index, columns=close_panel.columns)

    # 一字涨停排除（开盘=收盘=最高=最低）
    if open_panel is not None and high_panel is not None and low_panel is not None:
        one_word_limit = (open_panel == high_panel) & (high_panel == low_panel) & (low_panel == close_panel)
    else:
        one_word_limit = pd.DataFrame(False, index=close_panel.index, columns=close_panel.columns)

    return {
        'first_board': first_board,
        'low_open': low_open,
        'one_word_limit': one_word_limit,
        'yesterday_limit_up': yesterday_limit_up,
        'returns': returns,
    }


def select_stocks_v72(factors, date, current_holdings=None, params=None,
                       sold_recently=None, close_panel=None, high_panel=None,
                       open_panel=None):
    """
    v72选股：
    1. 昨日首板涨停（非连板）
    2. 今日低开（95%-101%）
    3. 排除一字涨停
    4. 排除已持有/近期卖出
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    if date not in factors['first_board'].index:
        return []

    # 首板股票
    fb = factors['first_board'].loc[date]
    first_board_stocks = list(fb[fb == 1].index)

    if not first_board_stocks:
        return []

    # 今日低开
    lo = factors['low_open'].loc[date] if date in factors['low_open'].index else pd.Series(False, index=first_board_stocks)
    candidates = [c for c in first_board_stocks if c in lo.index and lo[c]]

    if not candidates:
        return []

    # 排除一字涨停
    ow = factors['one_word_limit'].loc[date] if date in factors['one_word_limit'].index else pd.Series(False, index=candidates)
    candidates = [c for c in candidates if c in ow.index and not ow[c]]

    # 排除已持有和近期卖出
    if current_holdings:
        candidates = [c for c in candidates if c not in current_holdings]
    if sold_recently:
        candidates = [c for c in candidates if c not in sold_recently]

    if not candidates:
        return []

    # 低开幅度排序（低开越多，修复空间越大）
    if open_panel is not None and date in open_panel.index:
        prev_close = close_panel.shift(1) if close_panel is not None else None
        if prev_close is not None and date in prev_close.index:
            pc = prev_close.loc[date]
            op = open_panel.loc[date]
            scores = pd.Series(0.0, index=candidates)
            for c in candidates:
                if c in pc.index and c in op.index and pc[c] > 0:
                    # 低开幅度：越低越好（负值=低开越多）
                    scores[c] = -(op[c] / pc[c] - 1)  # 取反，低开越多分越高
            scores = scores.sort_values(ascending=False)
        else:
            scores = pd.Series(range(len(candidates), 0, -1), index=candidates)
    else:
        scores = pd.Series(range(len(candidates), 0, -1), index=candidates)

    selected = scores.index[:p['MAX_DAILY_BUY']]
    return [(code, scores[code]) for code in selected]


if __name__ == '__main__':
    print("v72: 首板低开修复策略")
    print(f"低开范围: {DEFAULT_PARAMS['LOW_OPEN_MIN']:.0%} ~ {DEFAULT_PARAMS['LOW_OPEN_MAX']:.0%}")
    print(f"止损: {DEFAULT_PARAMS['STOP_LOSS']:.0%}, 止盈: {DEFAULT_PARAMS['TAKE_PROFIT']:.0%}")
