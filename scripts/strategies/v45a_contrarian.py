#!/usr/bin/env python3
"""
v45a — 反转策略（Contrarian / Mean-Reversion）

核心逻辑：买5日超跌（mom_5 < -4%）+ 放量（turnover放大）的股票，
持有3-5天赚反弹。

与 v43 追涨的区别：
- v43 追涨：mom_5 > 5%，买连板涨停股 → 必然回调止损
- v45a 反转：mom_5 < -4%，买超跌放量股 → 等反弹

假设：A股短期过度反应（尤其恐慌性下跌）后存在1-5日窗口的反弹概率。
"""

import pandas as pd
import numpy as np


def select_stocks_v45a(date, factors, extra_data=None):
    """
    反转选股：买超跌放量反弹

    参数:
        date: 当前日期
        factors: dict of DataFrames (date as index)
            - mom_5: 5日动量
            - turnover: 换手率
            - illiq: 非流动性
            - size_factor: 市值因子
            - close: 收盘价
            - volume: 成交量
        extra_data: 额外数据（可选）

    返回:
        list of (code, score, reason)
    """
    mom_5 = factors.get('mom_5')
    turnover = factors.get('turnover')
    illiq = factors.get('illiq')

    if mom_5 is None or turnover is None:
        return []

    if date not in mom_5.index:
        return []

    # 当日全市场数据
    day_mom = mom_5.loc[date]
    day_turnover = turnover.loc[date] if date in turnover.index else pd.Series(dtype=float)

    # 反转条件：mom_5 < -4%（超跌）
    candidates = day_mom[day_mom < -0.04].index

    results = []
    for code in candidates:
        if code not in day_turnover.index:
            continue

        mom_val = day_mom[code]
        tr_val = day_turnover[code]

        # 排除换手率异常
        if pd.isna(tr_val) or tr_val <= 0:
            continue

        # 评分逻辑：
        # 1. 动量越跌越好（反转空间大）→ 取负值，越小越好
        # 2. 换手率适中（放量=有资金接盘）
        # 3. 非流动性适中（太低=流动性陷阱，太高=无人关注）

        mom_score = -mom_val  # 动量越负，分数越高

        # 换手率：适中最好（1%-5%），太高或太低都不好
        if 0.01 <= tr_val <= 0.05:
            tr_score = 1.0
        elif tr_val > 0.05:
            tr_score = 0.5
        else:
            tr_score = 0.3

        # 综合评分
        score = mom_score * 0.6 + tr_score * 0.4

        results.append((code, score, f'mom={mom_val:.2%},tr={tr_val:.2%}'))

    # 按评分排序，取 Top 3，返回 (code, score) 列表
    results.sort(key=lambda x: x[1], reverse=True)
    return [(code, score) for code, score, reason in results[:3]]


def calc_factors_v45a(close_panel, volume_panel, float_shares_map, extra_data=None):
    """
    计算反转策略需要的因子

    参数:
        close_panel: 收盘价面板 (index=日期, columns=股票代码)
        volume_panel: 成交量面板
        float_shares_map: {code: float_shares} 流通股本映射
        extra_data: 额外参数

    返回:
        dict of DataFrames
    """
    factors = {}

    # mom_5: 5日动量
    factors['mom_5'] = close_panel.pct_change(periods=5)

    # turnover: 换手率 = volume / float_shares
    if float_shares_map and isinstance(float_shares_map, dict):
        float_shares_df = pd.DataFrame(
            {code: float_shares_map.get(code, np.nan) for code in close_panel.columns},
            index=close_panel.index
        )
        factors['turnover'] = volume_panel / float_shares_df
    else:
        # 无 float_shares 时用 volume 变化率作为替代
        factors['turnover'] = volume_panel.pct_change(periods=5)

    # illiq: 非流动性 = |收益率| / log(成交量)
    ret = close_panel.pct_change()
    factors['illiq'] = np.abs(ret) / np.log(volume_panel.replace(0, np.nan))

    # size_factor: 对数市值的负值（小市值偏好）
    if float_shares_map and isinstance(float_shares_map, dict):
        market_cap = close_panel * pd.DataFrame(
            {code: float_shares_map.get(code, np.nan) for code in close_panel.columns},
            index=close_panel.index
        )
        factors['size_factor'] = -np.log(market_cap.replace(0, np.nan))
    else:
        factors['size_factor'] = -np.log(close_panel.replace(0, np.nan))

    return factors
