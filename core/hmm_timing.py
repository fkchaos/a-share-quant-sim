"""
HMM 市场状态识别 + 凯利仓位管理
================================

基于隐马尔科夫模型识别市场状态（趋势上涨/震荡/趋势下跌），
用凯利公式计算最优仓位比例。

参考：中邮证券《基于隐马尔科夫链与动态调制的量化择时方案》
      HMM_Opt_Kelly: 年化 20.9%, Sharpe 1.29, 回撤 -11.0%

实现说明：
  不使用完整 HMM 模型（避免引入 hmmlearn 依赖），
  用规则化方法模拟 HMM 状态输出。
  状态识别基于：趋势强度(trend_strength) + 波动率比率(vol_ratio)
"""

import numpy as np
import pandas as pd


def compute_hmm_positions_batch(
    close_panel: pd.DataFrame,
    min_lookback: int = 60,
) -> pd.Series:
    """
    批量计算所有日期的 HMM 仓位比例。

    预计算模式：一次性对所有日期计算仓位，避免在回测循环中重复计算。

    Args:
        close_panel: 收盘价面板 (dates x stocks)
        min_lookback: 最小回溯天数

    Returns:
        Series indexed by date, values in [0, 1] (仓位比例)
    """
    dates = close_panel.index

    # 计算市场代理指数（截面均值收益率）
    stock_returns = close_panel.pct_change().clip(-0.12, 0.12)
    mkt_ret = stock_returns.mean(axis=1)

    # 计算特征
    ret_5d = mkt_ret.rolling(5).sum()
    vol_20d = mkt_ret.rolling(20).std()
    vol_5d = mkt_ret.rolling(5).std()
    vol_ratio = vol_5d / vol_20d.replace(0, np.nan)
    trend_strength = mkt_ret.rolling(20).sum() / (mkt_ret.rolling(20).std() * np.sqrt(20)).replace(0, np.nan)

    # 状态识别
    states = pd.Series(index=dates, dtype=float)
    ts = trend_strength.fillna(0.0)
    vr = vol_ratio.fillna(1.0)

    for date in dates:
        t = ts.loc[date] if date in ts.index else 0.0
        v = vr.loc[date] if date in vr.index else 1.0

        if t > 0.5 and v < 1.5:
            states.loc[date] = 2  # 趋势上涨
        elif t < -0.5 and v > 1.0:
            states.loc[date] = 0  # 趋势下跌
        else:
            states.loc[date] = 1  # 震荡

    # 状态 → 仓位映射
    position_map = {0: 0.25, 1: 0.60, 2: 1.00}
    positions = states.map(position_map)

    # 前期数据不足时满仓
    positions.iloc[:min_lookback] = 1.0
    positions = positions.fillna(1.0)

    return positions


def compute_hmm_position_single(
    close_panel: pd.DataFrame,
    current_idx: int,
    min_lookback: int = 60,
) -> float:
    """
    计算单个日期的 HMM 仓位（用于无法预计算的场景）。

    Args:
        close_panel: 收盘价面板
        current_idx: 当前日期索引
        min_lookback: 最小回溯天数

    Returns:
        仓位比例 [0.0, 1.0]
    """
    if current_idx < min_lookback:
        return 1.0

    sub_panel = close_panel.iloc[:current_idx + 1]
    stock_returns = sub_panel.pct_change().clip(-0.12, 0.12)
    mkt_ret = stock_returns.mean(axis=1)

    ts = mkt_ret.rolling(20).sum() / (mkt_ret.rolling(20).std() * np.sqrt(20)).replace(0, np.nan)
    vol_5d = mkt_ret.rolling(5).std()
    vol_20d = mkt_ret.rolling(20).std()
    vr = vol_5d / vol_20d.replace(0, np.nan)

    t = ts.iloc[-1] if len(ts) > 0 else 0.0
    v = vr.iloc[-1] if len(vr) > 0 else 1.0

    if np.isnan(t): t = 0.0
    if np.isnan(v): v = 1.0

    if t > 0.5 and v < 1.5:
        return 1.00  # 趋势上涨
    elif t < -0.5 and v > 1.0:
        return 0.25  # 趋势下跌
    else:
        return 0.60  # 震荡
