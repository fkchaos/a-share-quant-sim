#!/usr/bin/env python3
"""趋势择时：大盘>MA牛市满仓，大盘<MA熊市空仓"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

import numpy as np
import pandas as pd
from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.account import (PortfolioState, buy, sell, check_stop_loss,
                          check_take_profit, apply_holding_decay, portfolio_value)
from core.config import config as core_config, STRATEGY_PROFILES

INITIAL_CAPITAL = core_config.costs.initial_capital

print("加载数据...", flush=True)
codes = sorted([f.replace(".csv","") for f in os.listdir(DAILY_DIR) if f.endswith(".csv")])
close_p, vol_p = {}, {}
for c in codes:
    f = os.path.join(DAILY_DIR, f"{c}.csv")
    if os.path.exists(f):
        df = pd.read_csv(f, index_col='date', parse_dates=True)['2021-01-01':]
        if len(df)>100: close_p[c]=df['close']; vol_p[c]=df['volume']
close = pd.DataFrame(close_p); vol = pd.DataFrame(vol_p); amt = close*vol
dates = close.dropna(how='all').index.sort_values()
print(f"  {close.shape}", flush=True)

# 大盘指数
vc = [c for c in close.columns if close[c].notna().sum()>200]
cv = close[vc]
mr = cv.pct_change().mean(axis=1)
mi = (1+mr).cumprod()*1000

ma20 = mi.rolling(20).mean()
ma60 = mi.rolling(60).mean()
ma120 = mi.rolling(120).mean()

print("计算因子...", flush=True)
factors = calc_factors_panel(close, vol, amt)
W = STRATEGY_PROFILES['v6b_8f_pos_ic'].factor_weights
score = composite_score(factors, {k:v for k,v in W.items() if k in factors})

TOP_N=12; REBAL=20; MAX_IND=0.25

def run_bt(timing, label):
    state = PortfolioState(cash=INITIAL_CAPITAL, initial_capital=INITIAL_CAPITAL)
    nav = []
    for i, dt in enumerate(dates):
        if i<120: nav.append(INITIAL_CAPITAL); continue
        if dt not in close.index:
            nav.append(nav[-1] if nav else INITIAL_CAPITAL); continue
        pd_ = close.loc[dt]
        state = check_stop_loss(state, dt, pd_)
        state = check_take_profit(state, dt, pd_, [(0.10,0.30),(0.20,0.30),(0.30,1.00)])
        state = apply_holding_decay(state, dt, pd_, REBAL)
        
        bull = timing[dt] if dt in timing.index else True
        
        if (i-120)%20==0 and dt in score.index:
            ds = score.loc[dt].dropna()
            ds = ds[ds.index.isin(pd_.dropna().index)]
            if len(ds)>0:
                top=[]; ic={}
                for c in ds.sort_values(ascending=False).index:
                    if ic.get(c[:2],0)<3: top.append(c); ic[c[:2]]=ic.get(c[:2],0)+1
                    if len(top)>=TOP_N: break
                if top:
                    cpv=portfolio_value(state,dt,pd_)
                    if not bull:
                        for c in list(state.holdings):
                            if c in pd_.index and not pd.isna(pd_[c]) and pd_[c]>0:
                                state=sell(state,c,pd_[c],dt,'SELL')
                    else:
                        for c in list(state.holdings):
                            if c not in top and c in pd_.index and not pd.isna(pd_[c]) and pd_[c]>0:
                                state=sell(state,c,pd_[c],dt,'SELL')
                        for c in top:
                            if c not in state.holdings and c in pd_.index:
                                p=pd_[c]
                                if pd.isna(p) or p<=0: continue
                                tv=min(cpv/TOP_N,cpv*0.1)
                                ap=p*(1+core_config.costs.slippage_rate)
                                sh=int(tv/ap/100)*100
                                if sh>0 and state.cash>=sh*ap: state=buy(state,c,p,dt,sh)
        
        # 熊市且无持仓时，记录现金价值
        if not bull and not state.holdings:
            nav.append(state.cash)
        else:
            nav.append(portfolio_value(state,dt,pd_))
    
    nav=pd.Series(nav,index=dates)
    rets=nav.pct_change().dropna()
    tr=nav.iloc[-1]/nav.iloc[0]-1; y=max(len(nav)/252,0.01)
    ar=(1+tr)**(1/y)-1; av=rets.std()*np.sqrt(252)
    sp=ar/av if av>0 else 0
    peak=nav.cummax(); md=((nav-peak)/peak).min()
    cm=ar/abs(md) if md!=0 else 0
    print(f"  {label}: 年化={ar*100:.2f}% Sharpe={sp:.3f} 回撤={md*100:.2f}% Calmar={cm:.3f}", flush=True)
    return nav,{'annual':round(ar*100,2),'sharpe':round(sp,3),'dd':round(md*100,2),'calmar':round(cm,3)}

print("\n回测...", flush=True)
t0=time.time()

print("\n[1] 无择时(始终满仓)...", flush=True)
n1,r1 = run_bt(pd.Series(True,index=dates),"无择时")

print("\n[2] MA60择时...", flush=True)
n2,r2 = run_bt(mi>ma60,"MA60择时")

print("\n[3] MA120择时...", flush=True)
n3,r3 = run_bt(mi>ma120,"MA120择时")

print(f"\n耗时: {time.time()-t0:.1f}s", flush=True)

print(f"\n{'='*60}")
for lbl,rv in [("无择时",r1),("MA60择时",r2),("MA120择时",r3)]:
    print(f"  {lbl:>16}: 年化={rv['annual']:.2f}% | Sharpe={rv['sharpe']:.3f} | 回撤={rv['dd']:.2f}% | Calmar={rv['calmar']:.3f}")

periods=[('2023-01-01','2023-06-30','2023H1'),('2023-07-01','2023-12-31','2023H2'),
         ('2024-01-01','2024-06-30','2024H1'),('2024-07-01','2024-12-31','2024H2'),
         ('2025-01-01','2025-06-30','2025H1'),('2025-07-01','2025-12-31','2025H2')]
print(f"\n分时段:")
for s,e,label in periods:
    mi_p=mi[(mi.index>=s)&(mi.index<=e)]
    mkt=(mi_p.iloc[-1]/mi_p.iloc[0]-1)*100 if len(mi_p)>0 else 0
    def gp(n):
        nn=n[(n.index>=s)&(n.index<=e)]
        return (nn.iloc[-1]/nn.iloc[0]-1)*100 if len(nn)>0 else 0
    print(f"  {label}: 大盘={mkt:+.1f}% | 无择时={gp(n1):+.1f}% | MA60={gp(n2):+.1f}% | MA120={gp(n3):+.1f}%")

print("\n完成")
