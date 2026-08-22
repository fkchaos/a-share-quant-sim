#!/usr/bin/env python3
"""分数增速排序模块 - 二次排序选出"正在变好"的股票

支持两种模式：
1. 实盘模式：写入DB，读取历史分数
2. 回测模式：内存缓存，不写DB
"""

import sqlite3
import os
import pandas as pd
import numpy as np

DB_PATH = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'quant_stocks.db')
RECORD_TOP_N = 50  # 存储top 50的分数

# 内存缓存（回测模式使用）
_score_cache = {}  # {strategy_name: {date_str: {code: score}}}


def save_scores(strategy_name, date, scores, top_n=RECORD_TOP_N, skip_db=False):
    """保存当天top N的分数到DB"""
    date_str = str(date)[:10]
    top_scores = scores.head(top_n)
    
    if skip_db:
        # 内存缓存模式
        if strategy_name not in _score_cache:
            _score_cache[strategy_name] = {}
        _score_cache[strategy_name][date_str] = {code: float(score) for code, score in top_scores.items()}
        return
    
    conn = sqlite3.connect(DB_PATH, timeout=10)
    
    # 检查当天是否已写过
    exists = conn.execute(
        "SELECT 1 FROM strategy_scores WHERE strategy_name=? AND date=? LIMIT 1",
        (strategy_name, date_str)
    ).fetchone()
    
    if exists:
        conn.close()
        return
    
    # 保存top N
    rows = [(strategy_name, date_str, code, float(score)) 
            for code, score in top_scores.items()]
    
    conn.executemany(
        "INSERT INTO strategy_scores (strategy_name, date, code, score) VALUES (?, ?, ?, ?)",
        rows
    )
    conn.commit()
    conn.close()


def get_yesterday_scores(strategy_name, date, codes=None, skip_db=False, lookback_days=1):
    """获取前N天的分数
    
    Args:
        strategy_name: 策略名
        date: 当前日期
        codes: 可选，只返回这些code的分数
        skip_db: 跳过DB读取（使用内存缓存）
        lookback_days: 回看天数（1=昨天，5=5天前）
    
    Returns:
        pd.Series: index=code, values=score
    """
    date_str = str(date)[:10]
    
    if skip_db:
        # 内存缓存模式
        cache = _score_cache.get(strategy_name, {})
        sorted_dates = sorted(cache.keys(), reverse=True)
        
        # 找lookback_days天前的日期
        target_date = None
        count = 0
        for d in sorted_dates:
            if d < date_str:
                count += 1
                if count >= lookback_days:
                    target_date = d
                    break
        
        if target_date is None:
            return pd.Series(dtype=float)
        
        target_scores = cache.get(target_date, {})
        if codes is not None and len(codes) > 0:
            filtered = {c: s for c, s in target_scores.items() if c in codes}
            return pd.Series(filtered)
        return pd.Series(target_scores)
    
    conn = sqlite3.connect(DB_PATH, timeout=10)
    
    # 找前N天的日期
    rows = conn.execute(
        "SELECT DISTINCT date FROM strategy_scores WHERE strategy_name=? AND date < ? ORDER BY date DESC LIMIT ?",
        (strategy_name, date_str, lookback_days)
    ).fetchall()
    
    if not rows:
        conn.close()
        return pd.Series(dtype=float)
    
    # 取最早的那个日期（lookback_days天前）
    target_date = rows[-1][0]
    
    # 读取那天的分数
    if codes is not None and len(codes) > 0:
        placeholders = ','.join(['?'] * len(codes))
        rows = conn.execute(
            f"SELECT code, score FROM strategy_scores WHERE strategy_name=? AND date=? AND code IN ({placeholders})",
            [strategy_name, target_date] + list(codes)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT code, score FROM strategy_scores WHERE strategy_name=? AND date=?",
            (strategy_name, target_date)
        ).fetchall()
    
    conn.close()
    
    return pd.Series({r[0]: r[1] for r in rows})


def rerank_by_delta(scores, old_scores, top_m=20, delta_days=1):
    """用分数增速二次排序
    
    Args:
        scores: 当天分数 pd.Series
        old_scores: 前N天分数 pd.Series
        top_m: 在前M只中做二次排序
        delta_days: 增速周期（用于日志）
    
    Returns:
        pd.Series，二次排序后的分数
    """
    # 第一步：取top_m
    top_candidates = scores.head(top_m)
    
    # 第二步：计算delta
    deltas = {}
    for code in top_candidates.index:
        if code in old_scores.index:
            delta = top_candidates[code] - old_scores[code]
            deltas[code] = delta
    
    if len(deltas) < 5:
        # 有delta的太少，不排序
        return scores
    
    # 第三步：排序逻辑
    # 有delta的按delta降序（增速快的排前面）
    # 没有delta的排在最后（用原始score兜底）
    delta_series = pd.Series(deltas)
    delta_ranked = delta_series.sort_values(ascending=False)
    
    # 构建最终排序
    result_index = list(delta_ranked.index)
    
    # 加入top_m中没有delta的股票
    for code in top_candidates.index:
        if code not in deltas:
            result_index.append(code)
    
    # 加入top_m以外的股票
    for code in scores.index:
        if code not in result_index:
            result_index.append(code)
    
    # 保持原始分数值，只重排顺序
    return scores.reindex(result_index)
