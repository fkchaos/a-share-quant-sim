#coding:gbk
"""
v75j_qmt.py - v75j QMT实盘版
流动性单因子 + 广度过滤，科技板块专用

Python 3.6.8兼容。
"""

import numpy as np
import pandas as pd
from qmt_data import FLOAT_SHARES, INDUSTRY_MAP

DEFAULT_PARAMS = {
    'MAX_HOLDINGS': 3,
    'REBALANCE_DAYS': 10,
    'STOP_LOSS': -0.08,
    'TAKE_PROFIT': 0.25,
    'HOLD_DAYS_MAX': 20,
    'BREADTH_MA': 20,
    'BREADTH_HIGH': 0.50,
    'BREADTH_LOW': 0.30,
}

TECH_SECTORS = ['电子', '计算机', '通信', '传媒']

def _get_tech_codes():
    return [c for c, ind in INDUSTRY_MAP.items() if ind in TECH_SECTORS]

def calc_factors_v75j(C, stock_list, count=30):
    data = C.get_market_data_ex(
        ['open', 'high', 'low', 'close', 'volume', 'amount'],
        stock_list, period='1d', count=count, subscribe=False,
    )

    close_list, vol_list, amount_list, high_list, low_list = [], [], [], [], []
    for code in stock_list:
        if code in data and len(data[code]) > 0:
            df = data[code]
            close_list.append(df['close'])
            vol_list.append(df['volume'])
            amount_list.append(df['amount'])
            high_list.append(df['high'])
            low_list.append(df['low'])

    if not close_list:
        return {}

    close_panel = pd.concat(close_list, axis=1)
    volume_panel = pd.concat(vol_list, axis=1)
    amount_panel = pd.concat(amount_list, axis=1)
    high_panel = pd.concat(high_list, axis=1)
    low_panel = pd.concat(low_list, axis=1)
    codes = close_panel.columns.tolist()

    tech_codes = _get_tech_codes()
    tech_codes_in_pool = [c for c in codes if c in tech_codes]

    if len(tech_codes_in_pool) < 10:
        return {}

    liq_win = 20
    amount_M = amount_panel[tech_codes_in_pool].rolling(liq_win, min_periods=10).mean()
    liq = amount_M.iloc[-1]

    scores = pd.Series(0.0, index=tech_codes_in_pool)
    valid = liq.dropna()
    if len(valid) > 10:
        ranked = valid.rank(ascending=True, pct=True)
        scores[ranked.index] = ranked

    return scores.sort_values(ascending=False)

def _calc_breadth(C, count=30):
    tech_codes = _get_tech_codes()
    if not tech_codes:
        return 1.0

    data = C.get_market_data_ex(
        ['close'], tech_codes, period='1d', count=count, subscribe=False,
    )

    above = 0
    total = 0
    for code in tech_codes:
        if code in data and len(data[code]) >= 20:
            close = data[code]['close'].values
            ma20 = np.mean(close[-20:])
            if close[-1] > ma20:
                above += 1
            total += 1

    return above / total if total > 0 else 1.0

def select_stocks_v75j(C, current_holdings, params=None):
    p = params or DEFAULT_PARAMS
    n = p.get('MAX_HOLDINGS', 3)

    breadth = _calc_breadth(C)
    low_thresh = p.get('BREADTH_LOW', 0.30)
    high_thresh = p.get('BREADTH_HIGH', 0.50)

    if breadth < low_thresh:
        print('[v75j] breadth=%.2f < %.2f, skip' % (breadth, low_thresh))
        return []

    if breadth < high_thresh:
        n = max(1, int(n * breadth / high_thresh))
        print('[v75j] breadth=%.2f, reduce to %d stocks' % (breadth, n))

    from qmt_data import ZZ1800_STOCKS
    tech_codes = _get_tech_codes()
    stock_list = [c for c in ZZ1800_STOCKS if c in tech_codes]

    scores = calc_factors_v75j(C, stock_list)
    if not scores:
        return []

    candidates = scores.head(n * 2).index.tolist()
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [c for c in candidates if c not in held]

    return [(code, round(scores.get(code, 0), 4)) for code in buy_list[:n]]
