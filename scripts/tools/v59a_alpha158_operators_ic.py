#!/usr/bin/env python3
"""
scripts/tools/v59a_alpha158_operators_ic.py — 验证 Alpha158 新算子
6种未在我们体系中验证过的算子 × 5个周期 = 50个因子
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from core.db import load_panel_from_db


def calc_ic_series(factor_panel, fwd_ret_panel, dates):
    ics = []
    for date in dates:
        if date not in factor_panel.index or date not in fwd_ret_panel.index:
            continue
        f = factor_panel.loc[date].dropna()
        r = fwd_ret_panel.loc[date].dropna()
        common = f.index.intersection(r.index)
        if len(common) < 30:
            continue
        ic = f.loc[common].corr(r.loc[common], method='spearman')
        if not np.isnan(ic):
            ics.append(ic)
    return pd.Series(ics)


def calc_alpha158_operators(cp, vp, hp, lp, op, windows=[5, 10, 20, 30, 60]):
    eps = 1e-10
    factors = {}
    for N in windows:
        log_vol = np.log(vp + 1)
        corr_vol = cp.rolling(N).corr(log_vol)
        factors[f'CORR_VOL_{N}'] = corr_vol
        min_low = lp.rolling(N).min()
        max_high = hp.rolling(N).max()
        rsv = (cp - min_low) / (max_high - min_low + eps)
        factors[f'RSV_{N}'] = rsv
        delta = cp.diff()
        pos_sum = delta.clip(lower=0).rolling(N).sum()
        abs_sum = delta.abs().rolling(N).sum()
        sump = pos_sum / (abs_sum + eps)
        factors[f'SUMP_{N}'] = sump
        sumd = 2 * sump - 1
        factors[f'SUMD_{N}'] = sumd
        is_up = (cp.diff() > 0).astype(float)
        cntp = is_up.rolling(N).mean()
        factors[f'CNTP_{N}'] = cntp
        cntd = 2 * cntp - 1
        factors[f'CNTD_{N}'] = cntd
        ret_abs = cp.pct_change().abs()
        vol_weighted = ret_abs * vp
        wvma = vol_weighted.rolling(N).std() / (vol_weighted.rolling(N).mean() + eps)
        factors[f'WVMA_{N}'] = wvma
        std_vol = vp.rolling(N).std() / (vp.rolling(N).mean() + eps)
        factors[f'STD_VOL_{N}'] = std_vol
        ma = cp.rolling(N).mean()
        factors[f'MA_dev_{N}'] = ma / (cp + eps)
    return factors


def main():
    print("=" * 70)
    print("v59a: Alpha158 新算子 IC 分析")
    print("=" * 70)
    tpl, codes = load_panel_from_db(start_date='2021-01-01', end_date='2026-06-24',
                                     pool='zz1800', need_open=True, need_hl=True)
    cp, vp, ap, op, hp, lp = tpl
    print(f"Panel: {cp.shape[0]} days x {cp.shape[1]} stocks")
    print("\n计算 Alpha158 算子...")
    factors = calc_alpha158_operators(cp, vp, hp, lp, op)
    print(f"总计: {len(factors)} 个因子")
    holding_periods = [1, 3, 5, 10, 20]
    dates = cp.index.tolist()
    print("\n" + "=" * 70)
    print("IC 分析")
    print("=" * 70)
    all_results = []
    for h in holding_periods:
        fwd_ret = cp.pct_change(h).shift(-h)
        print(f"\n--- 持有 {h} 天 ---")
        for fname, fpanel in sorted(factors.items()):
            ic_s = calc_ic_series(fpanel, fwd_ret, dates)
            if len(ic_s) == 0:
                continue
            ic_mean = ic_s.mean()
            ic_ir = ic_mean / ic_s.std() if ic_s.std() > 0 else 0
            if abs(ic_mean) > 0.03 and abs(ic_ir) > 0.3:
                sig = "OK"
            elif abs(ic_mean) > 0.01:
                sig = "~"
            else:
                sig = "X"
            if abs(ic_mean) > 0.01:
                print(f"  {sig} {fname:20s}: IC={ic_mean:+.4f}  IR={ic_ir:+.4f}  n={len(ic_s)}")
            all_results.append({'factor': fname, 'holding': h, 'ic_mean': ic_mean, 'ic_ir': ic_ir})
    df = pd.DataFrame(all_results)
    strong = df[(df['ic_mean'].abs() > 0.03) & (df['ic_ir'].abs() > 0.3)]
    print("\n" + "=" * 70)
    print(f"有效因子 (|IC|>0.03 & |IR|>0.3): {len(strong)} 个")
    print("=" * 70)
    if len(strong) > 0:
        for _, r in strong.sort_values('ic_mean', key=abs, ascending=False).iterrows():
            direction = "正" if r['ic_mean'] > 0 else "负"
            print(f"  {r['factor']:20s} (h={r['holding']:2d}): IC={r['ic_mean']:+.4f}  IR={r['ic_ir']:+.4f}  [{direction}]")
    else:
        print("  无有效因子")
    print("\n按算子类型最佳:")
    opts = df['factor'].str.extract(r'([A-Z_]+)_')[0].unique()
    for opt in sorted(opts):
        sub = df[df['factor'].str.startswith(opt)]
        if len(sub) == 0:
            continue
        best = sub.loc[sub['ic_mean'].abs().idxmax()]
        print(f"  {opt:15s}: {best['factor']:20s}  IC={best['ic_mean']:+.4f}  IR={best['ic_ir']:+.4f}")
    print("\nDone.")


if __name__ == "__main__":
    main()
