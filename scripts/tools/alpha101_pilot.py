#!/usr/bin/env python3
"""Alpha101 IC batch test — pilot 20 simple factors"""
import sys
sys.path.insert(0, '/root/a-share-quant-sim')
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from core.db import load_panel_from_db

print("[1] Loading...")
panels, codes = load_panel_from_db(pool='zz1800', start_date='2020-06-01', need_open=True, need_hl=True)
cp, vp, ap, op, hp, lp = panels
top = vp.mean().sort_values(ascending=False).head(500).index.tolist()
C,V,A,O,H,L = cp[top],vp[top],ap[top],op[top],hp[top],lp[top]
vw = np.where(V>0, A/V, C)
print(f"    {C.shape[0]}d x {C.shape[1]}s")

def fwd(h): return C.shift(-h)/C-1
fwd3, fwd5, fwd10 = fwd(3), fwd(5), fwd(10)

def tm(s,d): return s.rolling(d,min_periods=max(2,d//2)).mean()
def ts(s,d): return s.rolling(d,min_periods=max(2,d//2)).std()
def tmx(s,d): return s.rolling(d,min_periods=max(2,d//2)).max()
def tmn(s,d): return s.rolling(d,min_periods=max(2,d//2)).min()
def sh(s,d): return s.shift(d)
def dlt(s,d): return s - s.shift(d)

# cross-sectional rank (逐行)
def xr(s):
    return s.rank(axis=1, pct=True)

# time-series rank (each column)
def tsrank(s, d):
    out = np.full_like(s.values, np.nan)
    for t in range(d, s.shape[0]):
        window = s.values[t-d:t, :]
        last = s.values[t, :]
        for j in range(s.shape[1]):
            if np.isnan(last[j]): continue
            col = window[:, j]
            col = col[~np.isnan(col)]
            if len(col) < 2: continue
            out[t, j] = (col <= last[j]).mean()
    return pd.DataFrame(out, index=s.index, columns=s.columns)

# rolling corr (时序, 两 panel)
def rollcorr(a, b, d):
    out = np.full_like(a.values, np.nan)
    av, bv = a.values, b.values
    for t in range(d, a.shape[0]):
        aw = av[t-d:t, :]; bw = bv[t-d:t, :]
        for j in range(a.shape[1]):
            mask = np.isfinite(aw[:,j]) & np.isfinite(bw[:,j])
            if mask.sum() < 3: continue
            x = aw[mask, j]; y = bw[mask, j]
            if np.std(x) < 1e-10 or np.std(y) < 1e-10: continue
            out[t, j] = np.corrcoef(x, y)[0, 1]
    return pd.DataFrame(out, index=a.index, columns=a.columns)

ops = {}
for d in [1,5,10]:
    ops[f's{d}_C']=sh(C,d); ops[f's{d}_V']=sh(V,d)
for d in [5,20]:
    ops[f'm{d}_C']=tm(C,d); ops[f'm{d}_V']=tm(V,d); ops[f'm{d}_H']=tm(H,d); ops[f'm{d}_L']=tm(L,d)

def compute_ic(fp, fwd):
    common=sorted(set(fp.index)&set(fwd.index))
    ics=[]
    for dt in common:
        f=fp.loc[dt].dropna(); r=fwd.loc[dt].dropna()
        idx=f.index.intersection(r.index)
        if len(idx)<30:continue
        fv=f[idx].values.astype(float); rv=r[idx].values.astype(float)
        if np.nanstd(fv)<1e-10 or np.nanstd(rv)<1e-10:continue
        ic,_=spearmanr(fv,rv,nan_policy='omit')
        if not np.isnan(ic):ics.append(ic)
    if not ics:return{'IC_mean':np.nan,'IR':np.nan,'n':0}
    s=pd.Series(ics);return{'IC_mean':s.mean(),'IR':s.mean()/s.std() if s.std()>0 else 0,'n':len(ics)}

# 10 pilot alphas (simpler ones)
FACTORS = {
    'a101_close_open':  lambda: (C-O)/(H-L).replace(0,np.nan)+0.001,
    'a12_signdvol_dc': lambda: np.sign(dlt(V,1)) * (-dlt(C,1)),
    'a33_rank_neg':    lambda: xr(-((1-(O/C))**1)),
    'a41_hl_vwap':     lambda: np.sqrt(H*L) - vw,
    'a42_rank_vwap':   lambda: xr(vw-C)/xr(vw+C),
    'a52_minlz_mk':    lambda: ((-tmn(L,5)+sh(tmn(L,5),5))* xr(tm(V,5)/V.replace(0,np.nan))),
}

print(f"[2] {len(FACTORS)} Alpha101 factors")
results = []
for fname, fn in FACTORS.items():
    sys.stdout.write(f"    {fname:<20} "); sys.stdout.flush()
    try:
        panel = fn()
    except Exception as e:
        print(f"ERR:{e}"); continue
    for h, fwd_panel in [(3,fwd3),(5,fwd5),(10,fwd10)]:
        st = compute_ic(panel, fwd_panel)
        if st['n']>=10:
            ic, ir = st['IC_mean'],st['IR']
            results.append({'factor':fname,'holding':h,'IC_mean':round(ic,6),'IR':round(ir,4),'n':st['n'],'pass':'PASS' if abs(ic)>0.03 and abs(ir)>0.3 else 'fail'})
            print(f"h={h} IC={ic:+.4f} IR={ir:+.3f}", end="  ")
    print()

out = pd.DataFrame(results)
out_path = '/root/alpha-research/reports/alpha101_pilot_ic.csv'
out.to_csv(out_path, index=False)
print(f"\n[save] -> {out_path}")
print(f"Pass: {(out['pass']=='PASS').sum()} / {len(out)}")
