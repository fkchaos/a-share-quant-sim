"""
core/streak_factor.py — 连板辨识度因子

计算每只股票的"连板记忆效应"：历史上连板次数越多、距离越近，
二次启动的概率越高。

接口:
    compute_streak_factor(panel, date) -> pd.Series
    compute_streak_risk(panel, date) -> pd.Series
"""
import numpy as np
import pandas as pd


def compute_streak_factor(panel, date=None, decay_days=252):
    """
    连板辨识度因子：过去N日内最大连板次数，按距离衰减。
    
    逻辑: 
    - 检测涨停（close == 涨停价，即 close/open > 1.099 且 close == high）
    - 统计连续涨停天数
    - 距离当前日期越近，权重越高
    
    参数:
        panel: (close_panel, volume_panel, ...) 或 dict
        date: 截止日期
        decay_days: 衰减窗口（默认252天=1年）
    
    返回:
        pd.Series: 连板辨识度得分（值越大=连板记忆越强）
    """
    if isinstance(panel, (tuple, list)):
        close = panel[0]
    elif isinstance(panel, dict):
        close = panel.get('close')
    else:
        raise ValueError(f"不支持的 panel 类型: {type(panel)}")
    
    if date is not None:
        close = close[close.index <= date]
    
    if len(close) < 20:
        return pd.Series(0.0, index=close.columns)
    
    # 计算每日涨幅
    returns = close.pct_change()
    
    # 定义涨停：涨幅 >= 9.5%（考虑四舍五入）
    limit_up = returns >= 0.095
    
    # 对每只股票计算连板序列
    scores = pd.Series(0.0, index=close.columns)
    
    for code in close.columns:
        stock_returns = returns[code].dropna()
        stock_limit = limit_up[code].dropna()
        
        if len(stock_limit) < 20:
            continue
        
        # 找连板：连续涨停的天数
        streak_lengths = []
        streak_end_dates = []
        current_streak = 0
        
        for idx in stock_limit.index:
            if stock_limit[idx]:
                current_streak += 1
            else:
                if current_streak >= 2:  # 只记录2+连板
                    streak_lengths.append(current_streak)
                    streak_end_dates.append(idx)
                current_streak = 0
        # 处理最后一组
        if current_streak >= 2:
            streak_lengths.append(current_streak)
            streak_end_dates.append(stock_limit.index[-1])
        
        if not streak_lengths:
            continue
        
        # 计算加权得分：连板次数 * 距离衰减
        last_date = stock_limit.index[-1]
        score = 0.0
        for length, end_date in zip(streak_lengths, streak_end_dates):
            days_since = (last_date - end_date).days
            if days_since < 0:
                days_since = 0
            decay = np.exp(-3.0 * days_since / decay_days)  # 指数衰减
            score += length * decay  # 连板越长、越近 → 得分越高
        
        scores[code] = score
    
    # 标准化
    if scores.std() > 0:
        scores = (scores - scores.mean()) / scores.std()
    
    return scores


def compute_streak_risk(panel, date=None):
    """
    连板风险因子：最近是否有连板（距离当前 < 60天），
    如果有且涨幅已大 → 高风险（应回避）。
    
    返回:
        pd.Series: 0=安全, 1=高风险（近期有2+连板）
    """
    if isinstance(panel, (tuple, list)):
        close = panel[0]
    elif isinstance(panel, dict):
        close = panel.get('close')
    else:
        raise ValueError(f"不支持的 panel 类型: {type(panel)}")
    
    if date is not None:
        close = close[close.index <= date]
    
    if len(close) < 20:
        return pd.Series(0.0, index=close.columns)
    
    returns = close.pct_change()
    limit_up = returns >= 0.095
    
    risk = pd.Series(0.0, index=close.columns)
    
    for code in close.columns:
        stock_limit = limit_up[code].dropna()
        if len(stock_limit) < 20:
            continue
        
        # 检查最近60天是否有2+连板
        recent = stock_limit.tail(60)
        max_streak = 0
        current = 0
        for val in recent:
            if val:
                current += 1
                max_streak = max(max_streak, current)
            else:
                current = 0
        
        if max_streak >= 2:
            risk[code] = 1.0
    
    return risk
