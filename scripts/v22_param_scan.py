#!/usr/bin/env python3
"""
v22 参数扫描（单参数版）
扫描 mom_threshold: [0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10]
固定: max_holdings=8, max_daily_buy=6, hold_max=5, SL=-1.5%, TP=3%
"""
import sys, os
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from core.db import load_panel_from_db

print("加载数据...")
tpl, _ = load_panel_from_db('2022-01-01', '2026-05-31', need_open=True, need_hl=True)
close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
open_panel = tpl[3]

eps = 1e-10
returns = close_panel.pct_change()

# 预计算所有因子
mom_5 = close_panel.pct_change(5)
gap = (open_panel - close_panel.shift(1)) / (close_panel.shift(1) + eps)
avg_amount = amount_panel.rolling(20).mean()
illiq = 1.0 / (avg_amount / 1e8 + eps)
ma20 = close_panel.rolling(20).mean()
std20 = close_panel.rolling(20).std()
boll_w = (4 * std20) / (ma20 + eps)

gap_bonus = (gap > 0.02).astype(float) * 0.5
illiq_bonus = (illiq > 0).astype(float) * 0.8
boll_bonus = (boll_w > 1.2).astype(float) * 0.3
bonus = gap_bonus + illiq_bonus + boll_bonus

IC = 200000; MH = 8; MDB = 6; MP = 0.20; HM = 5
SL = -0.015; SP = 0.03; CR = 0.0003; ST = 0.001; SR = 0.002

def run_fast(mom_threshold):
    cash = IC; holdings = {}; nav_list = []
    dates = close_panel.index
    start_idx = 0
    # 找到 2022-01-01 的索引
    for idx, d in enumerate(dates):
        if d >= pd.Timestamp('2022-01-01'):
            start_idx = idx; break

    for i in range(start_idx, len(dates)):
        date = dates[i]
        if i < start_idx + 30: nav_list.append(cash); continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else cash); continue

        pd_ = close_panel.loc[date]; od = open_panel.loc[date]
        for c in holdings: holdings[c]['hold_days'] = holdings[c].get('hold_days', 0) + 1

        to_sell = []
        for c, h in holdings.items():
            if c not in pd_.index: continue
            cp = pd_[c]
            if pd.isna(cp) or cp <= 0: continue
            pnl = (cp - h['cost']) / h['cost']
            if pnl <= SL: to_sell.append((c, 'SL')); continue
            if pnl >= SP: to_sell.append((c, 'TP')); continue
            if h.get('hold_days', 0) >= HM: to_sell.append((c, 'TO'))

        sold = set()
        for c, reason in to_sell:
            if c not in pd_.index: continue
            sp = pd_[c]
            if pd.isna(sp) or sp <= 0: continue
            if i > 0:
                pc = close_panel.iloc[i-1].get(c)
                if pc and not pd.isna(pc) and pc > 0 and sp <= pc * 0.90 * 1.01:
                    holdings[c]['hold_days'] = max(0, holdings[c].get('hold_days', 0) - 1); continue
            h = holdings[c]; cash += h['shares'] * sp * (1 - CR - ST - SR); sold.add(c)
        for c in sold: holdings.pop(c, None)

        cands = []
        if date in mom_5.index:
            m5 = mom_5.loc[date]
            base = m5 * 100
            bn = bonus.loc[date] if date in bonus.index else 0
            valid = base > mom_threshold * 100
            scores = (base + bn)[valid].sort_values(ascending=False)
            cands = [c for c in scores.index[:MH] if c not in holdings]

        if cands and cash > IC * 0.1 and len(holdings) < MH:
            avail = cash - IC * 0.1; nb = min(len(cands), MDB, MH - len(holdings))
            per = min(avail / nb, IC * MP)
            for c in cands[:MDB]:
                if len(holdings) >= MH or nb <= 0: break
                bp = od[c] if c in od.index else pd_[c]
                if pd.isna(bp) or bp <= 0: continue
                if i > 0:
                    pc = close_panel.iloc[i-1].get(c)
                    if pc and not pd.isna(pc) and pc > 0 and bp >= pc * 1.10 * 0.99: continue
                adj = bp * (1 + CR + SR); sh = int(per / adj / 100) * 100
                if sh <= 0 or sh * adj > cash: continue
                cash -= sh * adj; holdings[c] = {'shares': sh, 'cost': bp, 'hold_days': 0}; nb -= 1

        nav = cash + sum(h['shares'] * pd_[c] for c, h in holdings.items()
                        if c in pd_.index and not pd.isna(pd_[c]) and pd_[c] > 0)
        nav_list.append(nav)

    nav_s = pd.Series(nav_list)
    total = nav_s.iloc[-1] / IC - 1
    days = len(nav_list) - 30
    ar = (1 + total) ** (365 / max(days, 1)) - 1
    ret = nav_s.pct_change().dropna()
    sh = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0
    mdd = ((nav_s.cummax() - nav_s) / nav_s.cummax()).max()
    return ar, sh, mdd

print("\n" + "=" * 60)
print("mom_threshold 参数扫描 (2022-2026)")
print("=" * 60)
print(f"\n{'threshold':>10} | {'年化':>8} | {'夏普':>6} | {'回撤':>8}")
print("-" * 45)

thresholds = [0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10]
t0 = time.time()
scan_results = []

for mt in thresholds:
    ar, sh, mdd = run_fast(mt)
    scan_results.append({'mt': mt, 'ar': ar, 'sh': sh, 'mdd': mdd})
    print(f"{mt:>10.3f} | {ar*100:>7.1f}% | {sh:>5.2f} | {mdd*100:>7.1f}%")

elapsed = time.time() - t0
print(f"\n扫描完成 ({elapsed:.0f}s)")

best = max(scan_results, key=lambda x: x['sh'])
print(f"\n最优 mom_threshold: {best['mt']:.3f}")
print(f"  年化: {best['ar']*100:.1f}%  夏普: {best['sh']:.2f}  回撤: {best['mdd']*100:.1f}%")

default = next((r for r in scan_results if r['mt'] == 0.02), None)
if default:
    print(f"  vs 默认(0.02={default['ar']*100:.1f}%/{default['sh']:.2f}/{default['mdd']*100:.1f}%):")
    print(f"    年化 {best['ar']*100 - default['ar']*100:+.1f}%, 夏普 {best['sh'] - default['sh']:+.2f}, 回撤 {(-best['mdd']+default['mdd'])*100:+.1f}%")
