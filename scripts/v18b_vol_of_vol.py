#!/usr/bin/env python3
"""
v18b — vol_of_vol 改进版（剔除退市末日轮）
=============================================
v18 问题：140% 年化主要来自 000937 退市末日轮（0.06→3.62，59倍收益）

改进方案：
1. 保持 vol_of_vol 因子
2. 加入严格的退市风险过滤（delist_risk 因子）
3. 排除价格 < 2 元的股票（退市整理期特征）
4. 排除成交额 < 1000 万的股票（流动性过低）

选股逻辑：
  基础：mom_5 > 2%（保持动量框架）
  vol_of_vol 加分：
    vol_of_vol 排名前30%: +0.8（高波动模糊性 = 机会）
  排除：
    delist_risk 前10%: 排除退市风险
    price < 2 元: 排除低价股
    amount < 1000万: 排除低流动性
"""
import sys, os
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from core.db import load_panel_from_db

tpl, _ = load_panel_from_db('2022-01-01', '2026-05-31', need_open=True, need_hl=True)
close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]

eps = 1e-10
returns = close_panel.pct_change()

# 预计算因子
mom_5 = close_panel.pct_change(5)
gap = (open_panel - close_panel.shift(1)) / (close_panel.shift(1) + eps)
avg_amount = amount_panel.rolling(20).mean()
illiq = 1.0 / (avg_amount / 1e8 + eps)
ma20 = close_panel.rolling(20).mean()
std20 = close_panel.rolling(20).std()
boll_w = (4 * std20) / (ma20 + eps)

# vol_of_vol
vol_20_returns = returns.rolling(20).std()
vol_of_vol = vol_20_returns.rolling(20).std()

# 退市风险
price_level = close_panel.rolling(20).mean()
price_trend = close_panel.pct_change(20)
vol_5 = volume_panel.rolling(5).mean()
vol_20 = volume_panel.rolling(20).mean()
vol_shrink = vol_5 / (vol_20 + eps)
vol_current = returns.rolling(5).std()
vol_hist = returns.rolling(60).std()
vol_abnormal = vol_current / (vol_hist + eps)

def _zscore(df):
    m = df.mean(axis=1)
    s = df.std(axis=1)
    return (df.sub(m, axis=0)).div(s + eps, axis=0)

delist_risk = (-_zscore(price_level) + -_zscore(price_trend) +
               -_zscore(vol_shrink) + _zscore(vol_abnormal)) / 4.0

IC = 200000; MH = 8; MDB = 6; MP = 0.20; HM = 5
SL = -0.015; SP = 0.03; CR = 0.0003; ST = 0.001; SR = 0.002

def run_v18b():
    cash = IC; holdings = {}; nav_list = []
    dates = close_panel.index[close_panel.index >= pd.Timestamp('2022-01-01')]
    # 预计算 vol_of_vol 排名
    vov_rank_df = vol_of_vol.rank(axis=1, pct=True)
    # 预计算退市风险阈值
    dr_threshold = delist_risk.quantile(0.9, axis=1)

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
            h = holdings[c]; cash += h['shares'] * sp * (1 - CR - ST - SR); sold.add(c)
        for c in sold: holdings.pop(c, None)

        cands = []
        if date in mom_5.index:
            m5 = mom_5.loc[date].dropna()
            for code in m5.index:
                m = m5[code]
                if m > 0.02:
                    # 排除1：退市风险（预计算阈值）
                    if date in dr_threshold.index and code in delist_risk.columns:
                        dr_val = delist_risk.loc[date, code]
                        dr_thr = dr_threshold.loc[date]
                        if not pd.isna(dr_val) and dr_val > dr_thr: continue
                    # 排除2：低价股
                    if date in close_panel.index and code in close_panel.columns:
                        price = close_panel.loc[date, code]
                        if not pd.isna(price) and price < 2.0: continue
                    # 排除3：低流动性
                    if date in avg_amount.index and code in avg_amount.columns:
                        amt = avg_amount.loc[date, code]
                        if not pd.isna(amt) and amt < 10_000_000: continue  # < 1000万

                    s = m * 100
                    # vol_of_vol 加分（预计算排名）
                    if date in vov_rank_df.index and code in vov_rank_df.columns:
                        vr = vov_rank_df.loc[date, code]
                        if not pd.isna(vr) and vr > 0.7:
                            s += 0.8
                    # gap
                    if date in gap.index and code in gap.columns:
                        gr = gap.loc[date, code]
                        if not pd.isna(gr) and gr > 0.02: s += 0.5
                    # illiq
                    if date in illiq.index and code in illiq.columns:
                        il = illiq.loc[date, code]
                        if not pd.isna(il) and il > 0: s += 0.8
                    # boll
                    if date in boll_w.index and code in boll_w.columns:
                        bw = boll_w.loc[date, code]
                        if not pd.isna(bw) and bw > 1.2: s += 0.3
                    cands.append((code, s))
        cands.sort(key=lambda x: x[1], reverse=True)
        cands = [c for c, s in cands[:MH] if c not in holdings]

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
    return ar, sh, mdd, nav_s

def run_v22_baseline():
    """v22 基线（同参数）"""
    cash = IC; holdings = {}; nav_list = []
    dates = close_panel.index[close_panel.index >= pd.Timestamp('2022-01-01')]

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
            h = holdings[c]; cash += h['shares'] * sp * (1 - CR - ST - SR); sold.add(c)
        for c in sold: holdings.pop(c, None)

        cands = []
        if date in mom_5.index:
            m5 = mom_5.loc[date].dropna()
            for code in m5.index:
                m = m5[code]
                if m > 0.02:
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
    return ar, sh, mdd, nav_s

print("=" * 70)
print("v18b (vol_of_vol 改进) vs v22 基线 对比")
print("=" * 70)

print("\n运行 v22 基线...")
r22 = run_v22_baseline()
print(f"v22 基线: {r22[0]*100:.1f}% / {r22[1]:.2f} / {-r22[2]*100:.1f}%")

print("运行 v18b (vol_of_vol + 严格过滤)...")
r18b = run_v18b()
print(f"v18b:     {r18b[0]*100:.1f}% / {r18b[1]:.2f} / {-r18b[2]*100:.1f}%")

print(f"\n{'='*70}")
print(f"对比结果")
print(f"{'='*70}")
print(f"{'策略':20} | {'年化':>8} | {'夏普':>6} | {'回撤':>8}")
print(f"{'v22 基线':20} | {r22[0]*100:>7.1f}% | {r22[1]:>5.2f} | {-r22[2]*100:>7.1f}%")
print(f"{'v18b (vov+过滤)':20} | {r18b[0]*100:>7.1f}% | {r18b[1]:>5.2f} | {-r18b[2]*100:>7.1f}%")
print(f"\n变化: 年化 {(r18b[0]-r22[0])*100:+.1f}%, 夏普 {r18b[1]-r22[1]:+.2f}, 回撤 {(-r18b[2]+r22[2])*100:+.1f}%")
