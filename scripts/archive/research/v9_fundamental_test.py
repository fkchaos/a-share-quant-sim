#!/usr/bin/env python3
"""
v9 基本面因子测试：log_mv 加入 v8
"""
import sys, os, re, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

import numpy as np
import pandas as pd
import urllib.request
from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.account import (PortfolioState, buy, sell, check_stop_loss,
                          check_take_profit, apply_holding_decay, portfolio_value)
from core.config import config as core_config, STRATEGY_PROFILES

INITIAL_CAPITAL = core_config.costs.initial_capital

# 加载数据
codes = sorted([f.replace(".csv","") for f in os.listdir(DAILY_DIR) if f.endswith(".csv")])
print("加载数据...")
close_p, vol_p = {}, {}
for c in codes:
    f = os.path.join(DAILY_DIR, f"{c}.csv")
    if os.path.exists(f):
        df = pd.read_csv(f, index_col='date', parse_dates=True)['2021-01-01':]
        if len(df)>100: close_p[c]=df['close']; vol_p[c]=df['volume']
close = pd.DataFrame(close_p); vol = pd.DataFrame(vol_p); amt = close*vol
dates = close.dropna(how='all').index.sort_values()
print(f"  {close.shape}")

# 获取市值
print("获取市值...")
fund = {}
for i in range(0,len(codes),50):
    batch = codes[i:i+50]
    syms = ['sh'+c if c.startswith('6') else 'sz'+c for c in batch]
    try:
        req = urllib.request.Request(f'http://qt.gtimg.cn/q={",".join(syms)}', headers={'User-Agent':'Mozilla/5.0'})
        data = urllib.request.urlopen(req,timeout=10).read().decode('gbk')
        for line in data.strip().split('\n'):
            m = re.search(r'"(.+?)"', line)
            if not m: continue
            f = m.group(1).split('~')
            if len(f)<50: continue
            try: fund[f[2]] = {'mv': float(f[44])} if f[44] and float(f[44])>0 else None
            except: pass
    except: pass

print("计算因子...")
factors = calc_factors_panel(close, vol, amt)
log_mv = pd.DataFrame({c: np.log(fund[c]['mv']) if c in fund and fund[c] else np.nan for c in close.columns}, index=dates)
factors['log_mv'] = log_mv

def get_ind(c): return c[:2]

def run_bt(score, label):
    state = PortfolioState(cash=INITIAL_CAPITAL, initial_capital=INITIAL_CAPITAL)
    nav_list = []
    TOP_N=12; REBAL=20; MAX_IND=0.25; MAX_POS=0.10
    USE_TP=True; TP_TIERS=[(0.10,0.30),(0.20,0.30),(0.30,1.00)]; USE_DEC=True
    
    for i, date in enumerate(dates):
        if i<120: nav_list.append(INITIAL_CAPITAL); continue
        if date not in close.index:
            nav_list.append(nav_list[-1] if nav_list else INITIAL_CAPITAL); continue
        pd_ = close.loc[date]
        state = check_stop_loss(state, date, pd_)
        if USE_TP: state = check_take_profit(state, date, pd_, TP_TIERS)
        if USE_DEC: state = apply_holding_decay(state, date, pd_, REBAL)
        if (i-120)%REBAL==0 and date in score.index:
            ds = score.loc[date].dropna()
            ds = ds[ds.index.isin(pd_.dropna().index)]
            if len(ds)>0:
                top=[]; ic={}; mpi=max(1,int(MAX_IND*TOP_N))
                for c in ds.sort_values(ascending=False).index:
                    ind=get_ind(c)
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
    y = max(len(nav)/252,0.01)
    ar=(1+tr)**(1/y)-1; av=rets.std()*np.sqrt(252)
    sp=ar/av if av>0 else 0
    peak=nav.cummax(); md=((nav-peak)/peak).min()
    cm=ar/abs(md) if md!=0 else 0
    print(f"  {label}: 总收益={tr*100:.2f}% | 年化={ar*100:.2f}% | Sharpe={sp:.3f} | 回撤={md*100:.2f}% | Calmar={cm:.3f}")
    return {'annual':round(ar*100,2),'sharpe':round(sp,3),'dd':round(md*100,2),'calmar':round(cm,3)}

v8w = STRATEGY_PROFILES['v8_all_icir'].factor_weights

print("\n策略对比（全集 2021~2026-05）:")
print("="*60)

# v8 baseline
avail = {k:v for k,v in v8w.items() if k in factors}
s = composite_score(factors, avail)
r = run_bt(s, "v8_18f")

# v9a: v8 + log_mv (10%)
w9 = dict(v8w); w9['log_mv'] = 0.10
avail9 = {k:v for k,v in w9.items() if k in factors}
tw = sum(abs(v) for v in avail9.values())
avail9 = {k:round(v/tw,4) for k,v in avail9.items()}
s9 = composite_score(factors, avail9)
r9 = run_bt(s9, "v9a_19f (v8+log_mv@10%)")

# v9b: v8 + log_mv (20%)
w9b = dict(v8w); w9b['log_mv'] = 0.20
avail9b = {k:v for k,v in w9b.items() if k in factors}
tw = sum(abs(v) for v in avail9b.values())
avail9b = {k:round(v/tw,4) for k,v in avail9b.items()}
s9b = composite_score(factors, avail9b)
r9b = run_bt(s9b, "v9b_19f (v8+log_mv@20%)")

# v9c: 只用 top5 量价 + log_mv
top5 = ['illiquidity','boll_width_20','amplitude','turnover_skew','vol_20']
w9c = {k:v8w[k] for k in top5 if k in v8w}; w9c['log_mv'] = 0.25
avail9c = {k:v for k,v in w9c.items() if k in factors}
tw = sum(abs(v) for v in avail9c.values())
avail9c = {k:round(v/tw,4) for k,v in avail9c.items()}
s9c = composite_score(factors, avail9c)
r9c = run_bt(s9c, "v9c_6f (top5+log_mv)")

print(f"\n{'策略':>25} | {'年化%':>8} | {'Sharpe':>8} | {'回撤%':>8} | {'Calmar':>8}")
print("-" * 70)
for lbl, rv in [("v8_18f",r),("v9a(+log_mv@10%)",r9),("v9b(+log_mv@20%)",r9b),("v9c(top5+log_mv)",r9c)]:
    print(f"  {lbl:>23} | {rv['annual']:>8.2f} | {rv['sharpe']:>8.3f} | {rv['dd']:>8.2f} | {rv['calmar']:>8.3f}")
