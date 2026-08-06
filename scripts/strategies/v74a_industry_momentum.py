#!/usr/bin/env python3
"""v74a: 行业动量增强策略
在v61b低换手+小市值基础上叠加行业动量因子。

IC分析结果（2026-08-05）:
- 行业动量→个股收益 IC=0.1767, IR=0.9575, 胜率83.2%
- v74a-aggressive (25/25/50): IC=0.1609, IR=0.9720, 胜率84.2%
- v61b baseline IC仅-0.0165，行业动量是主要alpha来源
"""

import numpy as np
import pandas as pd
import sqlite3

DEFAULT_PARAMS = {
    'MAX_HOLDINGS': 5,
    'REBALANCE_DAYS': 5,
    'STOP_LOSS': -0.08,
    'TAKE_PROFIT': 0.25,
    'HOLD_DAYS_MAX': 5,
    # 权重：保守版(35/35/30) vs 激进版(25/25/50)
    'W_TURNOVER': 0.25,
    'W_SIZE': 0.25,
    'W_INDUSTRY_MOM': 0.50,
}


def _load_industry_map(codes):
    """加载行业映射"""
    conn = sqlite3.connect('data/quant_stocks.db', timeout=15)
    df = pd.read_sql_query(
        'SELECT code, industry FROM industry_map WHERE industry != ""',
        conn, index_col='code'
    )
    conn.close()
    return df['industry'].reindex(codes)


def calc_factors_v74a(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel=None, extra_data=None):
    """计算行业动量+换手率+小市值因子"""
    import sqlite3 as sq3

    codes = close_panel.columns.tolist()
    dates = close_panel.index

    # 1. 加载流通股本（用于换手率计算）
    conn = sq3.connect('data/quant_stocks.db', timeout=15)
    fs = pd.read_sql_query(
        'SELECT code, float_shares FROM stock_pool_zz1800',
        conn, index_col='code'
    )['float_shares']
    conn.close()
    fs_arr = fs.reindex(codes).fillna(fs.median())

    # 2. 换手率（负向）
    turnover = volume_panel.mul(100).div(fs_arr, axis=1)
    turn_5 = turnover.rolling(5, min_periods=3).mean()

    # 3. 市值（负向）
    market_cap = close_panel.mul(fs_arr, axis=1)

    # 4. 行业动量（向量化版本）
    industry_map = _load_industry_map(codes)
    returns = close_panel.pct_change(fill_method=None)

    # 为每只股票分配行业标签（用pandas map，避免Python循环）
    code_series = pd.Series(codes)
    ind_labels = code_series.map(industry_map)

    # 构建行业收益率矩阵 (n_dates x n_industries)
    unique_inds = ind_labels.dropna().unique()
    ind_ret = pd.DataFrame(0.0, index=dates, columns=unique_inds)
    for ind in unique_inds:
        ind_codes_list = [codes[i] for i in range(len(codes)) if ind_labels.iloc[i] == ind]
        if len(ind_codes_list) >= 3:
            ind_ret[ind] = returns[ind_codes_list].mean(axis=1)

    # 行业20日动量
    ind_mom_20 = ind_ret.rolling(20, min_periods=10).sum()

    # 映射回个股（向量化：每只股票取其行业的动量值）
    ind_mom = pd.DataFrame(0.0, index=dates, columns=codes)
    for c in codes:
        ind = industry_map.get(c)
        if ind and ind in ind_mom_20.columns:
            ind_mom[c] = ind_mom_20[ind]

    # 5. 最新一天的因子值
    t5 = turn_5.iloc[-1]
    sz = market_cap.iloc[-1]
    im = ind_mom.iloc[-1]

    # 5.1 修正：无行业映射的股票（ind_mom=0.0）设为NaN，让rank时用中位数填充
    #     避免0.0被当作最低行业动量排到最后
    has_industry = industry_map.notna()
    im = im.copy()
    im[~has_industry] = np.nan

    # 6. rank 评分
    w_t = DEFAULT_PARAMS['W_TURNOVER']
    w_s = DEFAULT_PARAMS['W_SIZE']
    w_i = DEFAULT_PARAMS['W_INDUSTRY_MOM']

    scores = pd.Series(0.0, index=codes)

    # 低换手 rank（负向，换手低=高分）
    valid_turn = (-t5).dropna()
    if len(valid_turn) > 50:
        r_turn = valid_turn.rank(ascending=True, pct=True)
        scores += w_t * r_turn.reindex(codes, fill_value=0.5)

    # 小市值 rank（负向，市值小=高分）
    valid_sz = (-sz).dropna()
    if len(valid_sz) > 50:
        r_sz = valid_sz.rank(ascending=True, pct=True)
        scores += w_s * r_sz.reindex(codes, fill_value=0.5)

    # 行业动量 rank（正向，动量高=高分）
    valid_im = im.dropna()
    if len(valid_im) > 50:
        r_im = valid_im.rank(ascending=True, pct=True)
        scores += w_i * r_im.reindex(codes, fill_value=0.5)

    result = scores.sort_values(ascending=False)
    if isinstance(result, pd.Series):
        return {"v74a": result}
    return result


def select_stocks_v74a(factors, date, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, current_holdings,
                       params=None, sold_recently=None):
    """选股: 等权评分后选前N只"""
    p = params or DEFAULT_PARAMS
    n = p.get('MAX_HOLDINGS', 5)

    # 兼容 dict 和 Series
    if isinstance(factors, dict):
        scores = list(factors.values())[0]
    else:
        scores = factors

    candidates = scores.head(n * 2).index.tolist()
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [c for c in candidates if c not in held]

    return [(code, round(scores.get(code, 0), 4)) for code in buy_list[:n]]
