#!/usr/bin/env python3
"""v61e: v61c排名掉出Top15 + 涨跌幅确认启动信号 (v3)

核心思路：
- v61c选低换手+小市值的Top15
- 如果一只股票之前在Top15，现在掉出去了
- 排名下降 = 换手率升高/市值变大 = 被市场关注 = 可能要启动
- 加涨跌幅判断确认：近期有涨幅的才买

选股逻辑：
1. 计算v61c因子得分（换手率+小市值）
2. 保存历史排名（跨bar，在select中保存）
3. 找出"5天前在Top15，现在掉出去"的股票
4. 加涨跌幅条件：3日>1%, 5日>2%
5. 排除已持仓
6. 按涨跌幅排序选前N只
"""

import numpy as np
import pandas as pd

DEFAULT_PARAMS = {
    'MAX_HOLDINGS': 5,
    'REBALANCE_DAYS': 5,
    'STOP_LOSS': -0.08,
    'TAKE_PROFIT': 0.25,
    'HOLD_DAYS_MAX': 5,
    'SELL_OUT_OF': 15,
    'RANK_TODAY_N': 15,
    'LOOKBACK_BARS': 5,
    'MIN_PCT_3D': 0.01,
    'MIN_PCT_5D': 0.02,
}

# 跨bar保存的历史排名 (date -> scores)
_rank_history = {}


def calc_factors_v61e(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None, extra_data=None):
    """计算v61c因子得分 + 涨跌幅因子"""
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

    # v61c rank 评分 (低换手=高分, 小市值=高分)
    scores = pd.Series(0.0, index=codes)
    for f in [-t5, -sz]:
        valid = f.dropna()
        if len(valid) > 50:
            ranked = valid.rank(ascending=True, pct=True)
            scores[ranked.index] += ranked

    # 涨跌幅因子
    pct_3d = close_panel.pct_change(3).iloc[-1]
    pct_5d = close_panel.pct_change(5).iloc[-1]

    return {
        "v61e": scores,
        "pct_3d": pct_3d,
        "pct_5d": pct_5d,
    }


def select_stocks_v61e(factors, date, close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel, current_holdings, params=None, sold_recently=None):
    """选股: 选"5天前在Top15，现在掉出去" + 涨幅确认启动的股票"""
    global _rank_history
    import sys

    p = params or DEFAULT_PARAMS
    p = params or DEFAULT_PARAMS
    n = p.get('MAX_HOLDINGS', 5)
    rank_n = p.get('RANK_TODAY_N', 15)
    min_pct_3d = p.get('MIN_PCT_3D', 0.01)
    min_pct_5d = p.get('MIN_PCT_5D', 0.02)

    if isinstance(factors, dict):
        scores = factors.get("v61e")
        pct_3d = factors.get("pct_3d")
        pct_5d = factors.get("pct_5d")
    else:
        import sys
        print(f"[v61e] factors is not dict: {type(factors)}", file=sys.stderr)
        return []
    if scores is None or pct_3d is None or pct_5d is None:
        import sys
        print(f"[v61e] scores/pct missing: scores={scores is not None}, pct_3d={pct_3d is not None}, pct_5d={pct_5d is not None}", file=sys.stderr)
        return []

    # 保存今日排名到历史
    _rank_history[date] = scores.copy()

    # 清理旧数据 (只保留最近30天)
    if len(_rank_history) > 30:
        sorted_dates = sorted(_rank_history.keys())
        for old_date in sorted_dates[:-30]:
            del _rank_history[old_date]

    # 找出今日Top N
    sorted_today = scores.sort_values(ascending=False)
    today_top_n = set(sorted_today.head(rank_n).index)

    # 找出历史Top N（lookback天前）
    hist_scores = None
    for d in sorted(_rank_history.keys(), reverse=True):
        if d < date:
            hist_scores = _rank_history[d]
            break

    if hist_scores is None:
        # 没有历史数据，返回空
        return []

    sorted_hist = hist_scores.sort_values(ascending=False)
    hist_top_n = set(sorted_hist.head(rank_n).index)

    # 找出"历史在Top N，现在不在Top N"的股票
    dropped = [c for c in hist_top_n if c not in today_top_n]

    if not dropped:
        return []

    # 加涨跌幅条件
    candidates = []
    for code in dropped:
        if code in pct_3d.index and code in pct_5d.index:
            r3 = pct_3d[code]
            r5 = pct_5d[code]
            if not pd.isna(r3) and not pd.isna(r5) and r3 > min_pct_3d and r5 > min_pct_5d:
                candidates.append((code, round(scores.get(code, 0), 4), round(r3, 4), round(r5, 4)))

    # 按5日涨跌幅降序排序
    candidates.sort(key=lambda x: x[3], reverse=True)

    # 排除已持仓
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [(code, score) for code, score, r3, r5 in candidates if code not in held]
    # Debug
    import sys
    print(f"[v61e] date={date}, hist={len(_rank_history)}, dropped={len(dropped)}, candidates={len(candidates)}, buy={len(buy_list)}", file=sys.stderr)

    return buy_list[:n]
