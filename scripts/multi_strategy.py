#!/usr/bin/env python3
"""多策略并行（简化快速版）"""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")
import numpy as np, pandas as pd
from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.config import STRATEGY_PROFILES

codes = sorted([f.replace(".csv","") for f in os.listdir(DAILY_DIR) if f.endswith(".csv")])
vp, volp = {}, {}
for c in codes:
    f = os.path.join(DAILY_DIR, f"{c}.csv")
    if os.path.exists(f):
        d = pd.read_csv(f, index_col='date', parse_dates=True)['2021-01-01':]
        if len(d)>100: vp[c]=d['close']; volp[c]=d['volume']
close = pd.DataFrame(vp); vol = pd.DataFrame(volp); amt = close*vol
dates = close.dropna(how='all').index.sort_values()
print(f"面板: {close.shape}")

factors = calc_factors_panel(close, vol, amt)
W = STRATEGY_PROFILES['v6b_8f_pos_ic'].factor_weights
score_v6b = composite_score(factors, {k:v for k,v in W.items() if k in factors})
ret = close.pct_change()
score_mom = ret.rolling(20).sum()
score_rev = -ret.rolling(10).sum()
score_lowvol = -ret.rolling(20).std()

def calc_nav(score_df, label):
    holdings = {}
    cash = 200000.0
    vals = []
    for i, dt in enumerate(dates):
        if i < 120:
            vals.append(200000.0); continue
        if dt not in close.index:
            vals.append(vals[-1]); continue
        
        # 当前持仓市值
        sv = sum(holdings.get(c,0) * close.loc[dt,c] for c in holdings if dt in close.index and c in close.columns and not np.isnan(close.loc[dt,c]))
        total = cash + sv
        
        if (i-120)%20==0 and dt in score_df.index:
            ds = score_df.loc[dt].dropna()
            ds = ds[ds.index.isin(close.columns)]
            if len(ds)>0:
                top = ds.nlargest(12).index.tolist()
                per = total / 12
                new_h = {}
                for c in top:
                    p = close.loc[dt,c] if dt in close.index and c in close.columns else np.nan
                    if not np.isnan(p) and p>0:
                        sh = int(per/p/100)*100
                        if sh>0: new_h[c] = sh
                holdings = new_h
                cash = total - sum(holdings[c]*close.loc[dt,c] for c in holdings if dt in close.index and c in close.columns and not np.isnan(close.loc[dt,c]))
        
        sv = sum(holdings.get(c,0) * close.loc[dt,c] for c in holdings if dt in close.index and c in close.columns and not np.isnan(close.loc[dt,c]))
        vals.append(cash + sv)
    
    nav = pd.Series(vals, index=dates).ffill()
    rets = nav.pct_change().dropna()
    tr=nav.iloc[-1]/nav.iloc[0]-1; y=max(len(nav)/252,0.01)
    ar=(1+tr)**(1/y)-1; av=rets.std()*np.sqrt(252)
    sp=ar/av if av>0 else 0
    peak=nav.cummax(); md=((nav-peak)/peak).min()
    cm=ar/abs(md) if md!=0 else 0
    print(f"  {label}: 年化={ar*100:.2f}% Sharpe={sp:.3f} 回撤={md*100:.2f}% Calmar={cm:.3f}", flush=True)
    return nav

print("简化回测...", flush=True)
n1=calc_nav(score_v6b, "截面因子(v6b)")
n2=calc_nav(score_mom, "动量(20日)")
n3=calc_nav(score_rev, "反转(10日)")
n4=calc_nav(score_lowvol, "低波动")
multi=(n1+n2+n3+n4)/4

mr=multi.pct_change().dropna()
tr=multi.iloc[-1]/multi.iloc[0]-1; y=max(len(multi)/252,0.01)
ar=(1+tr)**(1/y)-1; av=mr.std()*np.sqrt(252)
sp=ar/av if av>0 else 0
peak=multi.cummax(); md=((multi-peak)/peak).min()
cm=ar/abs(md) if md!=0 else 0
print(f"  等权组合: 年化={ar*100:.2f}% Sharpe={sp:.3f} 回撤={md*100:.2f}% Calmar={cm:.3f}", flush=True)

SEP="="*65
print(f"\n{SEP}")
header = f"{'策略':>20} | {'年化%':>8} | {'Sharpe':>8} | {'回撤%':>8} | {'Calmar':>8}"
print(header)
print("-"*65)
for label, nav in [("截面因子(v6b)",n1),("动量(20日)",n2),("反转(10日)",n3),("低波动",n4),("等权组合(4x25%)",multi)]:
    r=nav.pct_change().dropna()
    tr=nav.iloc[-1]/nav.iloc[0]-1; y=max(len(nav)/252,0.01)
    ar=(1+tr)**(1/y)-1; av=r.std()*np.sqrt(252)
    sp=ar/av if av>0 else 0
    peak=nav.cummax(); md=((nav-peak)/peak).min()
    cm=ar/abs(md) if md!=0 else 0
    print(f"  {label:>18} | {ar*100:>8.2f} | {sp:>8.3f} | {md*100:>8.2f} | {cm:>8.3f}")

P=[('2023-01-01','2023-06-30','2023H1'),('2023-07-01','2023-12-31','2023H2'),
   ('2024-01-01','2024-06-30','2024H1'),('2024-07-01','2024-12-31','2024H2'),
   ('2025-01-01','2025-06-30','2025H1'),('2025-07-01','2025-12-31','2025H2')]
print(f"\n分时段:")
print(f"  {'时段':>6} | {'截面':>7} | {'动量':>7} | {'反转':>7} | {'低波':>7} | {'组合':>7}")
for s,e,label in P:
    def gp(n): 
        nn=n[(n.index>=s)&(n.index<=e)]
        return (nn.iloc[-1]/nn.iloc[0]-1)*100 if len(nn)>0 else 0
    print(f"  {label:>6} | {gp(n1):>+6.1f}% | {gp(n2):>+6.1f}% | {gp(n3):>+6.1f}% | {gp(n4):>+6.1f}% | {gp(multi):>+6.1f}%")

print(f"\n策略相关性:")
for k1,v1,k2,v2 in [("截面",n1,"动量",n2),("截面",n1,"反转",n3),("截面",n1,"低波",n4),("动量",n2,"反转",n3),("动量",n2,"低波",n4),("反转",n3,"低波",n4)]:
    c=v1.pct_change().dropna().index.intersection(v2.pct_change().dropna().index)
    if len(c)>10:
        corr=v1.pct_change()[c].corr(v2.pct_change()[c])
        print(f"  {k1} vs {k2}: {corr:+.3f}")
print("\n完成")
