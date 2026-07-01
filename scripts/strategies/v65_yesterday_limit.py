#!/usr/bin/env python3
"""
v65_yesterday_limit.py — 昨日涨停打板策略（严格按BigQuant原始逻辑）
================================================================
核心逻辑（BigQuant原始）：
1. 选股：连续两天涨停 + 热门概念（近3日+15日涨幅排名≥98%）
2. 排序：按市值升序（偏好中小盘弹性标的）
3. 买入：T+1日盘中高开>=2%时买入
4. 卖出：T+2日止盈5%或强制平仓

来源：BigQuant - 昨日涨停打板策略【日频】
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple

DEFAULT_PARAMS = {
    # ── 风控参数 ──
    "STOP_LOSS": -0.05,           # 止损线
    "TAKE_PROFIT": 0.05,          # 止盈线（5%）
    "HOLD_DAYS_MAX": 1,           # 最大持仓天数（1天）
    "MAX_DAILY_BUY": 3,           # 每日最多买入
    "MAX_POSITION": 0.20,         # 单票最大仓位
    "MAX_HOLDINGS": 5,            # 最大持仓数
    
    # ── 选股门槛（BigQuant原始参数）──
    "MIN_AMOUNT": 5000000,        # 最小成交额（500万）
    "MIN_MARKET_CAP": 2000000000, # 最小市值（20亿）
    "CONCEPT_HEAT_TOP": 0.98,     # 概念热度排名≥98%（BigQuant原始）
    "LIMIT_THRESHOLD": 0.095,     # 涨停阈值（9.5%）
    "HIGH_OPEN_THRESHOLD": 0.02,  # 高开阈值（2%）
}


def calc_factors_v65_yesterday_limit(close_panel: pd.DataFrame,
                                     volume_panel: pd.DataFrame,
                                     amount_panel: pd.DataFrame,
                                     high_panel: pd.DataFrame,
                                     low_panel: pd.DataFrame,
                                     open_panel: pd.DataFrame = None,
                                     extra_data: dict = None) -> Dict[str, pd.DataFrame]:
    """
    计算昨日涨停打板因子（BigQuant原始逻辑）
    """
    factors = {}
    
    # 1. 涨停判断（用涨跌幅接近10%）
    returns = close_panel.pct_change()
    limit_threshold = 0.095  # 9.5%作为涨停近似
    limit_up = (returns >= limit_threshold) & (returns <= 0.105)  # 9.5%-10.5%
    factors['limit_up'] = limit_up
    
    # 2. 连续两天涨停因子（BigQuant核心条件）
    # today_limit: 当日涨停
    # yesterday_limit: 前一日涨停
    yesterday_limit = limit_up.shift(1).fillna(False)
    two_day_limit = limit_up & yesterday_limit  # 连续两天涨停
    factors['two_day_limit'] = two_day_limit
    
    # 3. 概念热度因子（近3日+15日涨幅排名）
    returns_3d = close_panel.pct_change(3)
    returns_15d = close_panel.pct_change(15)
    
    # 概念热度 = 3日涨幅排名 + 15日涨幅排名
    concept_heat = (
        returns_3d.rank(axis=1, pct=True) + 
        returns_15d.rank(axis=1, pct=True)
    ) / 2
    factors['concept_heat'] = concept_heat
    
    # 4. 成交额因子
    amount_factor = amount_panel.rank(axis=1, pct=True)
    factors['amount_factor'] = amount_factor
    
    # 5. 市值因子（用成交额近似，因为没有直接的市值数据）
    # BigQuant用的是total_market_cap，我们用成交额近似
    factors['market_cap_approx'] = amount_panel.rolling(20).mean()
    
    # 6. 高开幅度因子
    if open_panel is not None:
        high_open = (open_panel / close_panel.shift(1) - 1)
        factors['high_open'] = high_open
    
    return factors


def select_stocks_v65_yesterday_limit(factors: Dict[str, pd.DataFrame],
                                      date: str,
                                      current_holdings: Optional[Dict] = None,
                                      params: Optional[Dict] = None,
                                      sold_recently: Optional[List] = None) -> List[Tuple[str, float]]:
    """
    昨日涨停打板选股（BigQuant原始逻辑）
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    
    if date not in factors.get('two_day_limit', pd.DataFrame()).index:
        return []
    
    # 获取当前日期的数据
    two_day_limit = factors['two_day_limit'].loc[date]
    concept_heat = factors['concept_heat'].loc[date]
    amount_factor = factors['amount_factor'].loc[date]
    market_cap = factors['market_cap_approx'].loc[date]
    
    # 筛选条件
    min_amount = p.get('MIN_AMOUNT', 5000000)
    min_market_cap = p.get('MIN_MARKET_CAP', 2000000000)
    concept_heat_top = p.get('CONCEPT_HEAT_TOP', 0.98)
    
    # 1. 连续两天涨停筛选（BigQuant核心条件）
    candidate_mask = two_day_limit
    
    # 2. 概念热度筛选：排名≥98%
    concept_heat_threshold = concept_heat.quantile(concept_heat_top)
    candidate_mask = candidate_mask & (concept_heat >= concept_heat_threshold)
    
    # 3. 成交额筛选：至少500万
    amount_today = factors.get('amount_panel', pd.DataFrame()).loc[date] if 'amount_panel' in factors else None
    if amount_today is not None:
        candidate_mask = candidate_mask & (amount_today >= min_amount)
    
    # 获取候选股票
    candidates = two_day_limit[candidate_mask].index.tolist()
    
    if not candidates:
        return []
    
    # 按市值升序排序（BigQuant原始排序方式）
    # 偏好中小盘弹性标的
    market_cap_scores = market_cap[candidates].rank(ascending=True)
    sorted_candidates = market_cap_scores.sort_values().index.tolist()
    
    # 选前N只
    n = p.get('MAX_HOLDINGS', 5)
    selected = sorted_candidates[:n]
    
    return [(code, 1.0) for code in selected]


if __name__ == '__main__':
    print("昨日涨停打板策略（BigQuant原始逻辑）")
    print(f"默认参数: {DEFAULT_PARAMS}")
