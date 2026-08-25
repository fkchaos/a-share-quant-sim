#coding:gbk
"""
v61c_qmt.py - v61c QMT实盘版
从qmt_data.py读取静态数据，不依赖DB。

Python 3.6.8兼容。
"""

import numpy as np
import pandas as pd
from qmt_data import FLOAT_SHARES

DEFAULT_PARAMS = {
    'MAX_HOLDINGS': 5,
    'REBALANCE_DAYS': 5,
    'STOP_LOSS': -0.08,
    'TAKE_PROFIT': 0.25,
    'HOLD_DAYS_MAX': 5,
    'SELL_OUT_OF': 15,
}

def calc_factors_v61c(C, stock_list, count=30):
    data = C.get_market_data_ex(
        ['open', 'high', 'low', 'close', 'volume', 'amount'],
        stock_list, period='1d', count=count, subscribe=False,
    )

    close_list, vol_list, high_list, low_list = [], [], [], []
    for code in stock_list:
        if code in data and len(data[code]) > 0:
            df = data[code]
            close_list.append(df['close'])
            vol_list.append(df['volume'])
            high_list.append(df['high'])
            low_list.append(df['low'])

    if not close_list:
        return {}

    close_panel = pd.concat(close_list, axis=1)
    volume_panel = pd.concat(vol_list, axis=1)
    high_panel = pd.concat(high_list, axis=1)
    low_panel = pd.concat(low_list, axis=1)
    codes = close_panel.columns.tolist()

    fs_arr = pd.Series({c: FLOAT_SHARES.get(c, 0) for c in codes})
    fs_arr = fs_arr[fs_arr > 0]

    if len(fs_arr) < 50:
        return {}

    valid_codes = fs_arr.index.tolist()
    turnover = volume_panel[valid_codes].mul(100).div(fs_arr, axis=1)
    turn_5 = turnover.rolling(5, min_periods=3).mean()
    t5 = turn_5.iloc[-1]

    market_cap = close_panel[valid_codes].mul(fs_arr, axis=1)
    sz = market_cap.iloc[-1]

    scores = pd.Series(0.0, index=valid_codes)
    for f in [-t5, -sz]:
        valid = f.dropna()
        if len(valid) > 50:
            ranked = valid.rank(ascending=True, pct=True)
            scores[ranked.index] += ranked

    return scores.sort_values(ascending=False)

def select_stocks_v61c(C, current_holdings, params=None):
    p = params or DEFAULT_PARAMS
    n = p.get('MAX_HOLDINGS', 5)

    from qmt_data import ZZ1800_STOCKS
    scores = calc_factors_v61c(C, ZZ1800_STOCKS)
    if not scores:
        return []

    candidates = scores.head(n * 2).index.tolist()
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [c for c in candidates if c not in held]

    return [(code, round(scores.get(code, 0), 4)) for code in buy_list[:n]]
