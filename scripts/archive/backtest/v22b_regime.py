#!/usr/bin/env python3
"""
v22b — v22 + 大势研判过滤器
=============================
识别熊市状态，在熊市降低仓位或暂停交易

大势研判信号：
1. MA60 斜率 < 0（市场趋势向下）
2. 全市场涨跌比 < 40%（多数股票下跌）
3. 市场波动率 > 历史 75% 分位（恐慌）

熊市定义：以上 3 个信号中 >= 2 个触发

策略：
- 正常状态：满仓操作（max_holdings=8）
- 熊市状态：半仓操作（max_holdings=4）或暂停买入
- 恢复信号：连续 5 天 MA60 斜率 > 0 → 恢复正常
"""
import sys, os
import numpy as np
import pandas as pd

from core.db import load_panel_from_db

tpl, _ = load_panel_from_db('2022-01-01', '2026-05-31', need_open=True, need_hl=True)
close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
open_panel = tpl[3]

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

gap_bonus = (gap > 0.02).astype(float) * 0.5
illiq_bonus = (illiq > 0).astype(float) * 0.8
boll_bonus = (boll_w > 1.2).astype(float) * 0.3
bonus = gap_bonus + illiq_bonus + boll_bonus

# 大势研判信号
market_ret = returns.median(axis=1)
market_ma60 = market_ret.rolling(60).mean()
ma60_slope = market_ma60.pct_change(20)
slope_signal = ma60_slope < 0  # 趋势向下

up_ratio = (returns > 0).sum(axis=1) / returns.shape[1]
up_signal = up_ratio < 0.4  # 多数下跌

vol_median = returns.rolling(20).std().median(axis=1)
vol_threshold = vol_median.rolling(252).quantile(0.75)
vol_signal = vol_median > vol_threshold  # 高波动

# 熊市状态：>= 2 个信号触发
bear_score = slope_signal.astype(int) + up_signal.astype(int) + vol_signal.astype(int)
is_bear = bear_score >= 2

# 恢复信号：连续 5 天 MA60 斜率 > 0
recovery = (ma60_slope > 0).rolling(5).sum() == 5

# 实际仓位乘数
position_multiplier = pd.Series(1.0, index=close_panel.index)
position_multiplier[is_bear] = 0.5  # 熊市半仓
position_multiplier[recovery] = 1.0  # 恢复满仓

IC = 200000; MH = 8; MDB = 6; MP = 0.20; HM = 5
SL = -0.015; SP = 0.03; CR = 0.0003; ST = 0.001; SR = 0.002

def run_with_regime():
    cash = IC; holdings = {}; nav_list = []
    dates = close_panel.index
    start_idx = 0
    for idx, d in enumerate(dates):
        if d >= pd.Timestamp('2022-01-01'):
            start_idx = idx; break

    for i in range(start_idx, len(dates)):
        date = dates[i]
        if i < start_idx + 30: nav_list.append(cash); continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else cash); continue

        pd_ = close_panel.loc[date]; od = open_panel.loc[date]
        pm = position_multiplier.loc[date] if date in position_multiplier.index else 1.0
        effective_mh = max(2, int(MH * pm))  # 最少 2 只

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
            valid = base > 0.02 * 100
            scores = (base + bn)[valid].sort_values(ascending=False)
            cands = [c for c in scores.index[:effective_mh] if c not in holdings]

        if cands and cash > IC * 0.1 and len(holdings) < effective_mh:
            avail = cash - IC * 0.1; nb = min(len(cands), MDB, effective_mh - len(holdings))
            per = min(avail / nb, IC * MP)
            for c in cands[:MDB]:
                if len(holdings) >= effective_mh or nb <= 0: break
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

def run_baseline():
    """v22 基线（无过滤器）"""
    cash = IC; holdings = {}; nav_list = []
    dates = close_panel.index
    start_idx = 0
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
            valid = base > 0.02 * 100
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

print("=" * 70)
print("大势研判过滤器对比 (2022-2026)")
print("=" * 70)

print("\n运行 v22 基线...")
r_base = run_baseline()
print(f"v22 基线:     {r_base[0]*100:.1f}% / {r_base[1]:.2f} / {-r_base[2]*100:.1f}%")

print("运行 v22b (大势研判)...")
r_bear = run_with_regime()
print(f"v22b (过滤器): {r_bear[0]*100:.1f}% / {r_bear[1]:.2f} / {-r_bear[2]*100:.1f}%")

print(f"\n{'='*70}")
print(f"对比结果")
print(f"{'='*70}")
print(f"{'策略':20} | {'年化':>8} | {'夏普':>6} | {'回撤':>8}")
print(f"{'v22 基线':20} | {r_base[0]*100:>7.1f}% | {r_base[1]:>5.2f} | {-r_base[2]*100:>7.1f}%")
print(f"{'v22b (大势研判)':20} | {r_bear[0]*100:>7.1f}% | {r_bear[1]:>5.2f} | {-r_bear[2]*100:>7.1f}%")
print(f"\n变化: 年化 {(r_bear[0]-r_base[0])*100:+.1f}%, 夏普 {r_bear[1]-r_base[1]:+.2f}, 回撤 {(-r_bear[2]+r_base[2])*100:+.1f}%")

if r_bear[2] < r_base[2]:
    print("✅ 回撤改善")
else:
    print("❌ 回撤未改善")
