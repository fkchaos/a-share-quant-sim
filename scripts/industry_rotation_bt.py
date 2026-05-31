#!/usr/bin/env python3
"""行业轮动回测（精简版）：纯 v6b vs 行业轮动 top3 vs 行业轮动 top5"""
import sys, os, time, json
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, "/root/a-share-quant-sim")

from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.account import PortfolioState, buy, sell, check_stop_loss, check_take_profit, apply_holding_decay, portfolio_value
from core.config import config as core_config, STRATEGY_PROFILES

DAILY_DIR = "/root/data/daily"
DATA_DIR = "/root/data"

with open('/tmp/industry_map.json') as f:
    industry_map = json.load(f)

codes = sorted([f.replace(".csv","") for f in os.listdir(DAILY_DIR) if f.endswith(".csv")])
ICAP = core_config.costs.initial_capital
W = STRATEGY_PROFILES['v6b_8f_pos_ic'].factor_weights

# 加载价格
close_p, vol_p, amt_p = {}, {}, {}
for c in codes:
    f = os.path.join(DAILY_DIR, f"{c}.csv")
    if os.path.exists(f):
        df = pd.read_csv(f, index_col='date', parse_dates=True)['2021-01-01':]
        if len(df) > 100:
            close_p[c] = df['close']
            vol_p[c] = df['volume']
            amt_p[c] = df.get('amount', df['close'] * df['volume'])

close = pd.DataFrame(close_p)
vol_df = pd.DataFrame(vol_p)
amt_df = pd.DataFrame(amt_p)
dates = close.dropna(how='all').index.sort_values()

# 行业指数动量
ind_members = {}
for c, ind in industry_map.items():
    ind_members.setdefault(ind, []).append(c)

ind_ret = {}
for ind, mem in ind_members.items():
    valid = [c for c in mem if c in close.columns]
    if len(valid) < 3: continue
    ind_ret[ind] = close[valid].pct_change().mean(axis=1)

ind_ret_df = pd.DataFrame(ind_ret).reindex(dates)
mom_20 = ind_ret_df.rolling(20).sum()

# v6b 评分
print("计算 v6b 评分...", flush=True)
factors = calc_factors_panel(close, vol_df, amt_df)
score_v6b = composite_score(factors, {k:v for k,v in W.items() if k in factors})
print(f"评分面板: {score_v6b.shape}", flush=True)

def run_bt(use_ind_timing, top_n_ind, label):
    """use_ind_timing: False=全行业, True=行业轮动; top_n_ind: 选前N个行业"""
    state = PortfolioState(cash=ICAP, initial_capital=ICAP)
    nav = []
    
    for i, dt in enumerate(dates):
        if i < 120: nav.append(ICAP); continue
        if dt not in close.index:
            nav.append(nav[-1] if nav else ICAP); continue
            
        pd_ = close.loc[dt]
        state = check_stop_loss(state, dt, pd_)
        state = check_take_profit(state, dt, pd_, [(0.10,0.30),(0.20,0.30),(0.30,1.00)])
        state = apply_holding_decay(state, dt, pd_, 20)
        
        if (i-120) % 20 == 0 and dt in score_v6b.index:
            ds = score_v6b.loc[dt].dropna()
            ds = ds[ds.index.isin(pd_.dropna().index)]
            
            if use_ind_timing and dt in mom_20.index:
                # 选 top_n_ind 行业
                ind_mom = mom_20.loc[dt].dropna()
                top_inds = ind_mom.nlargest(top_n_ind).index.tolist()
                # 过滤股票
                filtered = {c: ds[c] for c in ds.index if industry_map.get(c) in top_inds}
                if filtered:
                    ds = pd.Series(filtered)
                else:
                    nav.append(portfolio_value(state, dt, pd_))
                    continue
            
            if len(ds) > 0:
                top = []; ic = {}
                for c in ds.sort_values(ascending=False).index:
                    ind = industry_map.get(c, '未知')
                    if ic.get(ind, 0) < 4:
                        top.append(c)
                        ic[ind] = ic.get(ind, 0) + 1
                    if len(top) >= 12: break
                
                if top:
                    cpv = portfolio_value(state, dt, pd_)
                    for c in list(state.holdings):
                        if c not in top and c in pd_.index and not pd.isna(pd_[c]) and pd_[c]>0:
                            state = sell(state, c, pd_[c], dt, 'REBAL')
                    for c in top:
                        if c not in state.holdings and c in pd_.index:
                            p = pd_[c]
                            if pd.isna(p) or p <= 0: continue
                            ap = p * (1 + core_config.costs.slippage_rate)
                            sh = int(min(cpv/12, cpv*0.1) / ap / 100) * 100
                            if sh > 0 and state.cash >= sh * ap:
                                state = buy(state, c, p, dt, sh)
        
        nav.append(portfolio_value(state, dt, pd_))
    
    nav = pd.Series(nav, index=dates)
    rets = nav.pct_change().dropna()
    tr = nav.iloc[-1]/nav.iloc[0]-1; y = max(len(nav)/252, 0.01)
    ar = (1+tr)**(1/y)-1; av = rets.std()*np.sqrt(252)
    sp = ar/av if av > 0 else 0
    peak = nav.cummax(); md = ((nav-peak)/peak).min()
    cm = ar/abs(md) if md != 0 else 0
    print(f"  {label}: 年化={ar*100:.2f}% Sharpe={sp:.3f} 回撤={md*100:.2f}% Calmar={cm:.3f}", flush=True)
    return nav, {'annual':round(ar*100,2),'sharpe':round(sp,3),'dd':round(md*100,2),'calmar':round(cm,3)}

print("\n回测...", flush=True)

print("\n[1] 纯 v6b（全行业）...", flush=True)
t0 = time.time()
n1, r1 = run_bt(False, 0, "纯 v6b")
print(f"  耗时: {time.time()-t0:.0f}s", flush=True)

print("\n[2] 行业轮动 top3...", flush=True)
t0 = time.time()
n2, r2 = run_bt(True, 3, "行业轮动 top3")
print(f"  耗时: {time.time()-t0:.0f}s", flush=True)

print("\n[3] 行业轮动 top5...", flush=True)
t0 = time.time()
n3, r3 = run_bt(True, 5, "行业轮动 top5")
print(f"  耗时: {time.time()-t0:.0f}s", flush=True)

# 汇总
print(f"\n{'='*65}")
print(f"{'策略':>22} | {'年化%':>8} | {'Sharpe':>8} | {'回撤%':>8} | {'Calmar':>8}")
print("-"*65)
for lbl, rv in [("纯 v6b", r1), ("行业轮动 top3", r2), ("行业轮动 top5", r3)]:
    print(f"  {lbl:>20} | {rv['annual']:>8.2f} | {rv['sharpe']:>8.3f} | {rv['dd']:>8.2f} | {rv['calmar']:>8.3f}")

P = [('2023-01-01','2023-06-30','2023H1'), ('2023-07-01','2023-12-31','2023H2'),
     ('2024-01-01','2024-06-30','2024H1'), ('2024-07-01','2024-12-31','2024H2'),
     ('2025-01-01','2025-06-30','2025H1'), ('2025-07-01','2025-12-31','2025H2')]
print(f"\n分时段:")
print(f"  {'时段':>6} | {'纯v6b':>8} | {'行业top3':>9} | {'行业top5':>9}")
for s, e, label in P:
    def gp(n):
        nn = n[(n.index>=s)&(n.index<=e)]
        return (nn.iloc[-1]/nn.iloc[0]-1)*100 if len(nn)>0 else 0
    print(f"  {label:>6} | {gp(n1):>+7.1f}% | {gp(n2):>+8.1f}% | {gp(n3):>+8.1f}%")

print("\n完成")
