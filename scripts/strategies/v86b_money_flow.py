#!/usr/bin/env python3
"""v86b: 20日资金流量因子 + 广度过滤

基于v75j框架，将流动性因子替换为资金流量（量*价变化的20日均值）反向排名。
IC分析结果（zz1800池）：
- IC: -0.041, ICIR: -0.321
- 与v75j流动性因子相关性: 0.190（低冗余，最独立）

设计目的：
1. 验证资金流量因子对科技板块选股的有效性
2. 和v75j的流动性因子形成正交对比
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

    # 选股层参数（v86b：资金流量因子）
    "W_BREAKOUT": 0.0,
    "W_VOL_SURGE": 0.0,
    "W_LIQUIDITY": 0.0,
    "W_MONEY_FLOW": 1.0,   # 资金流量因子

    # 资金流量窗口
    "MONEY_FLOW_WINDOW": 20,
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


def calc_factors_v86b(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel=None, extra_data=None):
    """计算v86b因子：20日资金流量反向排名
    资金流量 = |amount * close_pct_change| 的20日均值
    反向：值越大，未来收益越低（高资金流入往往对应顶部）
    """
    codes = close_panel.columns.tolist()

    # 行业映射
    industry_map = _load_industry_map(codes)
    tech_mask = industry_map.isin(TECH_INDUSTRIES)

    # 日收益率
    returns = close_panel.pct_change()

    # 资金流量 = |成交额 * 收益率| 的20日均值
    daily_flow = (amount_panel * returns.abs())
    mf_window = DEFAULT_PARAMS.get("MONEY_FLOW_WINDOW", 20)
    money_flow = daily_flow.rolling(mf_window, min_periods=mf_window//2).mean()

    # 最新一天因子值
    mf_latest = money_flow.iloc[-1]

    # 反向排名（值越大越差）
    scores = pd.Series(np.nan, index=codes)
    valid = mf_latest[tech_mask].dropna()
    if len(valid) > 20:
        r_mf = (-valid).rank(ascending=True, pct=True)  # 反向排名
        scores[r_mf.index] = r_mf

    result = scores.dropna().sort_values(ascending=False)
    return {"v86b": result}


def select_stocks_v86b(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """选股：资金流量因子排序 + 广度过滤"""
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
