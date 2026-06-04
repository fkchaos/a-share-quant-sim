#!/usr/bin/env python3
"""v6b vs v8 Walk-Forward 对比（全量加载，切片回测）"""
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

print("加载全量数据..."); t0=time.time()
files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
all_data = {}
for f in files:
    code = f.replace(".csv","")
    df = pd.read_csv(os.path.join(DAILY_DIR,f), index_col='date', parse_dates=True)
    if len(df)>0: all_data[code]=df
valid = {}
for code,df in all_data.items():
    if df.index.min()<=pd.Timestamp('2021-01-01')+pd.Timedelta(days=60) and \
       df.index.max()>=pd.Timestamp('2026-05-31')-pd.Timedelta(days=60):
        valid[code]=df
close_all = pd.DataFrame({c:d['close'] for c,d in valid.items()})
vol_all   = pd.DataFrame({c:d['volume'] for c,d in valid.items()})
amt_all   = pd.DataFrame({c:d.get('amount',d['close']*d['volume']) for c,d in valid.items()})
dates_all = close_all.dropna(how='all').index.sort_values()
dates_all = dates_all[(dates_all>='2021-01-01')&(dates_all<='2026-05-31')]
close_all=close_all.loc[dates_all]; vol_all=vol_all.loc[dates_all]; amt_all=amt_all.loc[dates_all]
print(f"  {len(valid)} 股票, {len(dates_all)} 交易日, {time.time()-t0:.1f}s")

def get_industry(code): return code[:2]

def run_bt(c, v, a, weights, profile):
    f = calc_factors_panel(c, v, a)
    av = {k:w for k,w in weights.items() if k in f}
    score = composite_score(f, av)
    
    state = PortfolioState(cash=INITIAL_CAPITAL, initial_capital=INITIAL_CAPITAL)
    dates = c.index; nav_list=[]
    TOP_N=profile.top_n; REBAL=profile.rebalance_freq; MAX_IND=profile.max_industry_weight
    USE_TP=profile.use_take_profit; TP_TIERS=profile.tp_tiers; USE_DEC=profile.use_holding_decay
    MAX_POS=profile.max_position
    
    for i, date in enumerate(dates):
        if i<120: nav_list.append(INITIAL_CAPITAL); continue
        if date not in c.index:
            nav_list.append(nav_list[-1] if nav_list else INITIAL_CAPITAL); continue
        pd_ = c.loc[date]
        state = check_stop_loss(state, date, pd_)
        if USE_TP and TP_TIERS: state = check_take_profit(state, date, pd_, TP_TIERS)
        if USE_DEC: state = apply_holding_decay(state, date, pd_, REBAL)
        if (i-120)%REBAL==0 and date in score.index:
            ds = score.loc[date].dropna()
            ds = ds[ds.index.isin(pd_.dropna().index)]
            if len(ds)>0:
                if MAX_IND and MAX_IND>0:
                    top=[]; ic={}; mpi=max(1,int(MAX_IND*TOP_N))
                    for code in ds.sort_values(ascending=False).index:
                        ind=get_industry(code)
                        if ic.get(ind,0)<mpi: top.append(code); ic[ind]=ic.get(ind,0)+1
                        if len(top)>=TOP_N: break
                else: top=ds.nlargest(TOP_N).index.tolist()
                if top:
                    cpv=portfolio_value(state, date, pd_)
                    for c2 in list(state.holdings.keys()):
                        if c2 not in top and c2 in pd_.index and not pd.isna(pd_[c2]) and pd_[c2]>0:
                            state=sell(state,c2,pd_[c2],date,0)
                    for c2 in top:
                        if c2 not in state.holdings and c2 in pd_.index:
                            p=pd_[c2]
                            if pd.isna(p) or p<=0: continue
                            tv=min(cpv/len(top), cpv*MAX_POS)
                            ap=p*(1+core_config.costs.slippage_rate)
                            sh=int(tv/ap/100)*100
                            if sh>0 and state.cash>=sh*ap: state=buy(state,c2,p,date,shares=sh)
        dv=portfolio_value(state, date, pd_)
        nav_list.append(dv)
    return pd.Series(nav_list, index=dates)

def calc_metrics(nav):
    rets=nav.pct_change().dropna()
    tr=nav.iloc[-1]/nav.iloc[0]-1
    days=(nav.index[-1]-nav.index[0]).days
    y=max(days/365,0.01)
    ar=(1+tr)**(1/y)-1
    av=rets.std()*np.sqrt(252)
    sp=ar/av if av>0 else 0
    peak=nav.cummax()
    md=((nav-peak)/peak).min()
    cm=ar/abs(md) if md!=0 else 0
    return round(ar*100,2), round(sp,3), round(md*100,2), round(cm,3)

folds=[
    ('2023-01-01','2023-06-30','F1'),
    ('2023-07-01','2023-12-31','F2'),
    ('2024-01-01','2024-06-30','F3'),
    ('2024-07-01','2024-12-31','F4'),
    ('2025-01-01','2025-06-30','F5'),
    ('2025-07-01','2025-12-31','F6'),
]

for sk in ['v6b_8f_pos_ic','v8_all_icir']:
    prof = STRATEGY_PROFILES[sk]
    w = prof.factor_weights
    print(f"\n{'='*60}\n策略: {sk} | 因子({len(w)}): {list(w.keys())}")
    res=[]
    for s,e,label in folds:
        t0=time.time()
        ws=pd.Timestamp(s)-pd.Timedelta(days=180)
        c=close_all[(close_all.index>=ws)&(close_all.index<=e)]
        v=vol_all[(vol_all.index>=ws)&(vol_all.index<=e)]
        a=amt_all[(amt_all.index>=ws)&(amt_all.index<=e)]
        nav=run_bt(c,v,a,w,prof)
        tn=nav[(nav.index>=pd.Timestamp(s))&(nav.index<=pd.Timestamp(e))]
        if len(tn)<10: print(f"  {label}: 数据不足"); continue
        ar,sp,md,cm=calc_metrics(tn)
        print(f"  {label} ({s}~{e}): 年化={ar}% | Sharpe={sp} | 回撤={md}% | Calmar={cm} | {time.time()-t0:.1f}s")
        res.append({'ar':ar,'sp':sp})
    if res:
        print(f"  → 平均: 年化={np.mean([r['ar'] for r in res]):.2f}% | Sharpe={np.mean([r['sp'] for r in res]):.3f} | 正收益={sum(1 for r in res if r['ar']>0)}/{len(res)}")

print("\n完成")
