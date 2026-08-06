#!/usr/bin/env python3
"""v75b: 科技趋势增强 + 板块MA50过滤
熊市自动空仓，只在板块趋势向上时开仓。
基于v75a_tech_momentum.py，增加板块趋势判断。
"""

import sqlite3
import pandas as pd
import numpy as np
from core.strategy_map import load_strategy

DEFAULT_PARAMS = {
    "STOP_LOSS": -0.08,
    "TAKE_PROFIT": 0.30,
    "HOLD_DAYS_MAX": 15,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.35,
    "MAX_HOLDINGS": 3,
    "REBALANCE_DAYS": 10,
    "W_BREAKOUT": 0.45,
    "W_VOL_SURGE": 0.30,
    "W_LIQUIDITY": 0.25,
}

TECH_INDUSTRIES = {'电子', '计算机', '通信', '传媒'}

def _load_industry_map():
    conn = sqlite3.connect('data/quant_stocks.db')
    df = pd.read_sql('SELECT code, industry FROM industry_map', conn)
    conn.close()
    return dict(zip(df['code'], df['industry']))

def calc_factors_v75b(close_panel, volume_panel, amount_panel,
                          high_panel, low_panel, open_panel, params=None):
    """计算v75b因子：突破+放量+流动性，返回含板块趋势信号的因子"""
    codes = close_panel.columns.tolist()
    dates = close_panel.index
    industry_map = _load_industry_map()

    # 过滤科技板块
    tech_codes = [c for c in codes if industry_map.get(c) in TECH_INDUSTRIES]
    if not tech_codes:
        return {'score': pd.DataFrame(0.0, index=dates, columns=codes),
                'sector_bull': pd.Series(False, index=dates)}

    close = close_panel[tech_codes]
    volume = volume_panel[tech_codes]
    amount = amount_panel[tech_codes]

    # === 板块趋势信号 ===
    sector_index = close.mean(axis=1)
    sector_ma50 = sector_index.rolling(50, min_periods=30).mean()
    sector_bull = sector_index > sector_ma50  # True = 牛市

    # === 因子计算 ===
    # 1. 突破分数
    high_20 = close.rolling(20, min_periods=10).max()
    low_20 = close.rolling(20, min_periods=10).min()
    range_20 = high_20 - low_20
    range_20 = range_20.replace(0, np.nan)
    breakout = (close - low_20) / range_20

    # 2. 放量分数
    vol_5 = volume.rolling(5, min_periods=3).mean()
    vol_20 = volume.rolling(20, min_periods=10).mean()
    vol_20 = vol_20.replace(0, np.nan)
    vol_surge = vol_5 / vol_20

    # 3. 流动性分数（20日均成交额排名）
    amt_20 = amount.rolling(20, min_periods=10).mean()
    liquidity = amt_20.rank(axis=1, pct=True)

    # 综合评分
    scores = pd.DataFrame(0.0, index=dates, columns=codes)
    for t in range(len(dates)):
        bp = breakout.iloc[t].fillna(0.0)
        vp = vol_surge.iloc[t].fillna(0.0)
        lp = liquidity.iloc[t].fillna(0.0)
        score = bp * 0.45 + vp * 0.30 + lp * 0.25
        for code in score.index:
            if code in scores.columns:
                scores.at[dates[t], code] = score[code]

    return {'score': scores, 'sector_bull': sector_bull}

def select_stocks_v75b(factors, date, close_panel, volume_panel, amount_panel,
                            high_panel, low_panel, open_panel, current_holdings,
                            params=None, sold_recently=None):
    """v75b选股：板块牛市时选科技股，熊市返回空列表"""
    if params is None:
        params = DEFAULT_PARAMS

    # 检查板块趋势
    sector_bull = factors.get('sector_bull')
    if sector_bull is not None and date in sector_bull.index:
        if not sector_bull.loc[date]:
            return []  # 熊市，不开新仓

    score_series = factors['score'].loc[date].dropna()
    if score_series.empty:
        return []

    # 排除已持有
    if current_holdings:
        score_series = score_series.drop(
            list(current_holdings.keys()), errors='ignore')

    # 排除涨停
    try:
        close_today = close_panel.loc[date]
        close_prev = close_panel.iloc[close_panel.index.get_loc(date) - 1]
        pct = (close_today / close_prev - 1).reindex(score_series.index)
        limit_up_mask = pct >= 0.098
        score_series = score_series[~limit_up_mask]
    except Exception:
        pass

    top = score_series.nlargest(params.get('MAX_DAILY_BUY', 3))
    return [(code, score) for code, score in top.items()]
