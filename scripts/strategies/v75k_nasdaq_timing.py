#!/usr/bin/env python3
"""v75k: v75j + 纳斯达克隔夜择时

在v75j（流动性单因子+广度过滤）基础上，叠加一层美股科技板块隔夜信号：
- QQQ前一日跌幅 > 3% → 当日不开新仓
- QQQ前一日跌幅 1~3% → MAX_HOLDINGS 减半
- 其他情况 → 正常（v75j原逻辑）

数据源：data/external/qqq_daily.csv（yfinance拉取的QQQ日线）
"""

import numpy as np
import pandas as pd
import os

from scripts.strategies.v75j_liquidity_only import (
    calc_factors_v75j, select_stocks_v75j,
    _calc_breadth, _load_tech_codes
)

# ── QQQ数据加载 ──
_QQQ_RETURNS = None

def _load_qqq_returns():
    """加载QQQ日线，计算日涨跌幅，返回 {date_str: return} dict"""
    global _QQQ_RETURNS
    if _QQQ_RETURNS is not None:
        return _QQQ_RETURNS
    
    csv_path = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'external', 'qqq_daily.csv')
    if not os.path.exists(csv_path):
        print(f"[v75k] WARNING: QQQ data not found at {csv_path}, falling back to v75j logic")
        _QQQ_RETURNS = {}
        return _QQQ_RETURNS
    
    df = pd.read_csv(csv_path)
    # 去掉时区信息，只保留日期部分
    df['Date'] = df['Date'].str[:10]  # "2020-01-02 00:00:00-05:00" -> "2020-01-02"
    df['ret'] = df['Close'].pct_change()
    _QQQ_RETURNS = dict(zip(df['Date'].iloc[1:], df['ret'].iloc[1:]))
    return _QQQ_RETURNS


DEFAULT_PARAMS = {
    # 风控参数（与v75j相同）
    "STOP_LOSS": -0.08,
    "TAKE_PROFIT": 0.25,
    "HOLD_DAYS_MAX": 20,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.35,
    "MAX_HOLDINGS": 3,
    "REBALANCE_DAYS": 10,
    "MAX_STOCK_PRICE": 300,
    
    # 广度过滤（与v75j相同）
    "BREADTH_MA": 20,
    "BREADTH_HIGH": 0.50,
    "BREADTH_LOW": 0.30,
    
    # 选股因子（与v75j相同：只保留流动性）
    "W_BREAKOUT": 0.0,
    "W_VOL_SURGE": 0.0,
    "W_LIQUIDITY": 1.0,
    
    # ── 纳斯达克择时参数 ──
    "QQQ_GATE_STRONG": -0.03,   # 纳指跌>3% → 不开新仓
    "QQQ_GATE_MILD": -0.01,     # 纳指跌1~3% → 减半持仓
}


def _get_qqq_signal(date):
    """获取QQQ前一日信号
    返回: 'block' | 'reduce' | 'normal'
    """
    qqq_ret = _load_qqq_returns()
    if not qqq_ret:
        return 'normal'
    
    date_str = pd.Timestamp(date).strftime('%Y-%m-%d')
    
    # 找前一个交易日的QQQ收益
    # A股和美股交易日不完全对齐，需要找最近的前一个有数据的日期
    prev_dates = [d for d in qqq_ret.keys() if d <= date_str]
    if not prev_dates:
        return 'normal'
    
    prev_date = max(prev_dates)
    ret = qqq_ret.get(prev_date, 0)
    
    if ret < DEFAULT_PARAMS["QQQ_GATE_STRONG"]:
        return 'block'    # 恐慌传导，不开仓
    elif ret < DEFAULT_PARAMS["QQQ_GATE_MILD"]:
        return 'reduce'   # 谨慎，减仓
    else:
        return 'normal'


def select_stocks_v75k(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """选股：v75j逻辑 + 纳斯达克隔夜择时"""
    if params is None:
        params = DEFAULT_PARAMS
    
    # ── 第一层：纳指隔夜择时 ──
    signal = _get_qqq_signal(date)
    
    if signal == 'block':
        return []  # 纳指大跌，不开新仓
    
    # ── 第二层：广度过滤 + 纳指信号调整 ──
    breadth = _calc_breadth(close_panel, date, params)
    high_thresh = params.get("BREADTH_HIGH", 0.50)
    low_thresh = params.get("BREADTH_LOW", 0.30)
    
    if breadth < low_thresh:
        return []
    
    # 纳指信号调整持仓数
    max_hold = params.get("MAX_HOLDINGS", 3)
    if signal == 'reduce':
        max_hold = max(1, max_hold // 2)
    
    # 线性减仓（中间区域）
    if breadth < high_thresh:
        p = dict(params)
        p["MAX_HOLDINGS"] = max(1, int(max_hold * breadth / high_thresh))
        return select_stocks_v75j(factors, date, close_panel, volume_panel, amount_panel,
                                  high_panel, low_panel, open_panel, current_holdings,
                                  p, sold_recently=sold_recently, return_all=return_all)
    
    # 正常/满仓区域（可能被纳指信号削减）
    if signal == 'reduce':
        p = dict(params)
        p["MAX_HOLDINGS"] = max_hold
        return select_stocks_v75j(factors, date, close_panel, volume_panel, amount_panel,
                                  high_panel, low_panel, open_panel, current_holdings,
                                  p, sold_recently=sold_recently, return_all=return_all)
    
    # 正常信号，走v75j原逻辑
    return select_stocks_v75j(factors, date, close_panel, volume_panel, amount_panel,
                              high_panel, low_panel, open_panel, current_holdings,
                              params, sold_recently=sold_recently, return_all=return_all)


def calc_factors_v75k(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel=None, extra_data=None):
    """计算v75k因子（与v75j完全相同）"""
    return calc_factors_v75j(close_panel, volume_panel, amount_panel,
                             high_panel, low_panel, open_panel, extra_data)
