#!/usr/bin/env python3
"""快速测试 v8 全集回测"""
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

print("加载数据...")
codes = sorted([f.replace(".csv","") for f in os.listdir(DAILY_DIR) if f.endswith(".csv")])
close_p, vol_p = {}, {}
for c in codes:
    f = os.path.join(DAILY_DIR, f"{c}.csv")
    if os.path.exists(f):
        df = pd.read_csv(f, index_col='date', parse_dates=True)['2021-01-01':]
        if len(df)>100: close_p[c]=df['close']; vol_p[c]=df['volume']
close = pd.DataFrame(close_p); vol = pd.DataFrame(vol_p); amt = close*vol
dates = close.dropna(how='all').index.sort_values()

print("计算因子...")
factors = calc_factors_panel(close, vol, amt)

# v8 权重
W_V8 = STRATEGY_PROFILES['v8_all_icir'].factor_weights
avail = {k:v for k,v in W_V8.items() if k in factors}
score = composite_score(factors, avail)

print("回测 v8...")
state = PortfolioState(cash=INITIAL_CAPITAL, initial_capital=INITIAL_CAPITAL)
nav_list = []
TOP_N=12; REBAL=20; MAX_IND=0.25; MAX_POS=0.10

for i, date in enumerate(dates):
    if i<120: nav_list.append(INITIAL_CAPITAL); continue
    if date not in close.index:
        nav_list.append(nav_list[-1] if nav_list else INITIAL_CAPITAL); continue
    pd_ = close.loc[date]
    state = check_stop_loss(state, date, pd_)
    state = check_take_profit(state, date, pd_, [(0.10,0.30),(0.20,0.30),(0.30,1.00)])
    state = apply_holding_decay(state, date, pd_, REBAL)
    if (i-120)%REBAL==0 and date in score.index:
        ds = score.loc[date].dropna()
        ds = ds[ds.index.isin(pd_.dropna().index)]
        if len(ds)>0:
            top=[]; ic={}; mpi=max(1,int(MAX_IND*TOP_N))
            for c in ds.sort_values(ascending=False).index:
                ind=c[:2]
                if ic.get(ind,0)<mpi: top.append(c); ic[ind]=ic.get(ind,0)+1
                if len(top)>=TOP_N: break
            if top:
                cpv=portfolio_value(state,date,pd_)
                for c in list(state.holdings.keys()):
                    if c not in top and c in pd_.index and not pd.isna(pd_[c]) and pd_[c]>0:
                        state=sell(state,c,pd_[c],date,0)
                for c in top:
                    if c not in state.holdings and c in pd_.index:
                        p=pd_[c]
                        if pd.isna(p) or p<=0: continue
                        tv=min(cpv/len(top),cpv*MAX_POS)
                        ap=p*(1+core_config.costs.slippage_rate)
                        sh=int(tv/ap/100)*100
                        if sh>0 and state.cash>=sh*ap: state=buy(state,c,p,date,shares=sh)
    nav_list.append(portfolio_value(state,date,pd_))

nav = pd.Series(nav_list, index=dates)
rets = nav.pct_change().dropna()
tr = nav.iloc[-1]/nav.iloc[0]-1
y=max(len(nav)/252,0.01); ar=(1+tr)**(1/y)-1
av=rets.std()*np.sqrt(252); sp=ar/av if av>0 else 0
peak=nav.cummax(); md=((nav-peak)/peak).min()
cm=ar/abs(md) if md!=0 else 0
print(f"v8 全集: 总收益={tr*100:.2f}% | 年化={ar*100:.2f}% | Sharpe={sp:.3f} | 回撤={md*100:.2f}% | Calmar={cm:.3f}")
print(f"交易日: {len(nav)}, 最终净值: {nav.iloc[-1]:,.0f}")
