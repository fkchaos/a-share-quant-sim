#coding:gbk
"""
v61c_qmt.py - v61c QMT实盘版
从qmt_data.py读取静态数据，不依赖DB。
"""

import numpy as np
import pandas as pd
from qmt_adapter.qmt_data import FLOAT_SHARES

DEFAULT_PARAMS = {
    'MAX_HOLDINGS': 5,
    'REBALANCE_DAYS': 5,
    'STOP_LOSS': -0.08,
    'TAKE_PROFIT': 0.25,
    'HOLD_DAYS_MAX': 5,
    'SELL_OUT_OF': 15,
}


def calc_factors_v61c(C, stock_list, count=30):
    """QMT版：从C获取行情，用静态数据计算因子"""
    # 获取行情数据
    data = C.get_market_data_ex(
        ['open', 'high', 'low', 'close', 'volume', 'amount'],
        stock_list, period='1d', count=count, subscribe=False,
    )

    # 构建面板
    close_list = []
    vol_list = []
    for code in stock_list:
        if code in data and len(data[code]) > 0:
            df = data[code]
            close_list.append(df['close'])
            vol_list.append(df['volume'])

    if not close_list:
        return {}

    close_panel = pd.concat(close_list, axis=1)
    volume_panel = pd.concat(vol_list, axis=1)
    codes = close_panel.columns.tolist()

    # 流通股本（静态数据）
    fs = pd.Series({c: FLOAT_SHARES.get(c, 0) for c in codes})
    fs = fs.replace(0, fs[fs > 0].median())
    codes_valid = fs[fs > 0].index.tolist()

    close_panel = close_panel[codes_valid]
    volume_panel = volume_panel[codes_valid]
    fs = fs[codes_valid]

    # 换手率 = volume(手)*100 / float_shares(股)
    turnover = volume_panel.mul(100).div(fs, axis=1)
    turn_5 = turnover.rolling(5, min_periods=3).mean()

    # 流通市值
    market_cap = close_panel.mul(fs, axis=1)

    # 最新一天
    t5 = turn_5.iloc[-1]
    sz = market_cap.iloc[-1]

    # rank评分
    scores = pd.Series(0.0, index=codes_valid)
    for f in [-t5, -sz]:
        valid = f.dropna()
        if len(valid) > 50:
            ranked = valid.rank(ascending=True, pct=True)
            scores[ranked.index] += ranked

    return scores.sort_values(ascending=False)


def select_stocks_v61c(C, current_holdings, params=None):
    """选股：调用calc_factors，返回前N只"""
    p = params or DEFAULT_PARAMS
    n = p.get('MAX_HOLDINGS', 5)

    # 获取股票池
    from qmt_adapter.qmt_data import ZZ1800_STOCKS
    stock_list = ZZ1800_STOCKS

    scores = calc_factors_v61c(C, stock_list)
    if scores.empty:
        return []

    candidates = scores.head(n * 2).index.tolist()
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [c for c in candidates if c not in held]

    return [(code, round(scores.get(code, 0), 4)) for code in buy_list[:n]]
