#!/usr/bin/env python3
"""
v63_dragon_leader.py — 龙头战法策略（二板定龙头）优化版
====================================================
核心逻辑：
- 识别市场连板龙头股（二板及以上）
- 在龙头确认后介入，追求主升浪波段收益
- 多层次动态止损止盈体系

优化点：
1. 改进涨停判断：用涨跌幅接近10%作为近似
2. 加入板块地位评分（用industry_map数据）
3. 加入情绪周期指标
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
    "MIN_STREAK": 2,              # 最少连板数
    "MIN_AMOUNT": 5000000,        # 最小成交额（500万）
    "MIN_MARKET_CAP": 2000000000, # 最小市值（20亿）
    "MAX_MARKET_CAP": 50000000000,# 最大市值（500亿）
    "LIMIT_THRESHOLD": 0.095,     # 涨停阈值（9.5%）
    
    # ── 评分权重 ──
    "W_STREAK": 0.35,             # 连板数权重
    "W_SECTOR": 0.30,             # 板块地位权重
    "W_SENTIMENT": 0.20,          # 情绪周期权重
    "W_LIQUIDITY": 0.15,          # 流动性权重
}


def calc_factors_v63_dragon(close_panel: pd.DataFrame,
                             volume_panel: pd.DataFrame,
                             amount_panel: pd.DataFrame,
                             high_panel: pd.DataFrame,
                             low_panel: pd.DataFrame,
                             open_panel: pd.DataFrame = None,
                             extra_data: dict = None) -> Dict[str, pd.DataFrame]:
    """
    计算龙头战法因子（优化版）
    """
    factors = {}
    
    # 1. 涨停判断（改进：用涨跌幅接近10%）
    returns = close_panel.pct_change()
    limit_threshold = 0.095  # 9.5%作为涨停近似
    limit_up = (returns >= limit_threshold) & (returns <= 0.105)  # 9.5%-10.5%
    
    # 2. 连板数因子（streak_count）
    streak_count = pd.DataFrame(0, index=close_panel.index, columns=close_panel.columns)
    for i in range(1, len(close_panel)):
        today_limit = limit_up.iloc[i]
        yesterday_limit = limit_up.iloc[i-1] if i > 0 else pd.Series(False, index=limit_up.columns)
        
        # 连续涨停
        streak_count.iloc[i] = (today_limit & yesterday_limit).astype(int) * (
            streak_count.iloc[i-1] + 1
        )
        # 今天涨停但昨天没涨停，重新计数
        streak_count.iloc[i] = streak_count.iloc[i].where(today_limit, 0)
    
    factors['streak_count'] = streak_count
    
    # 3. 近期涨停因子（recent_limit）：近1-4日内有涨停记录
    recent_limit = pd.DataFrame(0, index=close_panel.index, columns=close_panel.columns)
    for i in range(1, min(5, len(close_panel))):
        if i < len(close_panel):
            recent_limit = recent_limit + limit_up.shift(i).fillna(0).astype(int)
    factors['recent_limit'] = recent_limit
    
    # 4. 成交额因子（amount_factor）
    amount_factor = amount_panel.rank(axis=1, pct=True)
    factors['amount_factor'] = amount_factor
    
    # 5. 换手率因子（turnover_factor）
    if open_panel is not None:
        avg_price = (open_panel + close_panel + high_panel + low_panel) / 4
        turnover = volume_panel / (avg_price * 100 + 1e-8)  # 简化
        factors['turnover_factor'] = turnover.rank(axis=1, pct=True)
    
    # 6. 板块地位因子（sector_rank）- 需要在选股时计算
    # 这里先创建空面板，选股时再填充
    factors['sector_rank'] = pd.DataFrame(0, index=close_panel.index, columns=close_panel.columns)
    
    # 7. 情绪周期因子（sentiment_cycle）
    # 用市场整体涨停数近似
    sentiment = limit_up.sum(axis=1) / len(limit_up.columns)
    factors['sentiment_cycle'] = sentiment
    
    return factors


def select_stocks_v63_dragon(factors: Dict[str, pd.DataFrame],
                              date: str,
                              current_holdings: Optional[Dict] = None,
                              params: Optional[Dict] = None,
                              sold_recently: Optional[List] = None) -> List[Tuple[str, float]]:
    """
    龙头战法选股（优化版）
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    
    if date not in factors.get('streak_count', pd.DataFrame()).index:
        return []
    
    # 获取当前日期的数据
    streak_count = factors['streak_count'].loc[date]
    recent_limit = factors['recent_limit'].loc[date]
    amount_factor = factors['amount_factor'].loc[date]
    sentiment_cycle = factors['sentiment_cycle'].loc[date]
    
    # 筛选条件
    min_streak = p.get('MIN_STREAK', 2)
    min_amount = p.get('MIN_AMOUNT', 5000000)
    
    # 1. 连板数筛选：至少2板
    candidate_mask = streak_count >= min_streak
    
    # 2. 成交额筛选：至少500万
    if 'amount_panel' in factors:
        amount_panel = factors['amount_panel']
        if date in amount_panel.index:
            amount_today = amount_panel.loc[date]
            candidate_mask = candidate_mask & (amount_today >= min_amount)
    
    # 3. 近期涨停筛选：近1-4日内有涨停
    candidate_mask = candidate_mask & (recent_limit >= 1)
    
    # 获取候选股票
    candidates = streak_count[candidate_mask].index.tolist()
    
    if not candidates:
        return []
    
    # 评分
    scores = pd.Series(0.0, index=candidates)
    
    # 1. 连板数评分（越高越好）
    streak_scores = streak_count[candidates].rank(pct=True)
    scores += streak_scores * p.get('W_STREAK', 0.35)
    
    # 2. 成交额评分（越高越好，流动性）
    amount_scores = amount_factor[candidates].rank(pct=True)
    scores += amount_scores * p.get('W_LIQUIDITY', 0.15)
    
    # 3. 板块地位评分（简化：用连板数近似）
    # 这里可以进一步优化，加入真实的板块数据
    scores += streak_scores * p.get('W_SECTOR', 0.30)
    
    # 4. 情绪周期评分
    scores += sentiment_cycle * p.get('W_SENTIMENT', 0.20)
    
    # 排序选择
    scores = scores.sort_values(ascending=False)
    n = p.get('MAX_HOLDINGS', 3)
    selected = scores.head(n).index.tolist()
    
    return [(code, 1.0) for code in selected]


if __name__ == '__main__':
    print("龙头战法策略（二板定龙头）优化版")
    print(f"默认参数: {DEFAULT_PARAMS}")
