#!/usr/bin/env python3
"""Alpha191 IC batch test — uses project's load_panel_from_db"""
import sys, os
sys.path.insert(0, '.')
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from core.db import load_panel_from_db

print("[1] Loading panel...")
# 只取波动最大、流动性最好的 500 只小票 (zz1800 中的)
# 用 float_shares 排序取小市值
panels, codes = load_panel_from_db(pool='zz1800', start_date='2020-06-01', need_open=True, need_hl=True)
cp, vp, ap, op, hp, lp = panels
print(f"    Full panel: {cp.shape[0]} days x {cp.shape[1]} stocks")

# 只保留日均成交额 > 5000万 的 top 500 只
avg_amt = vp.rolling(20).mean()  # 5000万 = 50_000_000
# 简化: 按 volume 排序取 top 500
mean_vol = vp.mean().sort_values(ascending=False)
top_codes = mean_vol.head(500).index.tolist()
cp, vp, ap, op, hp, lp = cp[top_codes], vp[top_codes], ap[top_codes], op[top_codes], hp[top_codes], lp[top_codes]
print(f"    Reduced to: {cp.shape[0]} days x {cp.shape[1]} stocks")

# forward returns
def fwd(h):
    return cp.shift(-h) / cp - 1

fwd3, fwd5, fwd10 = fwd(3), fwd(5), fwd(10)

# basic ops
def ts_mean(s, d): return s.rolling(d, min_periods=max(2,d//2)).mean()
def ts_std(s, d):  return s.rolling(d, min_periods=max(2,d//2)).std()
def ts_max(s, d):  return s.rolling(d, min_periods=max(2,d//2)).max()
def ts_min(s, d):  return s.rolling(d, min_periods=max(2,d//2)).min()
def shift(s, d):   return s.shift(d)
def delta(s, d):   return s - s.shift(d)

# precompute
print("[2] Precomputing operators...")
C, H, L, O, V, A = cp, hp, lp, op, vp, ap
ops = {}
for d in [1,2,3,5,6,10,12,20,24,30,60]:
    ops[f's{d}_C'] = shift(C,d); ops[f's{d}_H'] = shift(H,d); ops[f's{d}_L'] = shift(L,d)
    ops[f's{d}_O'] = shift(O,d); ops[f's{d}_V'] = shift(V,d)
for d in [3,5,6,10,12,20,24,30,40,60,80]:
    ops[f'm{d}_C'] = ts_mean(C,d); ops[f'm{d}_V'] = ts_mean(V,d)
    ops[f'm{d}_H'] = ts_mean(H,d); ops[f'm{d}_L'] = ts_mean(L,d)
for d in [5,6,10,20,24]:
    ops[f'std{d}_C'] = ts_std(C,d); ops[f'std{d}_H'] = ts_std(H,d)
for d in [2,3,5,6,9,12,20]:
    ops[f'mx{d}_H'] = ts_max(H,d); ops[f'mn{d}_L'] = ts_min(L,d)
    ops[f'mx{d}_C'] = ts_max(C,d); ops[f'mn{d}_C'] = ts_min(C,d)
print(f"    {len(ops)} operators ready")

# define 15 pilot factors
print("[3] Defining factors...")
FACTORS = {
    'a14_mom5d':    lambda: (C - ops['s5_C']) / ops['s5_C'],
    'a18_close5d':  lambda: C / ops['s5_C'],
    'a20_mom6d':    lambda: (C - ops['s6_C']) / ops['s6_C'] * 100,
    'a31_ma12dev':  lambda: (C - ops['m12_C']) / ops['m12_C'] * 100,
    'a34_invma12':  lambda: ops['m12_C'] / C,
    'a46_4ma_avg':  lambda: (ops['m3_C']+ops['m6_C']+ops['m12_C']+ops['m24_C']) / (4*C),
    'a65_ma6':      lambda: ops['m6_C'] / C,
    'a71_ma24dev':  lambda: (C - ops['m24_C']) / ops['m24_C'] * 100,
    'a47_williams': lambda: (ops['mx6_H'] - C) / (ops['mx6_H'] - ops['mn6_L']) * 100,
    'a57_stoch99':  lambda: (C - ops['mn9_L']) / (ops['mx9_H'] - ops['mn9_L']) * 100,
    'a2_cdlspread': lambda: ((C-L)-(H-C))/(H-L).replace(0,np.nan).diff(1)*-1,
    'a63_rsi6':     lambda: np.maximum(C-ops['s1_C'],0).rolling(6,min_periods=3).mean() / np.abs(C-ops['s1_C']).rolling(6,min_periods=3).mean()*100,
    'a67_rsi24':    lambda: np.maximum(C-ops['s1_C'],0).rolling(24,min_periods=12).mean() / np.abs(C-ops['s1_C']).rolling(24,min_periods=12).mean()*100,
    'a79_rsi12':    lambda: np.maximum(C-ops['s1_C'],0).rolling(12,min_periods=6).mean() / np.abs(C-ops['s1_C']).rolling(12,min_periods=6).mean()*100,
    'a80_volchg5':  lambda: (V - ops['s5_V']) / ops['s5_V'].replace(0,np.nan) * 100,
}

# compute IC
print("[4] Computing IC for each factor...")
def compute_ic(fpanel, fwdpanel):
    common = sorted(set(fpanel.index) & set(fwdpanel.index))
    ics = []
    for dt in common:
        f = fpanel.loc[dt].dropna(); r = fwdpanel.loc[dt].dropna()
        idx = f.index.intersection(r.index)
        if len(idx) < 30: continue
        fv = f[idx].values.astype(float); rv = r[idx].values.astype(float)
        if np.nanstd(fv)<1e-10 or np.nanstd(rv)<1e-10: continue
        ic,_ = spearmanr(fv, rv, nan_policy='omit')
        if not np.isnan(ic): ics.append(ic)
    if not ics: return {'IC_mean':np.nan,'IR':np.nan,'n':0}
    s = pd.Series(ics); return {'IC_mean':s.mean(),'IR':s.mean()/s.std() if s.std()>0 else 0,'n':len(ics)}

results = []
for fname, fn in FACTORS.items():
    sys.stdout.write(f"    {fname:<20} "); sys.stdout.flush()
    try:
        panel = fn()
    except Exception as e:
        print(f"ERR: {e}"); continue
    for h, fwd_panel in [(3,fwd3),(5,fwd5),(10,fwd10)]:
        st = compute_ic(panel, fwd_panel)
        if st['n'] >= 10:
            ic, ir = st['IC_mean'], st['IR']
            results.append({'factor':fname,'holding':h,'IC_mean':round(ic,6),'IR':round(ir,4),'n':st['n'],
                           'pass':'PASS' if abs(ic)>0.03 and abs(ir)>0.3 else 'fail'})
            print(f"h={h} IC={ic:+.4f} IR={ir:+.3f}", end="  ")
    print()

# save
out = pd.DataFrame(results)
out_path = '/root/alpha-research/reports/alpha191_pilot_ic.csv'
out.to_csv(out_path, index=False)
print(f"\n[save] -> {out_path}")
print(f"Pass: {(out['pass']=='PASS').sum()} / {len(out)}")
print("\n=== Top factors (|IC|>0.02) ===")
print(out[out['abs_IC']>0.02].sort_values('abs_IC', ascending=False).to_string(index=False) if 'abs_IC' in out.columns else out[out['IC_mean'].abs()>0.02].sort_values('IC_mean', ascending=False).to_string(index=False))
