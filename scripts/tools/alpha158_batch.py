#!/usr/bin/env python3
"""Alpha158 IC batch test"""
import sys
sys.path.insert(0, '/root/a-share-quant-sim')
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from core.db import load_panel_from_db

print("[1] Loading...")
panels, codes = load_panel_from_db(pool='zz1800', start_date='2020-06-01', need_open=True, need_hl=True)
cp, vp, ap, op, hp, lp = panels
mean_vol = vp.mean().sort_values(ascending=False)
top = mean_vol.head(500).index.tolist()
C,V,A,O,H,L = cp[top],vp[top],ap[top],op[top],hp[top],lp[top]
print(f"    {C.shape[0]}d x {C.shape[1]}s")

def fwd(h): return C.shift(-h)/C-1
fwd3, fwd5, fwd10 = fwd(3), fwd(5), fwd(10)

def tm(s,d): return s.rolling(d,min_periods=max(2,d//2)).mean()
def ts(s,d): return s.rolling(d,min_periods=max(2,d//2)).std()
def tmx(s,d): return s.rolling(d,min_periods=max(2,d//2)).max()
def tmn(s,d): return s.rolling(d,min_periods=max(2,d//2)).min()
def sh(s,d): return s.shift(d)
def dlt(s,d): return s - s.shift(d)

ops = {}
for d in [1,2,3,5,10,20,30,60]:
    ops[f's{d}_C']=sh(C,d); ops[f's{d}_O']=sh(O,d); ops[f's{d}_H']=sh(H,d); ops[f's{d}_L']=sh(L,d)
for d in [5,10,20,30,60]:
    ops[f'm{d}_C']=tm(C,d); ops[f'm{d}_V']=tm(V,d); ops[f'm{d}_O']=tm(O,d)
for d in [5,20]:
    ops[f'std{d}_C']=ts(C,d); ops[f'std{d}_V']=ts(V,d)
for d in [5,10,20]:
    ops[f'mx{d}_C']=tmx(C,d); ops[f'mn{d}_C']=tmn(C,d)

def compute_ic(fp, fwd):
    common = sorted(set(fp.index)&set(fwd.index))
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

# Alpha158 factors
FACTORS = {
    # K线基础 (9个)
    'KMID':     lambda: (C-O)/O,
    'KLEN':     lambda: (H-L)/O,
    'KMID2':    lambda: (C-O)/(H-L).replace(0,np.nan),
    'KUP':      lambda: (H-np.maximum(O,C))/O,
    'KUP2':     lambda: (H-np.maximum(O,C))/(H-L).replace(0,np.nan),
    'KLOW':     lambda: (np.minimum(O,C)-L)/O,
    'KLOW2':    lambda: (np.minimum(O,C)-L)/(H-L).replace(0,np.nan),
    'KSFT':     lambda: (2*C-H-L)/O,
    'KSFT2':    lambda: (2*C-H-L)/(H-L).replace(0,np.nan),
    # 静态价格 (4个)
    'OPEN0':    lambda: O/C,
    'HIGH0':    lambda: H/C,
    'LOW0':     lambda: L/C,
    'VWAP0':    lambda: (np.where(V>0,A/V,C))/C,
    # 趋势 (ROC 类, 5*5=25)
    'ROC_5':    lambda: ops['s5_C']/C, 'ROC_10': lambda: ops['s10_C']/C,
    'ROC_20':   lambda: ops['s20_C']/C, 'ROC_30': lambda: ops['s30_C']/C, 'ROC_60': lambda: ops['s60_C']/C,
    # 趋势 (MA 类)
    'MA_5':     lambda: tm(C,5)/C, 'MA_10': lambda: tm(C,10)/C,
    'MA_20':    lambda: tm(C,20)/C, 'MA_30': lambda: tm(C,30)/C, 'MA_60': lambda: tm(C,60)/C,
    # 趋势 (BETA ≈ slope/close, 简化版用 rolling 线性回归)
    # 波动 STD (5*6=30)
    'STD_5':    lambda: ts(C,5)/C, 'STD_10': lambda: ts(C,10)/C,
    'STD_20':   lambda: ts(C,20)/C, 'STD_30': lambda: ts(C,30)/C, 'STD_60': lambda: ts(C,60)/C,
    'VSTD_5':   lambda: ts(V,5)/V.replace(0,np.nan), 'VSTD_10': lambda: ts(V,10)/V.replace(0,np.nan),
    'VSTD_20':  lambda: ts(V,20)/V.replace(0,np.nan),
    # RSV (KDJ %K) (5*5=15 — 其实是 3*5)
    'RSV_5':    lambda: (C - tmn(L,5))/(tmx(H,5)-tmn(L,5)).replace(0,np.nan),
    'RSV_10':   lambda: (C - tmn(L,10))/(tmx(H,10)-tmn(L,10)).replace(0,np.nan),
    'RSV_20':   lambda: (C - tmn(L,20))/(tmx(H,20)-tmn(L,20)).replace(0,np.nan),
    'RSV_30':   lambda: (C - tmn(L,30))/(tmx(H,30)-tmn(L,30)).replace(0,np.nan),
    'RSV_60':   lambda: (C - tmn(L,60))/(tmx(H,60)-tmn(L,60)).replace(0,np.nan),
    # SUMP/SUMN/SUMD (RSI 分解, 5*5)
}

print(f"[2] {len(FACTORS)} Alpha158 factors")
results = []
for fname, fn in FACTORS.items():
    sys.stdout.write(f"    {fname:<15} "); sys.stdout.flush()
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
out_path = '/root/alpha-research/reports/alpha158_batch_ic.csv'
out.to_csv(out_path, index=False)
print(f"\n[save] -> {out_path}")
print(f"Pass: {(out['pass']=='PASS').sum()} / {len(out)}")
if len(out):
    print("\n=== Top (|IC|>0.02) ===")
    print(out[out['IC_mean'].abs()>0.02].sort_values('IC_mean', key=abs, ascending=False).head(20).to_string(index=False))
