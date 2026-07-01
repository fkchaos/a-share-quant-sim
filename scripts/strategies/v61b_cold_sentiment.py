#!/usr/bin/env python3
"""
v61b_cold: v61b + 冷清择时
情绪 < 阈值时才交易
"""
import numpy as np
import pandas as pd

DEFAULT_PARAMS = {
    'MAX_HOLDINGS': 5,
    'REBALANCE_DAYS': 5,
    'STOP_LOSS': -0.08,
    'TAKE_PROFIT': 0.25,
    'HOLD_DAYS_MAX': 5,
    'SENTIMENT_THRESHOLD': 2.0,  # 情绪阈值
    'SENTIMENT_WINDOW': 20,      # 情绪窗口
}

def calc_factors_v61b_cold(close_panel, volume_panel, amount_panel, 
                          high_panel=None, low_panel=None, open_panel=None,
                          extra_data=None):
    """计算v61b因子 + 情绪因子"""
    import sqlite3
    conn = sqlite3.connect('data/quant_stocks.db', timeout=15)
    fs = pd.read_sql_query('SELECT code, float_shares FROM stock_pool_zz1800', conn, index_col='code')['float_shares']
    conn.close()
    
    codes = close_panel.columns.tolist()
    fs_arr = fs.reindex(codes).fillna(fs.median())
    
    # 换手率
    turnover = volume_panel.mul(100).div(fs_arr, axis=1)
    turn_5 = turnover.rolling(5, min_periods=3).mean()
    
    # 市值
    market_cap = close_panel.mul(fs_arr, axis=1)
    
    # v61b评分
    t5 = turn_5.iloc[-1]
    sz = market_cap.iloc[-1]
    scores = pd.Series(0.0, index=codes)
    for f in [-t5, -sz]:
        valid = f.dropna()
        if len(valid) > 50:
            ranked = valid.rank(ascending=True, pct=True)
            scores[ranked.index] += ranked
    
    # 情绪因子
    daily_ret = close_panel.pct_change()
    is_limit = ((daily_ret >= 0.095) & (daily_ret <= 0.105)).astype(float).fillna(0)
    two_day_limit = (is_limit.shift(1).fillna(0) == 1) & (is_limit == 1)
    two_day_limit = two_day_limit.astype(float)
    daily_limit_count = two_day_limit.sum(axis=1)
    sentiment = daily_limit_count.rolling(DEFAULT_PARAMS['SENTIMENT_WINDOW']).mean()
    
    return {
        "v61b_cold": scores,
        "sentiment": sentiment
    }

def select_stocks_v61b_cold(factors, date, current_holdings=None, params=None,
                            sold_recently=None):
    """v61b选股 + 冷清择时"""
    p = {**DEFAULT_PARAMS, **(params or {})}
    
    # 检查情绪
    if 'sentiment' in factors and date in factors['sentiment'].index:
        sent = factors['sentiment'].loc[date]
        if sent >= p['SENTIMENT_THRESHOLD']:
            return []  # 市场活跃，不交易
    
    # v61b选股
    if isinstance(factors, dict):
        scores = list(factors.values())[0]
    else:
        scores = factors
    
    n = p.get('MAX_HOLDINGS', 5)
    candidates = scores.head(n * 2).index.tolist()
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [c for c in candidates if c not in held]
    
    return [(code, 1.0) for code in buy_list[:n]]
