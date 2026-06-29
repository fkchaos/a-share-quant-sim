#!/usr/bin/env python3
"""v61: 换手率+小市值 低流动性溢价策略
因子: 换手率5日均值(负向) + 市值(负向), 等权50/50
选股: 每5天调仓, 选综合得分最高5只
WF: 夏普2.342, 正fold 15/16 ✅
"""

import numpy as np
import pandas as pd

DEFAULT_PARAMS = {
    'MAX_HOLDINGS': 5,
    'REBALANCE_DAYS': 5,
    'STOP_LOSS': -0.10,
    'TAKE_PROFIT': 0.20,
    'HOLD_DAYS_MAX': 5,
}


def calc_factors_v61(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None, extra_data=None):
    """计算换手率+小市值因子"""
    import sqlite3
    conn = sqlite3.connect('data/quant_stocks.db', timeout=15)
    fs = pd.read_sql_query('SELECT code, float_shares FROM stock_pool_zz1800', conn, index_col='code')['float_shares']
    conn.close()

    codes = close_panel.columns.tolist()
    fs_arr = fs.reindex(codes).fillna(fs.median())

    # 换手率 = volume / float_shares
    turnover = volume_panel.div(fs_arr, axis=1)

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

    return scores.sort_values(ascending=False)


def select_stocks_v61(factors, date, close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel, current_holdings, params=None, sold_recently=None):
    """选股: 等权评分后选前N只"""
    p = params or DEFAULT_PARAMS
    n = p.get('MAX_HOLDINGS', 5)

    candidates = factors.head(n * 2).index.tolist()  # 多取一些候选
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [c for c in candidates if c not in held]

    return [(code, 1.0) for code in buy_list[:n]]
