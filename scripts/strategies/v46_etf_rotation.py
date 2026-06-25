#!/usr/bin/env python3
"""
v46 — 行业轮动策略（直接买入行业ETF）

与v39i的区别：
- v39i：全市场选动量最强的个股
- v46：选动量前3的行业ETF，等权重买入

核心逻辑：
1. 用23只行业ETF计算行业动量（5日收益率）
2. 选动量前3的ETF（排除同一sector重复）
3. 等权重买入这3只ETF
"""

import pandas as pd
import numpy as np
import os
import sqlite3


# 行业ETF列表（与数据库中 index_kline 表一致的代码）
INDUSTRY_ETFS = [
    {"code": "sz512480", "name": "半导体ETF", "sector": "科技"},
    {"code": "sz512500", "name": "5GETF", "sector": "科技"},
    {"code": "sz512760", "name": "科创50ETF", "sector": "科技"},
    {"code": "sz512660", "name": "军工ETF", "sector": "军工"},
    {"code": "sz512810", "name": "军工行业ETF", "sector": "军工"},
    {"code": "sz512510", "name": "芯片ETF", "sector": "科技"},
    {"code": "sz512030", "name": "医药ETF", "sector": "医药"},
    {"code": "sz512590", "name": "光伏ETF", "sector": "新能源"},
    {"code": "sz516160", "name": "新能源ETF", "sector": "新能源"},
    {"code": "sz516880", "name": "光伏ETF2", "sector": "新能源"},
    {"code": "sz515030", "name": "新能源车ETF", "sector": "新能源"},
    {"code": "sz512100", "name": "有色ETF", "sector": "周期"},
    {"code": "sz512200", "name": "化工ETF", "sector": "周期"},
    {"code": "sz512300", "name": "保险ETF", "sector": "金融"},
    {"code": "sz512690", "name": "证券ETF", "sector": "金融"},
    {"code": "sz513130", "name": "银行ETF", "sector": "金融"},
    {"code": "sz512800", "name": "地产ETF", "sector": "地产"},
    {"code": "sz512010", "name": "食品饮料ETF", "sector": "消费"},
    {"code": "sz512260", "name": "电子ETF", "sector": "科技"},
    {"code": "sz513030", "name": "医美ETF", "sector": "消费"},
    {"code": "sh512880", "name": "红利ETF", "sector": "策略"},
    {"code": "sz512020", "name": "家电ETF", "sector": "消费"},
    {"code": "sz512640", "name": "游戏ETF", "sector": "科技"},
]

# 去重（按code）
_seen = set()
INDUSTRY_ETFS_UNIQUE = []
for etf in INDUSTRY_ETFS:
    if etf["code"] not in _seen:
        _seen.add(etf["code"])
        INDUSTRY_ETFS_UNIQUE.append(etf)
INDUSTRY_ETFS = INDUSTRY_ETFS_UNIQUE


def _get_etf_kline_from_db(code, start_date, end_date):
    """从 index_kline 表获取ETF K线"""
    db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                          "data", "quant_stocks.db")
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT date, open, high, low, close, volume FROM index_kline "
        "WHERE code=? AND date>=? AND date<=? ORDER BY date",
        (code, start_date, end_date)
    )
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# 缓存：避免重复读取
_etf_cache = {}


def get_etf_kline_cached(code, start_date, end_date):
    """带缓存的ETF K线获取"""
    cache_key = f"{code}_{start_date}_{end_date}"
    if cache_key not in _etf_cache:
        _etf_cache[cache_key] = _get_etf_kline_from_db(code, start_date, end_date)
    return _etf_cache[cache_key]


def calc_etf_momentum(etf_info, start_date, end_date, lookback=5):
    """计算单个ETF的动量（最近lookback日收益率）"""
    code = etf_info["code"]
    kline = get_etf_kline_cached(code, start_date, end_date)
    if kline is None or len(kline) < lookback + 1:
        return None
    # 取最近的 lookback+1 天数据
    total = len(kline)
    start_idx = total - lookback - 1
    close_series = kline["close"]
    recent = close_series.iloc[start_idx:]
    if recent.iloc[0] <= 0:
        return None
    momentum = recent.iloc[-1] / recent.iloc[0] - 1
    return momentum


def select_stocks_v46(date, factors, extra_data=None):
    """
    v46 选股 — 行业轮动，选动量前3的行业ETF

    返回: list of (etf_code, score)
        score = 动量值（用于排序，实际等权重买入）
    """
    start_date = extra_data.get("start_date", "2018-01-01") if extra_data else "2018-01-01"
    end_date = str(date)[:10]

    momentums = []
    for etf in INDUSTRY_ETFS:
        mom = calc_etf_momentum(etf, start_date, end_date, lookback=5)
        if mom is not None:
            momentums.append((etf["code"], mom, etf["sector"], etf["name"]))

    # 按动量降序排列
    momentums.sort(key=lambda x: x[1], reverse=True)

    # 选前3，排除同一sector重复
    selected = []
    selected_sectors = set()
    for code, mom, sector, name in momentums:
        if sector in selected_sectors:
            continue
        selected.append((code, mom))
        selected_sectors.add(sector)
        if len(selected) >= 3:
            break

    return selected


def calc_factors_v46(close_panel, volume_panel, float_shares_map, extra_data=None):
    """
    v46 不需要额外计算因子（ETF动量在 select_stocks_v46 中实时计算）
    """
    return {}
