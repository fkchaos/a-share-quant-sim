#!/usr/bin/env python3
"""v28 滴水穿石因子 IC 分析"""
import sys, os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from core.db import load_panel_from_db

start, end = '2022-01-01', '2026-05-31'
tpl, _ = load_panel_from_db(start, end, need_open=True, need_hl=True)
close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
open_panel = tpl[3]

eps = 1e-10
returns = close_panel.pct_change()

# 计算 v28 因子
vol_5 = volume_panel.rolling(5).mean()
vol_20 = volume_panel.rolling(20).mean()
vr = vol_5 / (vol_20 + eps)

# vol_regime_score
vr_lag = vr.shift(1)
vr_mean_10 = vr.rolling(10).mean()
vr_lag_mean_10 = vr_lag.rolling(10).mean()
cov_vr = ((vr - vr_mean_10) * (vr_lag - vr_lag_mean_10)).rolling(10).mean()
var_vr = vr.rolling(10).std() * vr_lag.rolling(10).std()
vol_regime_score = cov_vr / (var_vr + eps)

# price_inertia
price_inertia = returns.rolling(10).mean() / (returns.rolling(10).std() + eps)

# vol_price_coupling
ret_mean_5 = returns.rolling(5).mean()
vr_mean_5 = vr.rolling(5).mean()
cov_vp = ((returns - ret_mean_5) * (vr - vr_mean_5)).rolling(5).mean()
vol_price_coupling = cov_vp / (returns.rolling(5).std() * vr.rolling(5).std() + eps)

# accumulation_score
mom_5 = close_panel.pct_change(5)
vr_change = vr.pct_change(5)
accumulation_score = mom_5 / (vr_change + eps + 0.5)

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

fwd_5 = close_panel.pct_change(5).shift(-5)
fwd_10 = close_panel.pct_change(10).shift(-10)

factors_to_test = {
    'vol_regime_score (v28)': vol_regime_score,
    'price_inertia (v28)': price_inertia,
    'vol_price_coupling (v28)': vol_price_coupling,
    'accumulation_score (v28)': accumulation_score,
    'mom_5 (v22基线)': close_panel.pct_change(5),
    'illiquidity': 1.0 / (amount_panel.rolling(20).mean() / 1e8 + eps),
    'gap_ratio': (open_panel - close_panel.shift(1)) / (close_panel.shift(1) + eps),
}

print("=" * 70)
print(f"v28 滴水穿石因子 IC 分析 ({start} ~ {end})")
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

    results.sort(key=lambda x: abs(x['IR']), reverse=True)
    print(f"\n  IR 排名 (前视{period}):")
    for i, r in enumerate(results, 1):
        tag = "✅" if abs(r['IR']) > 0.1 else ("⚠️" if abs(r['IR']) > 0.05 else "❌")
        print(f"  {i}. {r['因子']:>35} IR={r['IR']:>7.4f} {tag}")
