#!/usr/bin/env python3
"""完整对比: v22完整版 vs v22+pv_corr排除（优化版）"""
import sys, os
import numpy as np
import pandas as pd

from core.db import load_panel_from_db

start, end = '2022-01-01', '2026-05-31'
tpl, _ = load_panel_from_db(start, end, need_open=True, need_hl=True)
close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]

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

# pv_corr_10
vol_5 = volume_panel.rolling(5).mean()
vol_20 = volume_panel.rolling(20).mean()
vr = vol_5 / (vol_20 + eps)
ret_mean_10 = returns.rolling(10).mean()
vr_mean_10 = vr.rolling(10).mean()
cov = ((returns - ret_mean_10) * (vr - vr_mean_10)).rolling(10).mean()
pv_corr_10 = cov / (returns.rolling(10).std() * vr.rolling(10).std() + eps)

IC = 200000; MH = 8; MDB = 6; MP = 0.20; HM = 5
SL = -0.015; SP = 0.03; CR = 0.0003; ST = 0.001; SR = 0.002; MT = 0.02

def run(name, use_filter=False):
    cash = IC; holdings = {}; nav_list = []; sd = 0; tb = 0; ts = 0; sr = {}
    dates = close_panel.index[close_panel.index >= pd.Timestamp(start)]
    mom5 = close_panel.pct_change(5)  # 预计算

    for i, date in enumerate(dates):
        if i < 30: nav_list.append(cash); continue
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
            h = holdings[c]; cash += h['shares'] * sp * (1 - CR - ST - SR); sold.add(c); ts += 1; sr[reason] = sr.get(reason, 0) + 1
        for c in sold: holdings.pop(c, None)

        # 选股
        cands = []
        if date in mom5.index:
            m5 = mom5.loc[date].dropna()
            for code in m5.index:
                m = m5[code]
                if m > MT:
                    if use_filter and date in pv_corr_10.index and code in pv_corr_10.columns:
                        pv = pv_corr_10.loc[date, code]
                        if not pd.isna(pv) and pv < -0.5: continue
                    s = m * 100
                    if date in gap.index and code in gap.columns:
                        gr = gap.loc[date, code]
                        if not pd.isna(gr) and gr > 0.02: s += 0.5
                    if date in illiq.index and code in illiq.columns:
                        il = illiq.loc[date, code]
                        if not pd.isna(il) and il > 0: s += 0.8
                    if date in boll_w.index and code in boll_w.columns:
                        bw = boll_w.loc[date, code]
                        if not pd.isna(bw) and bw > 1.2: s += 0.3
                    cands.append((code, s))
        cands.sort(key=lambda x: x[1], reverse=True)
        cands = [c for c, s in cands[:MH] if c not in holdings]

        if cands and cash > IC * 0.1 and len(holdings) < MH:
            avail = cash - IC * 0.1; nb = min(len(cands), MDB, MH - len(holdings))
            per = min(avail / nb, IC * MP); bought = 0
            for c in cands[:MDB]:
                if bought >= nb: break
                bp = od[c] if c in od.index else pd_[c]
                if pd.isna(bp) or bp <= 0: continue
                if i > 0:
                    pc = close_panel.iloc[i-1].get(c)
                    if pc and not pd.isna(pc) and pc > 0 and bp >= pc * 1.10 * 0.99: continue
                adj = bp * (1 + CR + SR); sh = int(per / adj / 100) * 100
                if sh <= 0 or sh * adj > cash: continue
                cash -= sh * adj; holdings[c] = {'shares': sh, 'cost': bp, 'hold_days': 0}; bought += 1; tb += 1
        if cands: sd += 1
        nav = cash + sum(h['shares'] * pd_[c] for c, h in holdings.items() if c in pd_.index and not pd.isna(pd_[c]) and pd_[c] > 0)
        nav_list.append(nav)

    nav_s = pd.Series(nav_list); ret = nav_s.pct_change().dropna()
    total = nav_s.iloc[-1] / IC - 1; days = len(nav_list) - 30
    ar = (1 + total) ** (365 / max(days, 1)) - 1
    sh = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0
    mdd = ((nav_s.cummax() - nav_s) / nav_s.cummax()).max()
    tp = sr.get('TP', 0) / max(ts, 1) * 100; sl = sr.get('SL', 0) / max(ts, 1) * 100
    print(f"{name:40} | {ar*100:>6.1f}% | {sh:>5.2f} | {-mdd*100:>5.1f}% | TP={tp:.0f}% SL={sl:.0f}% | {tb}t")
    return ar, sh, mdd

print("=" * 95)
print(f"完整对比 ({start} ~ {end})")
print("=" * 90)
r22 = run("v22 完整 (mom+gap+illiq+boll)", use_filter=False)
r27b = run("v27b (+pv_corr排除< -0.5)", use_filter=True)
print("-" * 90)
ret_chg = (r27b[0]-r22[0])*100
sha_chg = r27b[1]-r22[1]
dd_chg = (-r27b[2]+r22[2])*100
print(f"收益变化: {ret_chg:+.1f}%  夏普变化: {sha_chg:+.2f}  回撤变化: {dd_chg:+.1f}%")
if abs(ret_chg) < 2 and dd_chg > 0:
    print("✅ 几乎不损失收益，回撤改善")
elif ret_chg > 0 and dd_chg > 0:
    print("✅ 收益和回撤都改善")
elif ret_chg < -5:
    print("❌ 收益损失过大")
else:
    print("⚠️ 收益略有损失，需权衡")
