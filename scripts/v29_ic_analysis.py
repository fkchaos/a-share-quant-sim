#!/usr/bin/env python3
"""v29 球队硬币因子 IC 分析（修正market_regime为Series）"""
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

# v29 因子 - market_regime 必须是 Series（截面指标）
# 用截面中位数/均值代表市场状态，不是个股指标

# 1. 市场趋势：全市场收益率的中位数 MA60 斜率
market_ret = returns.median(axis=1)  # 截面中位数收益 = 市场收益
market_ma60 = market_ret.rolling(60).mean()
market_slope = market_ma60.pct_change(20)

# 2. 市场涨跌比
up_ratio = (returns > 0).sum(axis=1) / returns.shape[1]

# 3. 市场波动率（截面中位数）
vol_median = returns.rolling(20).std().median(axis=1)

# market_regime = 归一化后的综合得分
market_slope_norm = (market_slope - market_slope.rolling(60).mean()) / (market_slope.rolling(60).std() + eps)
up_ratio_norm = (up_ratio - up_ratio.rolling(60).mean()) / (up_ratio.rolling(60).std() + eps)
vol_norm = (vol_median - vol_median.rolling(60).mean()) / (vol_median.rolling(60).std() + eps)

market_regime_raw = market_slope_norm * 0.4 + up_ratio_norm * 0.4 - vol_norm * 0.2
market_regime = (market_regime_raw - 0.5) * 2  # 归一化到 [-1, 1]
market_regime = market_regime.reindex(close_panel.index).ffill().fillna(0)

print(f"market_regime: Series, shape={market_regime.shape}, dtype={market_regime.dtype}")

# adaptive_mom: 用 numpy 广播（现在是 Series，可以正确广播）
mom_5 = close_panel.pct_change(5)
regime_np = market_regime.values[:, np.newaxis]  # (days, 1)
mom_5_np = mom_5.values  # (days, stocks)
print(f"mom_5_np shape: {mom_5_np.shape}, regime_np shape: {regime_np.shape}")

adaptive_mom_np = mom_5_np * (1 + regime_np) + (-mom_5_np) * (1 - regime_np)
adaptive_mom = pd.DataFrame(adaptive_mom_np, index=close_panel.index, columns=close_panel.columns)

# regime_vol_score
vol_20_returns = returns.rolling(20).std()
vol_of_vol = vol_20_returns.rolling(20).std()
vov_np = vol_of_vol.values
regime_vol_score_np = vov_np * (-regime_np)
regime_vol_score = pd.DataFrame(regime_vol_score_np, index=close_panel.index, columns=close_panel.columns)

# coin_flip_signal
coin_flip = market_regime.diff(3)

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
    'adaptive_mom (v29)': adaptive_mom,
    'regime_vol_score (v29)': regime_vol_score,
    'mom_5 (v22基线)': close_panel.pct_change(5),
    'illiquidity': 1.0 / (amount_panel.rolling(20).mean() / 1e8 + eps),
    'gap_ratio': (open_panel - close_panel.shift(1)) / (close_panel.shift(1) + eps),
}

print("=" * 70)
print(f"v29 球队硬币因子 IC 分析 ({start} ~ {end})")
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
