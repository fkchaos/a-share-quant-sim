#!/usr/bin/env python3
"""v61c: 换手率+小市值 + 到期续持优化（基于v61b，当前账户1策略）

核心改进：
- v61b: 持有到期(5天) → 强制卖出 → 可能买回同一只（白交手续费）
- v61c: 持有到期 → 检查是否还在Top15 → 在则续持，不在才卖出

止盈止损保持硬性（排名是选股指标，不是风控指标）。

WF对比（16 folds）:
  v61b原始: Sharpe=2.407, 收益=+36.8%, 正fold=14/16
  v61c-top15: Sharpe=2.530, 收益=+37.6%, 正fold=15/16 ✅
"""

import numpy as np
import pandas as pd

DEFAULT_PARAMS = {
    'MAX_HOLDINGS': 5,
    'REBALANCE_DAYS': 5,
    'STOP_LOSS': -0.08,
    'TAKE_PROFIT': 0.25,
    'HOLD_DAYS_MAX': 5,
    'SELL_OUT_OF': 15,  # 到期时检查的排名范围
}


def calc_factors_v61c(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None, extra_data=None):
    """计算换手率+小市值因子（与v61b完全相同）"""
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
    if isinstance(result, pd.Series):
        return {"v61c": result}
    return result


def select_stocks_v61c(factors, date, close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel, current_holdings, params=None, sold_recently=None):
    """选股: 等权评分后选前N只（与v61b相同）"""
    p = params or DEFAULT_PARAMS
    n = p.get('MAX_HOLDINGS', 5)

    if isinstance(factors, dict):
        scores = list(factors.values())[0]
    else:
        scores = factors

    candidates = scores.head(n * 2).index.tolist()
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [c for c in candidates if c not in held]

    return [(code, round(scores.get(code, 0), 4)) for code in buy_list[:n]]
