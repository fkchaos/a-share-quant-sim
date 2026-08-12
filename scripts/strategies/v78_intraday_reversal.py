#!/usr/bin/env python3
"""v78: 日内反转因子
intraday_return = close[t] / open[t] - 1
负向因子：日内涨幅大 → 预期反转下跌 → 排序取负值
"""
import numpy as np
import pandas as pd

DEFAULT_PARAMS = {
    "STOP_LOSS": -0.08,
    "TAKE_PROFIT": 0.25,
    "HOLD_DAYS_MAX": 20,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.35,
    "MAX_HOLDINGS": 3,
    "REBALANCE_DAYS": 10,
    "BREADTH_MA": 20,
    "BREADTH_HIGH": 0.50,
    "BREADTH_LOW": 0.30,
}

def calc_factors_v78(close_panel, volume_panel, amount_panel,
                     high_panel, low_panel, open_panel):
    """计算日内反转因子"""
    factors = {}
    
    # 日内收益率
    intraday_ret = close_panel / open_panel - 1
    
    # 取负值：日内涨幅大 → 负向信号
    factor = -intraday_ret.iloc[-1]
    
    factors["v78_intraday_reversal"] = factor
    return factors

def calc_breadth_v78(close_panel, params=None):
    """计算广度（MA20以上家数占比）"""
    if params is None:
        params = DEFAULT_PARAMS
    ma = close_panel.rolling(params["BREADTH_MA"]).mean()
    above = (close_panel > ma).iloc[-1]
    return above.mean()

def select_stocks_v78(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel,
                      params=None, top_n=None):
    """v78选股：日内反转 + 广度过滤"""
    if params is None:
        params = DEFAULT_PARAMS
    if top_n is None:
        top_n = params["MAX_HOLDINGS"]
    
    # 广度过滤
    breadth = calc_breadth_v78(close_panel, params)
    if breadth < params["BREADTH_LOW"]:
        return []
    
    # 计算因子
    factors = calc_factors_v78(close_panel, volume_panel, amount_panel,
                               high_panel, low_panel, open_panel)
    factor = factors["v78_intraday_reversal"]
    
    # 排序选股（负向因子，取最小值）
    ranked = factor.dropna().sort_values(ascending=True)
    selected = ranked.head(top_n)
    
    return list(selected.index)

if __name__ == "__main__":
    import sys
    sys.path.insert(0, "/root/a-share-quant-sim")
    from core.db import load_panel_from_db
    
    panels, codes = load_panel_from_db(
        start_date='2024-01-01', end_date='2026-06-30',
        need_open=True, need_hl=True, pool='zz1800'
    )
    close, vol, amt, opn, high, low = panels
    
    factors = calc_factors_v78(close, vol, amt, high, low, opn)
    print("v78因子:")
    print(factors["v78_intraday_reversal"].describe())
    
    breadth = calc_breadth_v78(close)
    print(f"\n广度: {breadth:.4f}")
