#!/usr/bin/env python3
"""趋势择时：只跑 MA60 + MA120，精简输出"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")
import numpy as np, pandas as pd
from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.account import (PortfolioState, buy, sell, check_stop_loss,
                          check_take_profit, apply_holding_decay, portfolio_value)
from core.config import config as core_config, STRATEGY_PROFILES

ICAP = core_config.costs.initial_capital

codes = sorted([f.replace(".csv","") for f in os.listdir(DAILY_DIR) if f.endswith(".csv")])
vp, volp = {}, {}
for c in codes:
    f = os.path.join(DAILY_DIR, f"{c}.csv")
    if os.path.exists(f):
        d = pd.read_csv(f, index_col='date', parse_dates=True)['2021-01-01':]
        if len(d)>100: vp[c]=d['close']; volp[c]=d['volume']
close = pd.DataFrame(vp); vol = pd.DataFrame(volp); amt = close*vol
dates = close.dropna(how='all').index.sort_values()

# 大盘
vc = [c for c in close.columns if close[c].notna().sum()>200]
mi = (1+close[vc].pct_change().mean(axis=1)).cumprod()*1000
ma60 = mi.rolling(60).mean()
ma120 = mi.rolling(120).mean()

# 因子
fac = calc_factors_panel(close, vol, amt)
W = STRATEGY_PROFILES['v6b_8f_pos_ic'].factor_weights
score = composite_score(fac, {k:v for k,v in W.items() if k in fac})

def run(timing, label):
    st = PortfolioState(cash=ICAP, initial_capital=ICAP)
    nav = []
    for i, dt in enumerate(dates):
        if i<120: nav.append(ICAP); continue
        if dt not in close.index:
            nav.append(nav[-1] if nav else ICAP); continue
        pd_ = close.loc[dt]
        st = check_stop_loss(st, dt, pd_)
        st = check_take_profit(st, dt, pd_, [(0.10,0.30),(0.20,0.30),(0.30,1.00)])
        st = apply_holding_decay(st, dt, pd_, 20)
        bull = timing[dt] if dt in timing.index else True
        if (i-120)%20==0 and dt in score.index:
            ds = score.loc[dt].dropna()
            ds = ds[ds.index.isin(pd_.dropna().index)]
            if len(ds)>0:
                top=[]; ic={}
                for c in ds.sort_values(ascending=False).index:
                    if ic.get(c[:2],0)<3: top.append(c); ic[c[:2]]=ic.get(c[:2],0)+1
                    if len(top)>=12: break
                if top:
                    cpv=portfolio_value(st,dt,pd_)
                    if not bull:
                        for c in list(st.holdings):
                            if c in pd_.index and not pd.isna(pd_[c]) and pd_[c]>0:
                                st=sell(st,c,pd_[c],dt,'REBAL')
                    else:
                        for c in list(st.holdings):
                            if c not in top and c in pd_.index and not pd.isna(pd_[c]) and pd_[c]>0:
                                st=sell(st,c,pd_[c],dt,'REBAL')
                        for c in top:
                            if c not in st.holdings and c in pd_.index:
                                p=pd_[c]
                                if pd.isna(p) or p<=0: continue
                                ap=p*(1+core_config.costs.slippage_rate)
                                sh=int(min(cpv/12,cpv*0.1)/ap/100)*100
                                if sh>0 and st.cash>=sh*ap: st=buy(st,c,p,dt,sh)
        if not bull and not st.holdings:
            nav.append(st.cash)
        else:
            nav.append(portfolio_value(st,dt,pd_))
    nav=pd.Series(nav,index=dates)
    rets=nav.pct_change().dropna()
    tr=nav.iloc[-1]/nav.iloc[0]-1; y=max(len(nav)/252,0.01)
    ar=(1+tr)**(1/y)-1; av=rets.std()*np.sqrt(252)
    sp=ar/av if av>0 else 0
    peak=nav.cummax(); md=((nav-peak)/peak).min()
    cm=ar/abs(md) if md!=0 else 0
    print(f"  {label}: 年化={ar*100:.2f}% Sharpe={sp:.3f} 回撤={md*100:.2f}% Calmar={cm:.3f}")
    return nav,{'annual':round(ar*100,2),'sharpe':round(sp,3),'dd':round(md*100,2),'calmar':round(cm,3),}

print("回测...", flush=True)
t0=time.time()
n1,r1=run(pd.Series(True,index=dates),"无择时")
n2,r2=run(mi>ma60,"MA60择时")
n3,r3=run(mi>ma120,"MA120择时")
print(f"耗时: {time.time()-t0:.1f}s")

print(f"\n{'='*65}")
for lbl,rv in [("无择时",r1),("MA60择时",r2),("MA120择时",r3)]:
    print(f"  {lbl:>14}: 年化={rv['annual']:>7.2f}% | Sharpe={rv['sharpe']:.3f} | 回撤={rv['dd']:.2f}% | Calmar={rv['calmar']:.3f}")

P=[('2023-01-01','2023-06-30','2023H1'),('2023-07-01','2023-12-31','2023H2'),
   ('2024-01-01','2024-06-30','2024H1'),('2024-07-01','2024-12-31','2024H2'),
   ('2025-01-01','2025-06-30','2025H1'),('2025-07-01','2025-12-31','2025H2')]
print(f"\n分时段对比:")
print(f"  {'时段':>6} | {'大盘%':>8} | {'无择时%':>9} | {'MA60%':>8} | {'MA120%':>9} | {'牛市%':>6}")
for s,e,label in P:
    m=mi[(mi.index>=s)&(mi.index<=e)]
    mkt=(m.iloc[-1]/m.iloc[0]-1)*100 if len(m)>0 else 0
    def gp(n):
        nn=n[(n.index>=s)&(n.index<=e)]
        return (nn.iloc[-1]/nn.iloc[0]-1)*100 if len(nn)>0 else 0
    bp=(mi>ma60)[(mi.index>=s)&(mi.index<=e)].mean()*100 if len(m)>0 else 0
    print(f"  {label:>6} | {mkt:>+8.1f}% | {gp(n1):>+9.1f}% | {gp(n2):>+8.1f}% | {gp(n3):>+9.1f}% | {bp:>5.0f}%")
print("\n完成")
