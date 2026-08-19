# -*- coding: utf-8 -*-
"""
qmt_adapter/data.py — QMT数据适配层
======================================
将QMT的get_market_data_ex()返回值转换为我们load_panel_from_db()的格式。

注意: 本文件运行在QMT内置Python 3.6环境中，必须兼容3.6.8。
      编码声明必须是 #coding:gbk（QMT要求）。
"""
#coding:gbk

import numpy as np
import pandas as pd


def qmt_to_our_format(qmt_data, stock_code):
    """将QMT get_market_data_ex()的单股结果转换为我们的DataFrame格式。

    QMT返回: DataFrame, 列名 ['open','high','low','close','volume','amount']
             索引是日期字符串 'YYYYMMDD' 或时间戳
    我们需要: DataFrame, 列 = ['date','open','high','low','close','vol','amt']
             索引是整数RangeIndex

    Parameters
    ----------
    qmt_data : dict
        QMT get_market_data_ex() 返回的 dict, key=stock_code, value=DataFrame
    stock_code : str
        股票代码, 如 '600000.SH'

    Returns
    -------
    pd.DataFrame
        我们格式的行情数据, 列 = [date, open, high, low, close, vol, amt]
    """
    if stock_code not in qmt_data:
        return pd.DataFrame()

    df = qmt_data[stock_code].copy()

    # QMT列名 → 我们列名
    col_map = {
        'open': 'open',
        'high': 'high',
        'low': 'low',
        'close': 'close',
        'volume': 'vol',
        'amount': 'amt',
    }
    df = df.rename(columns=col_map)

    # 确保有date列
    if 'date' not in df.columns:
        df['date'] = df.index.astype(str)

    # 重置索引
    df = df.reset_index(drop=True)

    # 只保留需要的列
    want_cols = ['date', 'open', 'high', 'low', 'close', 'vol', 'amt']
    have_cols = [c for c in want_cols if c in df.columns]
    df = df[have_cols]

    return df


def load_kline_from_qmt(C, stock_list, period='1d', count=120):
    """从QMT加载K线数据，返回我们格式的DataFrame。

    Parameters
    ----------
    C : ContextInfo
        QMT策略上下文对象
    stock_list : list of str
        股票代码列表, 如 ['600000.SH', '000001.SZ']
    period : str
        K线周期, '1d' / '1m' / '5m' 等
    count : int
        加载的K线根数

    Returns
    -------
    pd.DataFrame
        我们格式的行情, 列 = [date, open, high, low, close, vol, amt]
        如果多股, 按stock代码分组返回dict
    """
    # QMT获取数据（回测用subscribe=False加速）
    qmt_data = C.get_market_data_ex(
        ['open', 'high', 'low', 'close', 'volume', 'amount'],
        stock_list,
        period=period,
        count=count,
        subscribe=False,
    )

    # 转换格式
    if len(stock_list) == 1:
        return qmt_to_our_format(qmt_data, stock_list[0])
    else:
        return {code: qmt_to_our_format(qmt_data, code) for code in stock_list}


def get_close_series(C, stock_code, count=120):
    """获取收盘价序列（numpy array），用于因子计算。

    Parameters
    ----------
    C : ContextInfo
    stock_code : str
    count : int

    Returns
    -------
    np.ndarray
        收盘价数组，从旧到新
    """
    data = C.get_market_data_ex(
        ['close'],
        [stock_code],
        period='1d',
        count=count,
        subscribe=False,
    )
    if stock_code not in data:
        return np.array([])
    return data[stock_code]['close'].values


def get_multi_close(C, stock_list, count=120):
    """获取多股收盘价DataFrame，用于横截面打分。

    Parameters
    ----------
    C : ContextInfo
    stock_list : list of str
    count : int

    Returns
    -------
    pd.DataFrame
        索引=日期, 列=股票代码, 值=收盘价
    """
    data = C.get_market_data_ex(
        ['close'],
        stock_list,
        period='1d',
        count=count,
        subscribe=False,
    )
    result = {}
    for code in stock_list:
        if code in data:
            result[code] = data[code]['close'].values
    return pd.DataFrame(result)
