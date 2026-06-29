"""
v48 — 事件驱动因子计算模块
三类事件因子：高管增持、限售解禁、分红。
"""
import numpy as np
import pandas as pd
import sqlite3


def compute_insider_factor(trade_date: str, lookback_days: int = 90,
                           db_path: str = 'data/quant_stocks.db') -> pd.Series:
    """
    计算高管增持因子。
    
    逻辑：最近 lookback_days 天内高管净增持金额（增持-减持），
    按时间衰减加权，归一化到 [0, 1]。
    
    Parameters
    ----------
    trade_date : str
        计算日期 (YYYY-MM-DD)。
    lookback_days : int
        回溯天数。
    db_path : str
        数据库路径。
    
    Returns
    -------
    pd.Series
        stock -> insider factor score [0, 1]。
    """
    conn = sqlite3.connect(db_path)
    
    # 查询回溯期内的增减持数据
    query = """
        SELECT code, trade_date, change_shares, price
        FROM insider_trades
        WHERE trade_date >= date(?, '-' || ? || ' days')
          AND trade_date <= ?
          AND change_shares IS NOT NULL
          AND price IS NOT NULL
    """
    df = pd.read_sql_query(query, conn, params=[trade_date, lookback_days, trade_date])
    conn.close()
    
    if len(df) == 0:
        return pd.Series(dtype=float)
    
    # 计算每笔交易的金额
    df['amount'] = df['change_shares'] * df['price']
    
    # 时间衰减权重
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    ref_date = pd.to_datetime(trade_date)
    df['days_ago'] = (ref_date - df['trade_date']).dt.days
    
    # 分段衰减：0-30天=1.0, 30-60天=0.5, 60-90天=0.25
    df['weight'] = 0.25
    df.loc[df['days_ago'] <= 30, 'weight'] = 1.0
    df.loc[(df['days_ago'] > 30) & (df['days_ago'] <= 60), 'weight'] = 0.5
    
    # 加权净增持金额（增持为正，减持为负）
    df['weighted_amount'] = df['amount'] * df['weight']
    
    # 按股票聚合
    net_buy = df.groupby('code')['weighted_amount'].sum()
    
    # 只保留净增持的（减持的设为0）
    net_buy = net_buy.clip(lower=0)
    
    if len(net_buy) == 0:
        return pd.Series(dtype=float)
    
    # 归一化到 [0, 1]
    max_val = net_buy.max()
    if max_val > 0:
        scores = net_buy / max_val
    else:
        scores = net_buy
    
    return scores


def compute_unlock_factor(trade_date: str, forward_days: int = 30,
                          db_path: str = 'data/quant_stocks.db') -> pd.Series:
    """
    计算解禁压力因子（负向）。
    
    逻辑：未来 forward_days 天内有限售解禁的股票，
    解禁比例越大，分数越低。
    
    Parameters
    ----------
    trade_date : str
        计算日期。
    forward_days : int
        前瞻天数。
    db_path : str
        数据库路径。
    
    Returns
    -------
    pd.Series
        stock -> unlock pressure score [0, 1]，1=压力最大。
    """
    conn = sqlite3.connect(db_path)
    
    query = """
        SELECT code, unlock_date, pct_of_circulating
        FROM unlock_events
        WHERE unlock_date >= ?
          AND unlock_date <= date(?, '+' || ? || ' days')
          AND pct_of_circulating IS NOT NULL
    """
    df = pd.read_sql_query(query, conn, params=[trade_date, trade_date, forward_days])
    conn.close()
    
    if len(df) == 0:
        return pd.Series(dtype=float)
    
    # 按股票聚合（取最大解禁比例）
    max_unlock = df.groupby('code')['pct_of_circulating'].max()
    
    # 归一化到 [0, 1]
    max_val = max_unlock.max()
    if max_val > 0:
        scores = max_unlock / max_val
    else:
        scores = max_unlock
    
    return scores


def compute_dividend_factor(trade_date: str, lookback_days: int = 30,
                            db_path: str = 'data/quant_stocks.db') -> 'pd.Series':
    """
    计算分红事件因子。
    
    注意：分红数据通过 akshare 实时获取，不存储在 DB。
    这里预留接口，实际计算在策略中直接调用 akshare。
    
    Returns
    -------
    pd.Series
        stock -> dividend score [0, 1]。
    """
    # 分红数据量小，每次实时拉取成本太高
    # 改为：在数据下载阶段存储到 DB，这里从 DB 读取
    # 当前版本暂不实现，预留接口
    return pd.Series(dtype=float)


def compute_event_factors(trade_date: str, db_path: str = 'data/quant_stocks.db') -> dict:
    """
    计算所有事件因子。
    
    Returns
    -------
    dict
        {'insider': pd.Series, 'unlock': pd.Series, 'dividend': pd.Series}
    """
    return {
        'insider': compute_insider_factor(trade_date, db_path=db_path),
        'unlock': compute_unlock_factor(trade_date, db_path=db_path),
        'dividend': compute_dividend_factor(trade_date, db_path=db_path),
    }
