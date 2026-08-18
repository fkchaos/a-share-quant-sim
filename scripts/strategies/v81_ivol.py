#!/usr/bin/env python3
"""v81: IVOL低波溢价因子

基于factor-factory f0002a，在zz1800池验证IC=0.055，IR=0.34。
设计目的：
1. 验证外部因子在我们框架下的表现
2. 作为第一个从外部因子库引入的因子

IC分析结果：
- IVOL: IC=0.055, IR=0.34, P(>0)=61.6% ✅
- 与v75j流动性因子相关性：-0.25（独立）✅
- risk_on状态下IC更高（0.073 vs 0.050）✅
"""

import numpy as np
import pandas as pd

DEFAULT_PARAMS = {
    # 风控参数
    "STOP_LOSS": -0.08,
    "TAKE_PROFIT": 0.25,
    "HOLD_DAYS_MAX": 20,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.35,
    "MAX_HOLDINGS": 3,
    "REBALANCE_DAYS": 10,
    "MAX_STOCK_PRICE": 300,
    
    # 择时层参数（广度过滤）
    "BREADTH_MA": 20,
    "BREADTH_HIGH": 0.50,
    "BREADTH_LOW": 0.30,
    
    # 选股层参数（IVOL因子）
    "IVOL_WINDOW": 20,
}


def _calc_breadth(close_panel, date, params):
    """计算广度：多少股票收盘价>MA20"""
    ma_period = params.get("BREADTH_MA", 20)
    pos = close_panel.index.get_loc(date)
    if isinstance(pos, slice):
        pos = pos.start
    if pos < ma_period:
        return 1.0
    
    arr = close_panel.values
    close_today = arr[pos]
    ma_vals = np.nanmean(arr[pos-ma_period+1:pos+1], axis=0)
    
    valid = ~(np.isnan(close_today) | np.isnan(ma_vals) | (close_today <= 0))
    if valid.sum() == 0:
        return 1.0
    
    above = ((close_today[valid] > ma_vals[valid])).sum()
    return above / valid.sum()


def calc_factors_v81(close_panel, volume_panel, amount_panel, 
                     high_panel, low_panel, open_panel=None, extra_data=None):
    """计算IVOL因子，返回最新一天的rank分数（与v75a格式一致）"""
    window = DEFAULT_PARAMS.get("IVOL_WINDOW", 20)
    rets = close_panel.pct_change(fill_method=None)
    mkt_ret = rets.mean(axis=1)
    
    # 只算最新一天
    end_idx = len(close_panel) - 1
    start_idx = end_idx - window + 1
    if start_idx < 0:
        return {"v81": pd.Series(dtype=float)}
    
    win_rets = rets.iloc[start_idx:end_idx+1].values  # window+1行，pct_change少一行
    win_mkt = mkt_ret.iloc[start_idx:end_idx+1].values
    
    if len(win_rets) < window:
        return {"v81": pd.Series(dtype=float)}
    
    # 去掉第一行NaN
    win_rets = win_rets[1:]
    win_mkt = win_mkt[1:]
    
    X = np.column_stack([np.ones(len(win_mkt)), win_mkt])
    try:
        XtX_inv = np.linalg.inv(X.T @ X)
    except:
        return {"v81": pd.Series(dtype=float)}
    
    ivols = {}
    for j, asset in enumerate(close_panel.columns):
        y = win_rets[:, j]
        if np.isnan(y).sum() > window - 5:
            continue
        y_clean = np.nan_to_num(y, nan=0.0)
        beta = XtX_inv @ (X.T @ y_clean)
        eps = y_clean - X @ beta
        valid_mask = ~np.isnan(y)
        if valid_mask.sum() < 5:
            continue
        ivols[asset] = np.std(eps[valid_mask])
    
    if not ivols:
        return {"v81": pd.Series(dtype=float)}
    
    # 反向：做多低波动
    ivol_series = -pd.Series(ivols)
    
    # rank评分（百分位排名）
    ranked = ivol_series.rank(ascending=True, pct=True)
    return {"v81": ranked}


def select_stocks_v81(factors, date, close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel, current_holdings,
                      params=None, sold_recently=None, return_all=False):
    """选股：IVOL因子排序 + 广度过滤，返回[(code, score)]元组列表"""
    if params is None:
        params = DEFAULT_PARAMS
    
    # 广度过滤
    breadth = _calc_breadth(close_panel, date, params)
    high_thresh = params.get("BREADTH_HIGH", 0.50)
    low_thresh = params.get("BREADTH_LOW", 0.30)
    
    if breadth < low_thresh:
        return []
    
    # 获取因子值
    if factors is None:
        return []
    
    if isinstance(factors, dict):
        scores = list(factors.values())[0]
    else:
        scores = factors
    
    if not isinstance(scores, pd.Series) or len(scores) == 0:
        return []
    
    n = params.get('MAX_HOLDINGS', 3)
    display_n = 10 if return_all else n
    
    # 过滤科创板
    scores = scores[~scores.index.str.startswith(('688', '689'))]
    
    # 过滤：股价<MAX_STOCK_PRICE，非停牌
    max_price = params.get("MAX_STOCK_PRICE", 300)
    close_today = close_panel.loc[date] if date in close_panel.index else pd.Series(dtype=float)
    
    valid_mask = pd.Series(True, index=scores.index)
    for code in scores.index:
        if code not in close_today.index:
            valid_mask[code] = False
            continue
        price = close_today[code]
        if np.isnan(price) or price <= 0 or price > max_price:
            valid_mask[code] = False
            continue
        if date in volume_panel.index:
            vol_today = volume_panel.loc[date].get(code, 0) if hasattr(volume_panel.loc[date], 'get') else 0
            if vol_today <= 0:
                valid_mask[code] = False
    
    scores = scores[valid_mask].dropna()
    if len(scores) == 0:
        return []
    
    # 排除已持仓
    held = set(current_holdings.keys()) if current_holdings else set()
    candidates = scores.head(max(n * 3, display_n)).index.tolist()
    buy_list = [c for c in candidates if c not in held]
    
    # 线性减仓（广度中间区域）
    if breadth < high_thresh:
        actual_n = max(1, int(n * breadth / high_thresh))
        buy_list = buy_list[:actual_n]
    
    return [(code, round(scores.get(code, 0), 4)) for code in buy_list[:display_n]]
