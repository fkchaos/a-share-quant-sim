#!/usr/bin/env python3
"""v61c: v61b + 情绪择时因子（基于IC分析结果）"""
import sys, os
sys.path.insert(0, '/root/a-share-quant-sim')
import numpy as np, pandas as pd

# === 默认参数 ===
DEFAULT_PARAMS = {
    'STOP_LOSS': -0.08,
    'TAKE_PROFIT': 0.25,
    'HOLD_DAYS_MAX': 5,
    'REBALANCE_DAYS': 5,
    'MAX_HOLDINGS': 5,
    # 情绪择时参数
    'SENTIMENT_FACTOR': 'avg_amplitude',  # IC最高的因子
    'SENTIMENT_MODE': 'hot',  # hot=因子高时交易, cold=因子低时交易
    'SENTIMENT_THRESHOLD': 0.5,  # 分位数阈值
    'SENTIMENT_WINDOW': 20,  # 滚动窗口
}

def calc_factors(close, turnover, mcap):
    """计算v61b基础因子 + 情绪因子"""
    # v61b基础因子
    turn_5 = turnover.rolling(5, min_periods=3).mean()
    
    # 情绪因子
    daily_ret = close.pct_change()
    ret_5d = daily_ret.rolling(5, min_periods=3).mean()
    ret_20d = daily_ret.rolling(20, min_periods=10).mean()
    
    # avg_amplitude: 平均振幅（IC=+0.224）
    close_range = close.rolling(5).max() - close.rolling(5).min()
    avg_amplitude = (close_range / close.rolling(5).mean()).mean(axis=1)
    
    # volatility_20d: 20日波动率（IC=+0.167）
    volatility_20d = daily_ret.rolling(20, min_periods=10).std().mean(axis=1)
    
    # vol_change: 波动率变化（IC=+0.161）
    vol_20 = daily_ret.rolling(20).std().mean(axis=1)
    vol_60 = daily_ret.rolling(60).std().mean(axis=1)
    vol_change = vol_20 / vol_60.replace(0, np.nan)
    
    # breadth_ma5: 站上5日均线比例（IC=-0.123，反向）
    ma5 = close.rolling(5, min_periods=3).mean()
    breadth_ma5 = (close > ma5).sum(axis=1) / close.shape[1]
    
    # return_dispersion: 收益离散度（IC=+0.110）
    return_dispersion = daily_ret.rolling(20, min_periods=10).std().mean(axis=1)
    
    # avg_return_5d: 5日平均收益（IC=-0.174，反向）
    avg_return_5d = ret_5d.mean(axis=1)
    
    return {
        'turn_5': turn_5,
        'avg_amplitude': avg_amplitude,
        'volatility_20d': volatility_20d,
        'vol_change': vol_change,
        'breadth_ma5': breadth_ma5,
        'return_dispersion': return_dispersion,
        'avg_return_5d': avg_return_5d,
    }

def select_stocks(date, close, turnover, mcap, factors, params):
    """选股逻辑（v61b基础 + 情绪过滤）"""
    top_n = params.get('MAX_HOLDINGS', 5)
    rebal_days = params.get('REBALANCE_DAYS', 5)
    
    # 情绪过滤
    sentiment_factor = params.get('SENTIMENT_FACTOR', 'avg_amplitude')
    sentiment_mode = params.get('SENTIMENT_MODE', 'hot')
    sentiment_threshold = params.get('SENTIMENT_THRESHOLD', 0.5)
    sentiment_window = params.get('SENTIMENT_WINDOW', 20)
    
    if sentiment_factor in factors:
        sf = factors[sentiment_factor]
        if date in sf.index:
            # 计算滚动分位数
            recent = sf.loc[:date].tail(sentiment_window)
            if len(recent) >= sentiment_window:
                current_pct = (recent < sf.loc[date]).mean()
                
                if sentiment_mode == 'hot':
                    # hot模式：因子高时交易
                    if current_pct < sentiment_threshold:
                        return [], []  # 不交易
                else:
                    # cold模式：因子低时交易
                    if current_pct > sentiment_threshold:
                        return [], []  # 不交易
    
    # v61b选股逻辑
    t5 = factors['turn_5'].loc[date] if date in factors['turn_5'].index else None
    sz = mcap.loc[date] if date in mcap.index else None
    
    if t5 is None or sz is None:
        return [], []
    
    scores = pd.Series(0.0, index=close.columns)
    for f in (-t5, -sz):
        valid = f.dropna()
        if len(valid) >= 50:
            ranked = valid.rank(ascending=True, pct=True)
            scores[ranked.index] += ranked
    
    valid_codes = [c for c in scores.dropna().index
                  if close.at[date, c] > 0 and turnover.at[date, c] > 0]
    scores = scores[valid_codes].sort_values(ascending=False)
    
    cands = scores.head(top_n).index.tolist()
    return [], cands  # to_sell由risk_check处理

def risk_check(date, holdings, close, params):
    """风控检查"""
    to_sell = []
    sl = params.get('STOP_LOSS', -0.08)
    tp = params.get('TAKE_PROFIT', 0.25)
    hold_max = params.get('HOLD_DAYS_MAX', 5)
    
    for code, pos in holdings.items():
        if code in close.columns:
            p = close.at[date, code]
            if not np.isnan(p):
                pnl = (p - pos['cost']) / pos['cost']
                if pnl <= sl or pnl >= tp:
                    to_sell.append(code)
                    continue
                pos['days'] = pos.get('days', 0) + 1
                if pos['days'] >= hold_max:
                    to_sell.append(code)
    
    return to_sell
