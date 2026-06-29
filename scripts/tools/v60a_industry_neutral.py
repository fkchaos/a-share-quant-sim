#!/usr/bin/env python3
"""
scripts/tools/v60a_industry_neutral.py — 行业中性化因子选股
=============================================================
在 v39g 选股基础上，对评分因子做行业均值中性化

回测条件: train=252, test=126, step=63, 2021-01-01~2026-06-24, zz1800
标杆: v39g (Sharpe 1.297)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from core.db import load_panel_from_db
from core.strategy_map import load_strategy
from scripts.backtest.strategy_adapter import get_adapter, StrategyAdapter
from scripts.backtest.wf_runner import run_wf, _calc_factors
from scripts.strategies.v39c_pv_resonance import calc_factors as v39g_calc_factors


def load_industry_map():
    """加载股票→行业映射"""
    import sqlite3
    conn = sqlite3.connect('data/quant_stocks.db')
    df = pd.read_sql("SELECT code, industry FROM industry_map", conn)
    conn.close()
    return dict(zip(df['code'], df['industry']))


def neutralize_factors(factors, date, industry_map):
    """对当日因子做行业均值中性化"""
    neutralized = {}
    for fname, fpanel in factors.items():
        if date not in fpanel.index:
            neutralized[fname] = fpanel
            continue
        s = fpanel.loc[date].copy()  # Series(stock → value)
        if not isinstance(s, pd.Series):
            neutralized[fname] = fpanel
            continue
        # 行业分组
        ind_map = {c: industry_map.get(c, 'UNKNOWN') for c in s.index}
        ind_series = pd.Series(ind_map)
        industry_means = s.groupby(ind_series).transform('mean')
        s_neutral = s - industry_means
        new_panel = fpanel.copy()
        new_panel.loc[date] = s_neutral
        neutralized[fname] = new_panel
    return neutralized


def select_stocks_v60a(factors, date, current_holdings=None, params=None,
                        sold_recently=None, industry_map=None):
    """v60a: v39g 选股 + 行业中性化评分"""
    from scripts.strategies.v39g_optimized import select_stocks_v39g, DEFAULT_PARAMS
    from scripts.strategies.v39c_pv_resonance import _score_column

    p = {**DEFAULT_PARAMS, **(params or {})}

    if date not in factors.get('mom_5', pd.DataFrame()).index:
        return []

    # 行业中性化
    if industry_map:
        factors = neutralize_factors(factors, date, industry_map)

    # 复用 v39g 选股逻辑
    m5 = factors['mom_5'].loc[date].dropna()
    candidates = list(m5.index)
    candidates = [c for c in candidates if m5[c] > p["MOM_THRESHOLD"]]
    if not candidates:
        return []

    if date in factors.get('pv_corr_10', pd.DataFrame()).index:
        pv10 = factors['pv_corr_10'].loc[date]
        candidates = [c for c in candidates if c in pv10.index and pv10[c] >= p["PV_CORR_10_MIN"]]
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
        to_scores = _score_column(factors, date, 'turnover_avg', clip_min=0, clip_max=0.05)
        scores += to_scores.reindex(candidates).fillna(0) * p["W_TURNOVER"]
    if p.get("W_GAP", 0) > 0:
        gap_scores = _score_column(factors, date, 'gap_ratio', clip_min=0, clip_max=0.05)
        scores += gap_scores.reindex(candidates).fillna(0) * p["W_GAP"]
    if p.get("W_FUND_FLOW", 0) > 0:
        ff_scores = _score_column(factors, date, 'fund_flow', clip_min=0.5, clip_max=3.0)
        scores += ff_scores.reindex(candidates).fillna(0) * p["W_FUND_FLOW"]

    scores = scores.sort_values(ascending=False)
    selected = scores.index[:p["MAX_DAILY_BUY"]]
    return [(code, scores[code]) for code in selected]


def main():
    print("=" * 60)
    print("v60a: 行业中性化选股 WF 回测")
    print("=" * 60)

    industry_map = load_industry_map()
    print(f"行业映射: {len(industry_map)} 只股票")
    industries = set(industry_map.values())
    print(f"行业数: {len(industries)}")

    # 加载数据
    tpl, codes = load_panel_from_db(start_date='2021-01-01', end_date='2026-06-24',
                                     pool='zz1800', need_open=True, need_hl=True)
    cp, vp, ap, op, hp, lp = tpl
    print(f"Panel: {cp.shape[0]} days x {cp.shape[1]} stocks")

    # 计算 v39g 因子
    factors = v39g_calc_factors(cp, vp, ap, hp, lp, op)
    print(f"因子: {list(factors.keys())}")

    # 测试中性化效果
    test_date = cp.index[-100]
    m5_orig = factors['mom_5'].loc[test_date].dropna()
    factors_neutral = neutralize_factors(factors, test_date, industry_map)
    m5_neutral = factors_neutral['mom_5'].loc[test_date].dropna()

    print(f"\n中性化效果抽样 (date={test_date}):")
    print(f"  原始 mom_5: mean={m5_orig.mean():.4f}, std={m5_orig.std():.4f}")
    print(f"  中性 mom_5: mean={m5_neutral.mean():.4f}, std={m5_neutral.std():.4f}")

    # 行业分布对比
    ind_series = pd.Series({c: industry_map.get(c, 'UNKNOWN') for c in m5_orig.index})
    orig_by_ind = m5_orig.groupby(ind_series).mean()
    neutral_by_ind = m5_neutral.groupby(ind_series).mean()
    print(f"\n  原始因子行业均值差异: std={orig_by_ind.std():.4f}")
    print(f"  中性因子行业均值差异: std={neutral_by_ind.std():.4f}")

    print("\n中性化验证完成。下一步: 接入 WF 框架做完整回测。")


if __name__ == "__main__":
    main()
