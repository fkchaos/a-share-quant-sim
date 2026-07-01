#!/usr/bin/env python3
"""
scripts/strategies/v70_midcap_momentum.py — v70 中盘域动量策略
====================================================
针对中盘域（100-500亿市值）的特点设计：
- 成长性好，但波动也大
- 机构和散户混合参与
- 需要平衡动量和质量

因子设计：
1. mom_20: 20日动量（捕捉中期趋势）
2. quality: ROE/PE（质量因子）
3. vol_20: 20日波动率（低波偏好）
4. reversal_5: 5日反转（超跌反弹）

选股逻辑：
- 硬筛选：市值100-500亿
- 软评分：动量30% + 质量20% + 低波20% + 反转30%
"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional, Tuple


DEFAULT_PARAMS = {
    # ── 风控参数 ──
    "STOP_LOSS": -0.06,           # 止损6%
    "TAKE_PROFIT": 0.12,          # 止盈12%
    "HOLD_DAYS_MAX": 5,           # 最长持有5天
    "HOLD_DAYS_EXTEND": 3,        # 延长3天
    "HOLD_DAYS_EXTEND_PNL": 0.08, # 盈利8%时延长
    "MAX_DAILY_BUY": 3,           # 每日最多买3只
    "MAX_POSITION": 0.25,         # 单只最大25%仓位
    "MAX_HOLDINGS": 4,            # 最多持有4只
    "COOLDOWN_DAYS": 0,           # 冷却期0天

    # ── 选股门槛 ──
    "MOM_THRESHOLD": -0.10,       # 动量>-10%（允许轻微下跌）
    "MIN_MARKET_CAP": 100e8,      # 最小市值100亿
    "MAX_MARKET_CAP": 500e8,      # 最大市值500亿

    # ── 评分权重 ──
    "W_MOM": 0.30,                # 动量权重30%
    "W_QUALITY": 0.20,            # 质量权重20%
    "W_LOW_VOL": 0.20,            # 低波权重20%
    "W_REVERSAL": 0.30,           # 反转权重30%
}


def calc_factors_v70(close_panel: pd.DataFrame, 
                     volume_panel: pd.DataFrame,
                     amount_panel: pd.DataFrame,
                     high_panel: pd.DataFrame,
                     low_panel: pd.DataFrame,
                     open_panel: pd.DataFrame = None,
                     extra_data: dict = None) -> Dict[str, pd.DataFrame]:
    """
    计算v70因子
    
    Args:
        close_panel: 收盘价面板 (dates × codes)
        volume_panel: 成交量面板
        amount_panel: 成交额面板
        high_panel: 最高价面板
        low_panel: 最低价面板
        open_panel: 开盘价面板
        extra_data: 额外数据（市值等）
        
    Returns:
        Dict: 因子字典
    """
    factors = {}
    
    # 1. 20日动量
    factors['mom_20'] = close_panel.pct_change(20)
    
    # 2. 20日波动率（低波因子，取负值）
    returns = close_panel.pct_change()
    factors['vol_20'] = returns.rolling(20).std()
    
    # 3. 质量因子（ROE/PE代理）
    # 由于我们没有真实的ROE/PE数据，用价格动量/波动率作为代理
    # 质量 = 动量/波动率（夏普比率的简化版）
    mom_20 = factors['mom_20']
    vol_20 = factors['vol_20']
    factors['quality'] = mom_20 / (vol_20 + 1e-8)
    
    # 4. 5日反转因子（超跌反弹）
    factors['reversal_5'] = -close_panel.pct_change(5)  # 取负值，跌得多=高分
    
    # 5. 市值因子（用于筛选）
    if extra_data and 'market_cap' in extra_data:
        factors['market_cap'] = extra_data['market_cap']
    
    return factors


def select_stocks_v70(factors: Dict[str, pd.DataFrame],
                      date: str,
                      current_holdings: Optional[Dict] = None,
                      params: Optional[Dict] = None,
                      sold_recently: Optional[List] = None,
                      domain_codes: Optional[List[str]] = None) -> List[Tuple[str, float]]:
    """
    v70选股
    
    Args:
        factors: 因子字典
        date: 日期
        current_holdings: 当前持仓
        params: 参数
        sold_recently: 近期卖出的股票
        domain_codes: 域内股票列表（中盘域）
        
    Returns:
        List[Tuple[str, float]]: [(code, weight), ...]
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    
    if date not in factors['mom_20'].index:
        return []
    
    # 获取当日因子值
    mom_20 = factors['mom_20'].loc[date].dropna()
    vol_20 = factors['vol_20'].loc[date].dropna()
    quality = factors['quality'].loc[date].dropna()
    
    # 候选股票
    candidates = list(mom_20.index)
    
    # 硬筛选1：动量>阈值
    candidates = [c for c in candidates if mom_20[c] > p["MOM_THRESHOLD"]]
    
    # 硬筛选2：域内股票（如果指定）
    if domain_codes:
        candidates = [c for c in candidates if c in domain_codes]
    
    # 硬筛选3：市值范围
    if 'market_cap' in factors:
        mc = factors['market_cap']
        if date in mc.index:
            mc_today = mc.loc[date]
            candidates = [c for c in candidates 
                         if c in mc_today.index 
                         and p["MIN_MARKET_CAP"] <= mc_today[c] <= p["MAX_MARKET_CAP"]]
    
    # 排除当前持仓
    if current_holdings:
        candidates = [c for c in candidates if c not in current_holdings]
    
    # 排除近期卖出
    if sold_recently:
        candidates = [c for c in candidates if c not in sold_recently]
    
    if not candidates:
        return []
    
    # 评分
    scores = pd.Series(0.0, index=candidates)
    
    # 动量评分（越大越好）
    if p.get("W_MOM", 0) > 0:
        mom_scores = mom_20.reindex(candidates).rank(pct=True)
        scores += mom_scores * p["W_MOM"]
    
    # 质量评分（越大越好）
    if p.get("W_QUALITY", 0) > 0:
        quality_scores = quality.reindex(candidates).rank(pct=True)
        scores += quality_scores * p["W_QUALITY"]
    
    # 低波评分（波动率越低越好，取负值排名）
    if p.get("W_LOW_VOL", 0) > 0:
        vol_scores = -vol_20.reindex(candidates).rank(pct=True)  # 负向
        vol_scores = vol_scores.rank(pct=True)  # 重新排名
        scores += vol_scores * p["W_LOW_VOL"]
    
    # 反转评分（跌得多=高分）
    if p.get("W_REVERSAL", 0) > 0 and 'reversal_5' in factors:
        reversal_5 = factors['reversal_5'].loc[date].dropna()
        reversal_scores = reversal_5.reindex(candidates).rank(pct=True)
        scores += reversal_scores * p["W_REVERSAL"]
    
    # 排序，选前N只
    scores = scores.sort_values(ascending=False)
    n = p.get("MAX_HOLDINGS", 4)
    selected = scores.head(n * 2).index.tolist()  # 多取一些候选
    
    # 返回选股列表（等权）
    return [(code, 1.0) for code in selected[:n]]


def risk_check_v70(position, date, price_data, params=None, prev_close=None):
    """
    v70风控检查
    
    Args:
        position: 持仓对象
        date: 日期
        price_data: 价格数据
        params: 参数
        prev_close: 前一日收盘价
        
    Returns:
        Tuple[bool, str]: (是否卖出, 原因)
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    
    code = position.security
    current_price = price_data.get(code, {}).get('close', 0)
    cost_price = position.avg_cost
    hold_days = position.hold_days
    
    if current_price <= 0 or cost_price <= 0:
        return False, ""
    
    pnl = (current_price - cost_price) / cost_price
    
    # 止损
    if pnl <= p["STOP_LOSS"]:
        return True, f"止损: {pnl:.2%}"
    
    # 止盈
    if pnl >= p["TAKE_PROFIT"]:
        return True, f"止盈: {pnl:.2%}"
    
    # 超时卖出
    if hold_days >= p["HOLD_DAYS_MAX"]:
        # 延长条件：盈利超过阈值
        if pnl >= p.get("HOLD_DAYS_EXTEND_PNL", 0.08):
            if hold_days < p["HOLD_DAYS_MAX"] + p.get("HOLD_DAYS_EXTEND", 3):
                return False, ""  # 继续持有
        return True, f"超时: {hold_days}天"
    
    return False, ""


if __name__ == '__main__':
    # 测试代码
    print("v70 中盘域动量策略")
    print(f"默认参数: {DEFAULT_PARAMS}")
