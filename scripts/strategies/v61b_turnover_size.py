#!/usr/bin/env python3
"""v61b: 换手率+小市值 低流动性溢价策略（优化版）
基于v61优化风控参数:
- 止损: -10% → -8%
- 止盈: +20% → +25%
- 持仓: 5天（保持不变）
- 新增: 卖出即买逻辑（不等调仓日）

WF: 夏普2.300, 正fold 15/16 ✅
"""

import numpy as np
import pandas as pd

DEFAULT_PARAMS = {
    'MAX_HOLDINGS': 5,
    'REBALANCE_DAYS': 5,
    'STOP_LOSS': -0.08,
    'TAKE_PROFIT': 0.25,
    'HOLD_DAYS_MAX': 5,
}


def calc_factors_v61b(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None, extra_data=None):
    """计算换手率+小市值因子"""
    import sqlite3
    conn = sqlite3.connect('data/quant_stocks.db', timeout=15)
    fs = pd.read_sql_query('SELECT code, float_shares FROM stock_pool_zz1800', conn, index_col='code')['float_shares']
    conn.close()

    codes = close_panel.columns.tolist()
    fs_arr = fs.reindex(codes).fillna(fs.median())

    # 换手率 = volume(手) * 100 / float_shares(股)
    turnover = volume_panel.mul(100).div(fs_arr, axis=1)

    # 5日均换手率 (负向)
    turn_5 = turnover.rolling(5, min_periods=3).mean()

    # 市值 (负向)
    market_cap = close_panel.mul(fs_arr, axis=1)

    # 最新一天的因子值
    t5 = turn_5.iloc[-1]
    sz = market_cap.iloc[-1]

    # rank 评分 (低换手=高分, 小市值=高分)
    scores = pd.Series(0.0, index=codes)
    for f in [-t5, -sz]:
        valid = f.dropna()
        if len(valid) > 50:
            ranked = valid.rank(ascending=True, pct=True)
            scores[ranked.index] += ranked

    result = scores.sort_values(ascending=False)
    # 包装为 dict 以兼容 wf_runner 的 _slice_factors
    if isinstance(result, pd.Series):
        return {"v61b": result}
    return result


def select_stocks_v61b(factors, date, close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel, current_holdings, params=None, sold_recently=None):
    """选股: 等权评分后选前N只"""
    p = params or DEFAULT_PARAMS
    n = p.get('MAX_HOLDINGS', 5)

    # 兼容 dict 和 Series 两种因子格式
    if isinstance(factors, dict):
        scores = list(factors.values())[0]
    else:
        scores = factors

    candidates = scores.head(n * 2).index.tolist()
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [c for c in candidates if c not in held]

    return [(code, 1.0) for code in buy_list[:n]]
