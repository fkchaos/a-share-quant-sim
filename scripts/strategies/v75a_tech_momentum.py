#!/usr/bin/env python3
"""v75a: 科技趋势增强策略
锁定电子/计算机/通信/传媒板块，选趋势确认+放量突破+高流动性个股。
设计目标：牛市场景下极度放大收益（集中持仓+趋势确认）。
持仓周期10天（比v61b更长，让趋势发展）。

IC分析发现：科技板块内突破信号在弱市是反向因子，只有强牛市才正。
因此本策略定位为牛市弹性最大化，不追求全周期有效。
"""

import numpy as np
import pandas as pd

DEFAULT_PARAMS = {
    'MAX_HOLDINGS': 3,      # 集中持仓（3只）
    'REBALANCE_DAYS': 10,    # 每10天调仓
    'STOP_LOSS': -0.08,
    'TAKE_PROFIT': 0.30,     # 止盈30%（让趋势发展）
    'HOLD_DAYS_MAX': 15,     # 最多持15天
    'W_BREAKOUT': 0.45,
    'W_VOL_SURGE': 0.30,
    'W_LIQUIDITY': 0.25,
}

# 科技板块行业
TECH_INDUSTRIES = {'电子', '计算机', '通信', '传媒'}


def _load_industry_map(codes):
    import sqlite3
    conn = sqlite3.connect('data/quant_stocks.db', timeout=15)
    df = pd.read_sql_query(
        'SELECT code, industry FROM industry_map WHERE industry != ""',
        conn, index_col='code'
    )
    conn.close()
    return df['industry'].reindex(codes)


def calc_factors_v75a(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel=None, extra_data=None,
                      weights=None, windows=None):
    """计算科技趋势增强因子（突破+放量+流动性）
    weights: 可选dict，覆盖默认权重 {'W_BREAKOUT': x, 'W_VOL_SURGE': x, 'W_LIQUIDITY': x}
    windows: 可选dict，覆盖默认窗口 {'BREAKOUT': 20, 'VOL_SHORT': 5, 'VOL_LONG': 20, 'LIQ': 20}
    """
    codes = close_panel.columns.tolist()
    dates = close_panel.index

    # 窗口参数
    w = windows or {}
    brk_win = w.get('BREAKOUT', 20)
    vol_short = w.get('VOL_SHORT', 5)
    vol_long = w.get('VOL_LONG', 20)
    liq_win = w.get('LIQ', 20)

    # 1. 行业映射，筛选科技板块
    industry_map = _load_industry_map(codes)
    tech_mask = industry_map.isin(TECH_INDUSTRIES)

    # 非科技板块设为NaN（不参与排名）
    scores = pd.Series(np.nan, index=codes)

    # 2. 突破分数：当前价格在N日区间中的位置
    high_N = high_panel.rolling(brk_win, min_periods=brk_win//2).max()
    low_N = low_panel.rolling(brk_win, min_periods=brk_win//2).min()
    range_N = high_N - low_N
    range_N = range_N.replace(0, np.nan)
    breakout = (close_panel - low_N) / range_N

    # 3. 放量确认：short日均量 / long日均量
    vol_s = volume_panel.rolling(vol_short, min_periods=max(2, vol_short//2)).mean()
    vol_l = volume_panel.rolling(vol_long, min_periods=max(3, vol_long//2)).mean()
    vol_l = vol_l.replace(0, np.nan)
    vol_ratio = vol_s / vol_l

    # 4. 流动性：M日均成交额
    amount_M = amount_panel.rolling(liq_win, min_periods=max(3, liq_win//2)).mean()

    # 5. 最新一天因子值
    bs = breakout.iloc[-1]
    vr = vol_ratio.iloc[-1]
    liq = amount_M.iloc[-1]

    # 6. rank评分（仅科技板块）
    w = weights or DEFAULT_PARAMS
    w_bs = w.get('W_BREAKOUT', DEFAULT_PARAMS['W_BREAKOUT'])
    w_vr = w.get('W_VOL_SURGE', DEFAULT_PARAMS['W_VOL_SURGE'])
    w_lq = w.get('W_LIQUIDITY', DEFAULT_PARAMS['W_LIQUIDITY'])

    # 突破分数 rank
    valid_bs = bs[tech_mask].dropna()
    if len(valid_bs) > 20:
        r_bs = valid_bs.rank(ascending=True, pct=True)
        scores[r_bs.index] = w_bs * r_bs

    # 放量 rank
    valid_vr = vr[tech_mask].dropna()
    if len(valid_vr) > 20:
        r_vr = valid_vr.rank(ascending=True, pct=True)
        scores[r_vr.index] += w_vr * r_vr

    # 流动性 rank
    valid_lq = liq[tech_mask].dropna()
    if len(valid_lq) > 20:
        r_lq = valid_lq.rank(ascending=True, pct=True)
        scores[r_lq.index] += w_lq * r_lq

    result = scores.dropna().sort_values(ascending=False)
    return {"v75a": result}


def select_stocks_v75a(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None, return_all=False):
    """选股：选前N只科技趋势股。return_all=True时返回前10候选（用于展示排名）"""
    p = params or DEFAULT_PARAMS
    n = p.get('MAX_HOLDINGS', 3)
    display_n = 10 if return_all else n

    if isinstance(factors, dict):
        scores = list(factors.values())[0]
    else:
        scores = factors

    # 科创板过滤（688/689开头）——排序前过滤，确保Top10不含科创板
    scores = scores[~scores.index.str.startswith(('688', '689'))]
    candidates = scores.head(max(n * 3, display_n)).index.tolist()
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [c for c in candidates if c not in held]

    return [(code, round(scores.get(code, 0), 4)) for code in buy_list[:display_n]]
