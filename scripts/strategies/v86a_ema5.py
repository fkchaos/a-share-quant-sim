#!/usr/bin/env python3
"""v86a: 5日EMA因子 + 广度过滤

基于v75j框架，将流动性因子（20日均成交额）替换为5日EMA反向排名。
IC分析结果（zz1800池）：
- IC: -0.068, ICIR: -0.344
- 与v75j流动性因子相关性: 0.417（中等独立）

设计目的：
1. 验证价格趋势因子（EMA）对科技板块选股的有效性
2. 和v75j的流动性因子形成对比
"""

import numpy as np
import pandas as pd
from scripts.strategies.v75a_tech_momentum import (
    calc_factors_v75a, select_stocks_v75a,
    _load_industry_map, TECH_INDUSTRIES
)
from scripts.strategies.score_delta import save_scores, get_yesterday_scores, rerank_by_delta

# 科技板块代码
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
    # 风控参数（与v75j相同）
    "STOP_LOSS": -0.08,
    "TAKE_PROFIT": 0.25,
    "HOLD_DAYS_MAX": 20,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.35,
    "MAX_HOLDINGS": 3,
    "REBALANCE_DAYS": 10,
    "MAX_STOCK_PRICE": 300,

    # 择时层参数（广度过滤，与v75j相同）
    "BREADTH_MA": 20,
    "BREADTH_HIGH": 0.50,
    "BREADTH_LOW": 0.30,

    # 选股层参数（v86a：5日EMA因子）
    "W_BREAKOUT": 0.0,
    "W_VOL_SURGE": 0.0,
    "W_LIQUIDITY": 0.0,   # 不用流动性
    "W_EMA": 1.0,          # 5日EMA反向排名

    # EMA窗口
    "EMA_WINDOW": 5,
}


def _calc_breadth(close_panel, date, params):
    """计算广度：多少科技股收盘价>MA20（与v75j相同）"""
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


def calc_factors_v86a(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel=None, extra_data=None):
    """计算v86a因子：5日EMA反向排名"""
    codes = close_panel.columns.tolist()
    dates = close_panel.index

    # 行业映射
    industry_map = _load_industry_map(codes)
    tech_mask = industry_map.isin(TECH_INDUSTRIES)

    # 5日EMA（反向：越大越差）
    ema_window = DEFAULT_PARAMS.get("EMA_WINDOW", 5)
    ema_values = close_panel.ewm(span=ema_window, adjust=False).mean()

    # 最新一天因子值
    ema_latest = ema_values.iloc[-1]

    # 反向：负的EMA（越大越好→越小的EMA越好）
    scores = pd.Series(np.nan, index=codes)
    valid = ema_latest[tech_mask].dropna()
    if len(valid) > 20:
        r_ema = (-valid).rank(ascending=True, pct=True)  # 反向排名
        scores[r_ema.index] = r_ema

    result = scores.dropna().sort_values(ascending=False)
    return {"v86a": result}


def select_stocks_v86a(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """选股：EMA因子排序 + 广度过滤"""
    if params is None:
        params = DEFAULT_PARAMS

    # 广度过滤（择时层）
    breadth = _calc_breadth(close_panel, date, params)
    high_thresh = params.get("BREADTH_HIGH", 0.50)
    low_thresh = params.get("BREADTH_LOW", 0.30)

    if breadth < low_thresh:
        return []

    # 线性减仓（中间区域）
    if breadth < high_thresh:
        p = dict(params)
        p["MAX_HOLDINGS"] = max(1, int(params.get("MAX_HOLDINGS", 3) * breadth / high_thresh))
        return select_stocks_v75a(factors, date, close_panel, volume_panel, amount_panel,
                                  high_panel, low_panel, open_panel, current_holdings,
                                  p, sold_recently=sold_recently, return_all=return_all)

    # 满仓区域
    return select_stocks_v75a(factors, date, close_panel, volume_panel, amount_panel,
                              high_panel, low_panel, open_panel, current_holdings,
                              params, sold_recently=sold_recently, return_all=return_all)
