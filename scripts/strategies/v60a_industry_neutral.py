#!/usr/bin/env python3
"""
scripts/strategies/v60a_industry_neutral.py — v60a 行业中性化策略
=============================================================================
v39g 选股逻辑 + 行业均值中性化评分
"""
import numpy as np
import pandas as pd
from scripts.strategies.v39g_optimized import DEFAULT_PARAMS
from scripts.strategies.v39c_pv_resonance import _score_column


def calc_factors_v60a(close_panel, volume_panel, amount_panel,
                     high_panel, low_panel, open_panel=None, **kwargs):
    """复用 v39g/c 的因子计算"""
    from scripts.strategies.v39c_pv_resonance import calc_factors
    return calc_factors(close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel)


def _load_industry_map():
    import sqlite3
    conn = sqlite3.connect('data/quant_stocks.db')
    df = pd.read_sql("SELECT code, industry FROM industry_map", conn)
    conn.close()
    return dict(zip(df['code'], df['industry']))


def _neutralize_score(scores, industry_map):
    """对一个 Series 做行业均值减法"""
    ind_series = pd.Series({c: industry_map.get(c, 'UNKNOWN') for c in scores.index})
    ind_means = scores.groupby(ind_series).transform('mean')
    return scores - ind_means


_SELECT_CACHE = {}

def select_stocks_v60a(factors, date, current_holdings=None, params=None,
                        sold_recently=None):
    """v60a 选股: v39g + 行业中性化"""
    p = {**DEFAULT_PARAMS, **(params or {})}

    if factors is None or 'mom_5' not in factors or date not in factors['mom_5'].index:
        return []

    # 懒加载行业映射
    if 'industry' not in _SELECT_CACHE:
        _SELECT_CACHE['industry'] = _load_industry_map()
    industry_map = _SELECT_CACHE['industry']

    m5 = factors['mom_5'].loc[date].dropna()
    candidates = list(m5.index)
    candidates = [c for c in candidates if m5[c] > p.get("MOM_THRESHOLD", 0.03)]
    if not candidates:
        return []

    if date in factors.get('pv_corr_10', pd.DataFrame()).index:
        pv10 = factors['pv_corr_10'].loc[date]
        candidates = [c for c in candidates if c in pv10.index and pv10[c] >= p.get("PV_CORR_10_MIN", -0.5)]
    if current_holdings:
        candidates = [c for c in candidates if c not in current_holdings]
    if sold_recently:
        candidates = [c for c in candidates if c not in sold_recently]
    if not candidates:
        return []

    scores = pd.Series(0.0, index=candidates, dtype=float)
    for fname, wkey in [('mom_5', 'W_MOM'), ('pv_corr_20', 'W_PV_CORR'),
                         ('size_factor', 'W_SIZE'), ('illiq', 'W_ILLIQ')]:
        if p.get(wkey, 0) > 0:
            f_scores = _score_column(factors, date, fname)
            scores += f_scores.reindex(candidates).fillna(0) * p[wkey]
    if p.get("W_TURNOVER", 0) > 0:
        scores += _score_column(factors, date, 'turnover_avg', clip_min=0, clip_max=0.05).reindex(candidates).fillna(0) * p["W_TURNOVER"]
    if p.get("W_GAP", 0) > 0:
        scores += _score_column(factors, date, 'gap_ratio', clip_min=0, clip_max=0.05).reindex(candidates).fillna(0) * p["W_GAP"]
    if p.get("W_FUND_FLOW", 0) > 0:
        scores += _score_column(factors, date, 'fund_flow', clip_min=0.5, clip_max=3.0).reindex(candidates).fillna(0) * p["W_FUND_FLOW"]

    # 行业中性化（对总分做）
    scores = _neutralize_score(scores, industry_map)

    scores = scores.sort_values(ascending=False)
    selected = scores.index[:p.get("MAX_DAILY_BUY", 4)]
    return [(code, scores[code]) for code in selected]
