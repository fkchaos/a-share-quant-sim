#!/usr/bin/env python3
"""v85a: 板块动量 + 流动性双因子

基于v75n（含科创板+流动性因子+广度过滤），增加板块动量因子：
- 计算科技四大板块（电子/计算机/通信/传媒）近N天涨幅
- 强势板块的股票得分加成，弱势板块减成
- 与流动性因子等权混合

设计思路：
v75n高收益fold全部集中在AI/芯片板块集体上涨期。
板块动量因子可以更精准地捕捉"板块轮动"——
当AI芯片涨时侧重电子，当AI软件涨时侧重计算机。

因子公式：
  final_score = 0.5 * liquidity_rank + 0.5 * sector_momentum_rank

sector_momentum = 该股所属板块近N天的平均涨幅
"""

import numpy as np
import pandas as pd
from scripts.strategies.v75j_liquidity_only import (
    calc_factors_v75j, _calc_breadth, _load_tech_codes
)

DEFAULT_PARAMS = {
    # 风控参数
    "STOP_LOSS": -0.08,
    "TAKE_PROFIT": 0.25,
    "HOLD_DAYS_MAX": 20,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.35,
    "MAX_HOLDINGS": 3,
    "REBALANCE_DAYS": 10,
    "MAX_STOCK_PRICE": 300,

    # 择时层参数
    "BREADTH_MA": 20,
    "BREADTH_HIGH": 0.50,
    "BREADTH_LOW": 0.30,

    # 选股层参数
    "W_BREAKOUT": 0.0,
    "W_VOL_SURGE": 0.0,
    "W_LIQUIDITY": 0.5,
    "W_SECTOR_MOM": 0.5,
    "SECTOR_MOM_WINDOW": 20,  # 板块动量窗口（交易日）
}


def _load_sector_map():
    """加载code→sector映射"""
    import sqlite3, os
    db_path = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'quant_stocks.db')
    conn = sqlite3.connect(db_path)
    rows = conn.execute("""
        SELECT s.code, i.industry
        FROM stock_pool s
        JOIN industry_map i ON s.code = i.code
        WHERE i.industry IN ('电子', '计算机', '通信', '传媒')
    """).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def _calc_sector_momentum(close_panel, date, sector_map, window=20):
    """计算各板块近N天平均涨幅"""
    from datetime import timedelta

    if isinstance(date, str):
        date = pd.Timestamp(date)

    # 获取近N+5天的日期（多取几天确保有足够交易日）
    all_dates = close_panel.index.get_level_values('date').unique().sort_values()
    end_idx = all_dates.searchsorted(date)
    start_idx = max(0, end_idx - window - 5)
    recent_dates = all_dates[start_idx:end_idx + 1]

    if len(recent_dates) < 2:
        return {}

    # 取最近window天
    actual_window = min(window, len(recent_dates) - 1)
    period_dates = recent_dates[-actual_window - 1:]

    # 计算每只股票的区间涨幅
    returns = {}
    for code in close_panel.columns:
        try:
            prices = close_panel[code].loc[period_dates]
            prices = prices.dropna()
            if len(prices) >= 2:
                ret = (prices.iloc[-1] / prices.iloc[0]) - 1
                returns[code] = ret
        except:
            continue

    # 按板块聚合
    sector_returns = {}
    for code, ret in returns.items():
        sector = sector_map.get(code)
        if sector:
            if sector not in sector_returns:
                sector_returns[sector] = []
            sector_returns[sector].append(ret)

    # 板块平均涨幅
    sector_mom = {}
    for sector, rets in sector_returns.items():
        if len(rets) >= 3:  # 至少3只股票才有意义
            sector_mom[sector] = np.mean(rets)

    return sector_mom


def calc_factors_v85a(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel=None, extra_data=None):
    """计算v85a因子：流动性 + 板块动量"""
    # 流动性因子（与v75j相同）
    liq_scores = calc_factors_v75j(close_panel, volume_panel, amount_panel,
                                    high_panel, low_panel, open_panel, extra_data)
    if isinstance(liq_scores, dict):
        liq_df = list(liq_scores.values())[0]
    else:
        liq_df = liq_scores

    return liq_df


def select_stocks_v85a(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """选股：流动性因子 × 板块动量 双因子排序"""
    if params is None:
        params = DEFAULT_PARAMS

    # 广度过滤
    breadth = _calc_breadth(close_panel, date, params)
    high_thresh = params.get("BREADTH_HIGH", 0.50)
    low_thresh = params.get("BREADTH_LOW", 0.30)

    if breadth < low_thresh:
        return []

    # 流动性因子
    if isinstance(factors, dict):
        liq_scores = list(factors.values())[0]
    else:
        liq_scores = factors

    # 限定科技板块
    tech_codes = set(_load_tech_codes())
    liq_scores = liq_scores[liq_scores.index.isin(tech_codes)]

    # 板块动量
    sector_map = _load_sector_map()
    window = params.get("SECTOR_MOM_WINDOW", 20)
    sector_mom = _calc_sector_momentum(close_panel, date, sector_map, window)

    if not sector_mom:
        # 没有板块动量数据时，退化为纯流动性
        sector_scores = pd.Series(0, index=liq_scores.index)
    else:
        # 每只股票的板块动量得分
        sector_scores = pd.Series(0, index=liq_scores.index)
        for code in liq_scores.index:
            sector = sector_map.get(code, '')
            sector_scores[code] = sector_mom.get(sector, 0)

    # 归一化到[0,1]
    def _rank_normalize(s):
        ranked = s.rank(pct=True)
        return ranked

    liq_rank = _rank_normalize(liq_scores)
    sec_rank = _rank_normalize(sector_scores)

    # 双因子混合
    w_liq = params.get("W_LIQUIDITY", 0.5)
    w_sec = params.get("W_SECTOR_MOM", 0.5)
    final_score = w_liq * liq_rank + w_sec * sec_rank

    # 按最终得分排序
    final_score = final_score.sort_values(ascending=False)

    n = params.get('MAX_HOLDINGS', 3)
    display_n = 10 if return_all else n

    # 线性减仓
    if breadth < high_thresh:
        n = max(1, int(n * breadth / high_thresh))

    candidates = final_score.head(max(n * 3, display_n)).index.tolist()
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [c for c in candidates if c not in held]

    return [(code, round(final_score.get(code, 0), 4)) for code in buy_list[:display_n]]
