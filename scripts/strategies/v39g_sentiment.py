#!/usr/bin/env python3
"""
v39g_sentiment.py — v39g + 舆情因子组合策略
====================================================
在v39g基础上加入sentiment_momentum因子（负向）

核心逻辑：
- sentiment_momentum = 短期情绪 - 长期情绪
- IC = -0.042（负向：情绪越高→未来收益越低）
- 作为负向因子加入评分（情绪越高→扣分）
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple

# 舆情因子缓存（避免重复API调用）
_SENTIMENT_CACHE = {}


DEFAULT_PARAMS = {
    # ── 风控参数（继承v39g）──
    "STOP_LOSS": -0.05,
    "TAKE_PROFIT": 0.05,
    "HOLD_DAYS_MAX": 3,
    "HOLD_DAYS_EXTEND": 3,
    "HOLD_DAYS_EXTEND_PNL": 0.08,
    "MAX_DAILY_BUY": 4,
    "MAX_POSITION": 0.20,
    "MAX_HOLDINGS": 5,
    "COOLDOWN_DAYS": 0,

    # ── 选股门槛（继承v39g）──
    "MOM_THRESHOLD": 0.03,
    "PV_CORR_10_MIN": -0.5,
    "PV_CORR_20_MIN": 0.0,
    "BOLL_W_MIN": 0.0,

    # ── 评分权重（v39g + 舆情）──
    "W_MOM": 0.10,
    "W_PV_CORR": 0.05,
    "W_TURNOVER": 0.05,
    "W_SIZE": 0.35,           # 降低（为舆情腾出权重）
    "W_FUND_FLOW": 0.05,
    "W_GAP": 0.05,
    "W_ILLIQ": 0.20,
    "W_SENTIMENT": -0.15,     # 新增：舆情动量（负向）
}


def calc_factors_v39g_sentiment(close_panel: pd.DataFrame,
                                 volume_panel: pd.DataFrame,
                                 amount_panel: pd.DataFrame,
                                 high_panel: pd.DataFrame,
                                 low_panel: pd.DataFrame,
                                 open_panel: pd.DataFrame = None,
                                 extra_data: dict = None) -> Dict[str, pd.DataFrame]:
    """
    计算v39g因子 + 舆情因子（优化版：使用缓存，只在首次计算舆情因子）
    """
    from scripts.strategies.v39c_pv_resonance import calc_factors
    
    # 原有v39g因子（无舆情，速度快）
    factors = calc_factors(close_panel, volume_panel, amount_panel,
                           high_panel, low_panel, open_panel)
    
    # 舆情因子使用缓存（避免重复API调用）
    # 选股时再实时计算
    factors['sentiment_momentum'] = pd.DataFrame(
        index=close_panel.index, 
        columns=close_panel.columns, 
        dtype=float
    )
    
    return factors


def select_stocks_v39g_sentiment(factors: Dict[str, pd.DataFrame],
                                  date: str,
                                  current_holdings: Optional[Dict] = None,
                                  params: Optional[Dict] = None,
                                  sold_recently: Optional[List] = None) -> List[Tuple[str, float]]:
    """
    v39g_sentiment 选股（优化版：只在选股时计算舆情因子，使用缓存）
    """
    global _SENTIMENT_CACHE
    
    p = {**DEFAULT_PARAMS, **(params or {})}
    
    if date not in factors.get('mom_5', pd.DataFrame()).index:
        return []
    
    # 原有v39g选股逻辑
    from scripts.strategies.v39g_optimized import select_stocks_v39g
    
    # 先用v39g选股（快速）
    v39g_stocks = select_stocks_v39g(factors, date, current_holdings, params, sold_recently)
    
    if not v39g_stocks:
        return []
    
    # 舆情因子评分（使用缓存）
    sentiment_key = f"{date}"
    if sentiment_key not in _SENTIMENT_CACHE:
        try:
            from core.sentiment.factor_builder import SentimentFactorBuilder
            builder = SentimentFactorBuilder()
            codes = [s[0] for s in v39g_stocks]
            
            # 获取情绪因子
            sentiment_factors = builder.build_all_factors(codes, date)
            _SENTIMENT_CACHE[sentiment_key] = {
                'sentiment_momentum': sentiment_factors.get('sentiment_momentum', {}),
            }
        except Exception as e:
            _SENTIMENT_CACHE[sentiment_key] = {'sentiment_momentum': {}}
    
    # 对v39g选出的股票重新评分
    scores = pd.Series(0.0, index=[s[0] for s in v39g_stocks])
    
    # 原有因子评分
    for fname, wkey in [('mom_5', 'W_MOM'), ('size_factor', 'W_SIZE'), ('illiq', 'W_ILLIQ')]:
        if p.get(wkey, 0) > 0 and fname in factors:
            f_scores = factors[fname].loc[date].reindex(scores.index).rank(pct=True)
            scores += f_scores.fillna(0) * p[wkey]
    
    # 舆情因子评分（负向）
    if p.get('W_SENTIMENT', 0) != 0 and sentiment_key in _SENTIMENT_CACHE:
        sent_data = _SENTIMENT_CACHE[sentiment_key].get('sentiment_momentum', {})
        if sent_data:
            sent_series = pd.Series(sent_data).reindex(scores.index).rank(pct=True)
            scores += sent_series.fillna(0) * p['W_SENTIMENT']
    
    # 重新排序
    scores = scores.sort_values(ascending=False)
    n = p.get('MAX_HOLDINGS', 5)
    selected = scores.head(n).index.tolist()
    
    return [(code, 1.0) for code in selected]


if __name__ == '__main__':
    print("v39g + 舆情因子组合策略")
    print(f"默认参数: {DEFAULT_PARAMS}")
