#!/usr/bin/env python3
"""v77: 隔夜收益率因子
overnight_return = open[t] / close[t-1] - 1
负向因子：隔夜高开 → 次日反转下跌 → 排序取负值
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
    "W_OVERNIGHT": 1.0,  # 单因子，权重1.0
    "OVERNIGHT_MA_WINDOW": 5,  # 滚动均值窗口
    # 广度过滤（与v75j共用）
    "BREADTH_MA": 20,
    "BREADTH_HIGH": 0.50,
    "BREADTH_LOW": 0.30,
}


def calc_factors_v77(close_panel, volume_panel, amount_panel,
                     high_panel, low_panel, open_panel,
                     extra_data=None, params=None):
    """计算隔夜收益率因子
    
    Args:
        close_panel: 收盘价面板 (dates x stocks)
        volume_panel: 成交量面板
        amount_panel: 成交额面板
        high_panel: 最高价面板
        low_panel: 最低价面板
        open_panel: 开盘价面板
        extra_data: 额外数据（未使用）
        params: 运行时参数（必须从这里读取动态参数）
    
    Returns:
        dict: {"v77_overnight": pd.Series} 因子值（负值=买入候选）
    """
    if params is None:
        params = DEFAULT_PARAMS
    
    ma_window = params.get("OVERNIGHT_MA_WINDOW", 5)
    
    # 隔夜收益率 = 今日开盘 / 昨日收盘 - 1
    # open_panel[t] / close_panel[t-1] - 1
    overnight_ret = open_panel / close_panel.shift(1) - 1
    
    # 滚动均值平滑
    overnight_ma = overnight_ret.rolling(window=ma_window, min_periods=1).mean()
    
    # 负向因子：隔夜高开（正值）→ 排序取负 → 低排序 = 买入候选
    factor = -overnight_ma
    
    # 取最新截面
    latest_factor = factor.iloc[-1]
    
    return {"v77_overnight": latest_factor}


def select_stocks_v77(scores, top_n=3, return_all=False):
    """选股：选因子值最高的（即隔夜收益率最低的）
    
    Args:
        scores: dict {stock_code: factor_score}
        top_n: 选股数量
        return_all: 是否返回所有候选
    
    Returns:
        list: 选中的股票代码列表
    """
    if not scores:
        return []
    
    # 按分数降序排列（分数越高 = 隔夜收益率越低 = 买入候选）
    sorted_stocks = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    
    if return_all:
        return [code for code, _ in sorted_stocks]
    
    return [code for code, _ in sorted_stocks[:top_n]]


def calc_breadth_v77(close_panel, params=None):
    """计算广度指标（与v75j共用逻辑）
    
    Args:
        close_panel: 收盘价面板
        params: 参数
    
    Returns:
        float: 广度值（MA20以上家数占比）
    """
    if params is None:
        params = DEFAULT_PARAMS
    
    ma_window = params.get("BREADTH_MA", 20)
    
    # 计算每只股票的MA
    stock_ma = close_panel.rolling(window=ma_window, min_periods=1).mean()
    
    # 最新截面：收盘价 > MA 的比例
    latest_close = close_panel.iloc[-1]
    latest_ma = stock_ma.iloc[-1]
    
    above_ma = (latest_close > latest_ma).sum()
    total = len(latest_close.dropna())
    
    if total == 0:
        return 0.5
    
    return above_ma / total


if __name__ == "__main__":
    # 测试
    import sys
    sys.path.insert(0, "/root/a-share-quant-sim")
    from core.db import load_panel_from_db
    
    close, vol, amt, opn, high, low = load_panel_from_db(
        pool="zz1800", start="2024-01-01", end="2026-06-30"
    )
    
    factors = calc_factors_v77(close, vol, amt, high, low, opn)
    print(f"Factor keys: {list(factors.keys())}")
    print(f"Factor shape: {factors['v77_overnight'].shape}")
    print(f"Factor sample:\n{factors['v77_overnight'].head(10)}")
    
    breadth = calc_breadth_v77(close)
    print(f"\nBreadth: {breadth:.4f}")
