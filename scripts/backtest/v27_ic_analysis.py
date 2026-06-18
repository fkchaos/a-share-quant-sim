#!/usr/bin/env python3
"""v27 因子 IC 分析 — 验证 pv_corr 和 vol_price_divergence 的选股能力"""
import sys, os
import numpy as np
import pandas as pd

from core.db import load_panel_from_db

start, end = '2022-01-01', '2026-05-31'
tpl, _ = load_panel_from_db(start, end, need_open=True, need_hl=True)
close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
open_panel = tpl[3]

eps = 1e-10
returns = close_panel.pct_change()

# 计算 v27 新因子
vol_5 = volume_panel.rolling(5).mean()
vol_20 = volume_panel.rolling(20).mean()
vr = vol_5 / (vol_20 + eps)
daily_ret = close_panel.pct_change()

def _fast_rolling_corr_panel(ret_df, vol_df, window):
    ret_std = ret_df.rolling(window).std()
    vol_std = vol_df.rolling(window).std()
    ret_mean = ret_df.rolling(window).mean()
    vol_mean = vol_df.rolling(window).mean()
    xy_mean = (ret_df * vol_df).rolling(window).mean()
    cov = xy_mean - ret_mean * vol_mean
    return cov / (ret_std * vol_std + eps)

pv_corr_10 = _fast_rolling_corr_panel(daily_ret, vr, 10)
pv_corr_20 = _fast_rolling_corr_panel(daily_ret, vr, 20)

mom_rank = close_panel.pct_change(5).rank(axis=1, pct=True)
vol_rank = vr.rank(axis=1, pct=True)
vp_div = mom_rank - vol_rank

# 也计算 v17 的因子做对比
pct_dev = (close_panel - close_panel.rolling(20).mean()) / (close_panel.rolling(20).std() + eps)
vr_5 = volume_panel / (volume_panel.rolling(5).mean() + eps)
vr_20_v = volume_panel / (volume_panel.rolling(20).mean() + eps)
vol_accel = vr_5 / (vr_20_v + eps)
pvt = pct_dev * vol_accel  # v17 的 price_volume_tension

# v18 的 vol_of_vol
vol_20_returns = returns.rolling(20).std()
vov = vol_20_returns.rolling(20).std()

# IC 分析
def calc_ic(factor_df, fwd_ret, label):
    ics = []
    for dt in factor_df.index:
        if dt not in fwd_ret.index: continue
        fv = factor_df.loc[dt].dropna()
        rv = fwd_ret.loc[dt].dropna()
        common = fv.index.intersection(rv.index)
        if len(common) < 20: continue
        corr = np.corrcoef(fv[common], rv[common])[0, 1]
        if not np.isnan(corr): ics.append(corr)
    if len(ics) < 5: return None
    ic_mean = np.mean(ics)
    ir = ic_mean / np.std(ics) if np.std(ics) > 0 else 0
    pos_pct = sum(1 for x in ics if x > 0) / len(ics)
    return {'因子': label, 'IC': round(ic_mean, 4), 'IR': round(ir, 4),
            '正IC%': round(pos_pct, 3), 'N': len(ics)}

# 前视 5 天和 10 天的收益
fwd_5 = close_panel.pct_change(5).shift(-5)
fwd_10 = close_panel.pct_change(10).shift(-10)

factors_to_test = {
    'pv_corr_10 (v27新)': pv_corr_10,
    'pv_corr_20 (v27新)': pv_corr_20,
    'vol_price_divergence (v27新)': vp_div,
    'price_volume_tension (v17旧)': pvt,
    'vol_accel (v17旧)': vol_accel,
    'vol_of_vol (v18旧)': vov,
    'mom_5 (v22基线)': close_panel.pct_change(5),
    'illiquidity': 1.0 / (amount_panel.rolling(20).mean() / 1e8 + eps),
    'gap_ratio': (open_panel - close_panel.shift(1)) / (close_panel.shift(1) + eps),
}

print("=" * 70)
print(f"因子 IC 分析 ({start} ~ {end})")
print("=" * 70)

for period, fwd in [('5天', fwd_5), ('10天', fwd_10)]:
    print(f"\n--- 前视 {period} ---")
    print(f"{'因子':>35} | {'IC':>7} | {'IR':>7} | {'正IC%':>6} | {'N':>5}")
    print("-" * 70)
    results = []
    for name, fdf in factors_to_test.items():
        r = calc_ic(fdf, fwd, name)
        if r:
            results.append(r)
            print(f"{r['因子']:>35} | {r['IC']:>7.4f} | {r['IR']:>7.4f} | {r['正IC%']:>6.3f} | {r['N']:>5}")

    # 排序
    results.sort(key=lambda x: abs(x['IR']), reverse=True)
    print(f"\n  IR 排名 (前视{period}):")
    for i, r in enumerate(results[:5], 1):
        tag = "✅" if abs(r['IR']) > 0.1 else ("⚠️" if abs(r['IR']) > 0.05 else "❌")
        print(f"  {i}. {r['因子']:>35} IR={r['IR']:>7.4f} {tag}")
