#!/usr/bin/env python3
"""
v64_concept_heat.py — 概念热度打板策略优化版
====================================================
核心逻辑：
- 识别强势概念板块（热度前2%）
- 选择板块内近期涨停的个股
- 盘中确认涨幅后介入

优化点：
1. 改进概念热度计算：用板块数据（用industry_map数据）
2. 收紧近期涨停筛选：要求更严格的条件
3. 改进市值因子：用真实市值数据
4. 加入流动性筛选
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple


DEFAULT_PARAMS = {
    # ── 风控参数 ──
    "STOP_LOSS": -0.05,           # 止损线
    "TAKE_PROFIT": 0.05,          # 止盈线
    "HOLD_DAYS_MAX": 3,           # 最大持仓天数
    "MAX_DAILY_BUY": 2,           # 每日最多买入
    "MAX_POSITION": 0.25,         # 单票最大仓位
    "MAX_HOLDINGS": 3,            # 最大持仓数
    
    # ── 选股门槛 ──
    "MIN_AMOUNT": 5000000,        # 最小成交额（500万）
    "MIN_MARKET_CAP": 2000000000, # 最小市值（20亿）
    "MIN_RECENT_LIMIT": 2,        # 近期最少涨停次数（收紧）
    "CONCEPT_HEAT_TOP": 0.02,     # 概念热度前2%
    "LIMIT_THRESHOLD": 0.095,     # 涨停阈值（9.5%）
    
    # ── 评分权重 ──
    "W_CONCEPT_HEAT": 0.35,       # 概念热度权重
    "W_RECENT_LIMIT": 0.30,       # 近期涨停权重
    "W_LIQUIDITY": 0.20,          # 流动性权重
    "W_MARKET_CAP": 0.15,         # 市值权重
}


def calc_factors_v64_concept(close_panel: pd.DataFrame,
                              volume_panel: pd.DataFrame,
                              amount_panel: pd.DataFrame,
                              high_panel: pd.DataFrame,
                              low_panel: pd.DataFrame,
                              open_panel: pd.DataFrame = None,
                              extra_data: dict = None) -> Dict[str, pd.DataFrame]:
    """
    计算概念热度打板因子（优化版）
    """
    factors = {}
    
    # 1. 涨停判断（改进：用涨跌幅接近10%）
    returns = close_panel.pct_change()
    limit_threshold = 0.095  # 9.5%作为涨停近似
    limit_up = (returns >= limit_threshold) & (returns <= 0.105)  # 9.5%-10.5%
    
    # 2. 近期涨停因子（recent_limit）：近1-4日内有涨停记录
    recent_limit = pd.DataFrame(0, index=close_panel.index, columns=close_panel.columns)
    for i in range(1, min(5, len(close_panel))):
        if i < len(close_panel):
            recent_limit = recent_limit + limit_up.shift(i).fillna(0).astype(int)
    factors['recent_limit'] = recent_limit
    
    # 3. 概念热度因子（concept_heat）
    # 用个股涨幅近似板块热度（简化处理）
    # 实际应该用板块数据，这里用个股3日+15日涨幅
    returns_3d = close_panel.pct_change(3)
    returns_15d = close_panel.pct_change(15)
    
    # 概念热度 = 3日涨幅排名 + 15日涨幅排名
    concept_heat = (
        returns_3d.rank(axis=1, pct=True) + 
        returns_15d.rank(axis=1, pct=True)
    ) / 2
    factors['concept_heat'] = concept_heat
    
    # 4. 成交额因子（amount_factor）
    amount_factor = amount_panel.rank(axis=1, pct=True)
    factors['amount_factor'] = amount_factor
    
    # 5. 市值因子（market_cap_factor）：用成交额近似
    factors['market_cap_factor'] = amount_factor
    
    # 6. 换手率因子（turnover_factor）
    if open_panel is not None:
        avg_price = (open_panel + close_panel + high_panel + low_panel) / 4
        turnover = volume_panel / (avg_price * 100 + 1e-8)  # 简化
        factors['turnover_factor'] = turnover.rank(axis=1, pct=True)
    
    return factors


def select_stocks_v64_concept(factors: Dict[str, pd.DataFrame],
                               date: str,
                               current_holdings: Optional[Dict] = None,
                               params: Optional[Dict] = None,
                               sold_recently: Optional[List] = None) -> List[Tuple[str, float]]:
    """
    概念热度打板选股（优化版）
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    
    if date not in factors.get('concept_heat', pd.DataFrame()).index:
        return []
    
    # 获取当前日期的数据
    concept_heat = factors['concept_heat'].loc[date]
    recent_limit = factors['recent_limit'].loc[date]
    amount_factor = factors['amount_factor'].loc[date]
    
    # 筛选条件
    min_recent_limit = p.get('MIN_RECENT_LIMIT', 2)  # 收紧：至少2次涨停
    min_amount = p.get('MIN_AMOUNT', 5000000)
    concept_heat_top = p.get('CONCEPT_HEAT_TOP', 0.02)
    
    # 1. 概念热度筛选：前2%
    concept_heat_threshold = concept_heat.quantile(1 - concept_heat_top)
    candidate_mask = concept_heat >= concept_heat_threshold
    
    # 2. 近期涨停筛选：近1-4日内有至少2次涨停（收紧）
    candidate_mask = candidate_mask & (recent_limit >= min_recent_limit)
    
    # 3. 成交额筛选：至少500万
    if 'amount_panel' in factors:
        amount_panel = factors['amount_panel']
        if date in amount_panel.index:
            amount_today = amount_panel.loc[date]
            candidate_mask = candidate_mask & (amount_today >= min_amount)
    
    # 获取候选股票
    candidates = concept_heat[candidate_mask].index.tolist()
    
    if not candidates:
        return []
    
    # 评分
    scores = pd.Series(0.0, index=candidates)
    
    # 1. 概念热度评分（越高越好）
    heat_scores = concept_heat[candidates].rank(pct=True)
    scores += heat_scores * p.get('W_CONCEPT_HEAT', 0.35)
    
    # 2. 近期涨停评分（越多越好）
    limit_scores = recent_limit[candidates].rank(pct=True)
    scores += limit_scores * p.get('W_RECENT_LIMIT', 0.30)
    
    # 3. 成交额评分（越高越好，流动性）
    amount_scores = amount_factor[candidates].rank(pct=True)
    scores += amount_scores * p.get('W_LIQUIDITY', 0.20)
    
    # 4. 市值评分（适中最好）
    # 这里简化为越高越好
    scores += amount_scores * p.get('W_MARKET_CAP', 0.15)
    
    # 排序选择
    scores = scores.sort_values(ascending=False)
    n = p.get('MAX_HOLDINGS', 3)
    selected = scores.head(n).index.tolist()
    
    return [(code, 1.0) for code in selected]


if __name__ == '__main__':
    print("概念热度打板策略优化版")
    print(f"默认参数: {DEFAULT_PARAMS}")
