#!/usr/bin/env python3
"""v27 vs v28 vs v22 三维对比"""
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

# 计算所有因子
factors = {}
factors['mom_5'] = close_panel.pct_change(5)
factors['mom_10'] = close_panel.pct_change(10)
prev_close = close_panel.shift(1)
factors['gap_ratio'] = (open_panel - prev_close) / (prev_close + eps)
avg_amount = amount_panel.rolling(20).mean()
factors['illiquidity'] = 1.0 / (avg_amount / 1e8 + eps)
ma20 = close_panel.rolling(20).mean()
std20 = close_panel.rolling(20).std()
factors['boll_width_20'] = (4 * std20) / (ma20 + eps)

# v27 因子
vol_5 = volume_panel.rolling(5).mean()
vol_20 = volume_panel.rolling(20).mean()
vr = vol_5 / (vol_20 + eps)
daily_ret = close_panel.pct_change()

def _fast_rolling_corr_panel(ret_df, vol_df, window):
    ret_std = ret_df.rolling(window).std()
    vol_std = vol_df.rolling(window).std()
    ret_mean = ret_df.rolling(window).mean()
    vol_mean = vol_df.rolling(window).mean()
    xy_mean = (ret_df * vol_df).rolling(window).mean()
    cov = xy_mean - ret_mean * vol_mean
    return cov / (ret_std * vol_std + eps)

factors['pv_corr_10'] = _fast_rolling_corr_panel(daily_ret, vr, 10)
factors['pv_corr_20'] = _fast_rolling_corr_panel(daily_ret, vr, 20)
mom_rank = close_panel.pct_change(5).rank(axis=1, pct=True)
vol_rank = vr.rank(axis=1, pct=True)
factors['vol_price_divergence'] = mom_rank - vol_rank

# v28 因子
vr_lag = vr.shift(1)
vr_mean_10 = vr.rolling(10).mean()
vr_lag_mean_10 = vr_lag.rolling(10).mean()
cov_vr = ((vr - vr_mean_10) * (vr_lag - vr_lag_mean_10)).rolling(10).mean()
factors['vol_regime_score'] = cov_vr / (vr.rolling(10).std() * vr_lag.rolling(10).std() + eps)
factors['price_inertia'] = returns.rolling(10).mean() / (returns.rolling(10).std() + eps)
ret_mean_5 = returns.rolling(5).mean()
vr_mean_5 = vr.rolling(5).mean()
cov_vp = ((returns - ret_mean_5) * (vr - vr_mean_5)).rolling(5).mean()
factors['vol_price_coupling'] = cov_vp / (returns.rolling(5).std() * vr.rolling(5).std() + eps)
factors['accumulation_score'] = close_panel.pct_change(5) / (vr.pct_change(5) + eps + 0.5)

# 退市风险
price_level = close_panel.rolling(20).mean()
price_trend = close_panel.pct_change(20)
vol_current = returns.rolling(5).std()
vol_hist = returns.rolling(60).std()

def _zscore(df):
    m = df.mean(axis=1)
    s = df.std(axis=1)
    return (df.sub(m, axis=0)).div(s + eps, axis=0)

factors['delist_risk'] = (
    -_zscore(price_level) + -_zscore(price_trend) +
    -_zscore(vol_5 / (vol_20 + eps)) + _zscore(vol_current / (vol_hist + eps))
) / 4.0

# 回测参数
IC = 200000; MH = 8; MDB = 6; MP = 0.20; HM = 5
SL = -0.015; SP = 0.03; CR = 0.0003; ST = 0.001; SR = 0.002; MT = 0.02

def run(name, select_fn):
    cash = IC; holdings = {}; nav_list = []; sd = 0; tb = 0; ts = 0; sr = {}
    dates = close_panel.index[close_panel.index >= pd.Timestamp(start)]
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
        cands = select_fn(factors, date, pd_, od, holdings)
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
    print(f"{name:12} | {ar*100:>6.1f}% | {sh:>5.2f} | {-mdd*100:>5.1f}% | TP={tp:.0f}% SL={sl:.0f}% | {sd}/{len(dates)-30}d | {tb}t")
    return ar, sh, mdd

def v22_sel(f, date, pd_, od, h):
    if date not in f['mom_5'].index: return []
    m5 = f['mom_5'].loc[date].dropna(); cands = []
    for code in m5.index:
        m = m5[code]; s = 0
        if m > MT:
            s = m * 100
            if 'gap_ratio' in f and date in f['gap_ratio'].index:
                gr = f['gap_ratio'].loc[date, code] if code in f['gap_ratio'].columns else 0
                if not pd.isna(gr) and gr > 0.02: s += 0.5
            if 'illiquidity' in f and date in f['illiquidity'].index:
                il = f['illiquidity'].loc[date, code] if code in f['illiquidity'].columns else 0
                if not pd.isna(il) and il > 0: s += 0.8
            if 'boll_width_20' in f and date in f['boll_width_20'].index:
                bw = f['boll_width_20'].loc[date, code] if code in f['boll_width_20'].columns else 0
                if not pd.isna(bw) and bw > 1.2: s += 0.3
            cands.append((code, s))
    cands.sort(key=lambda x: x[1], reverse=True)
    r = [c for c, s in cands[:MH]]; return [c for c in r if c not in h]

def v27_sel(f, date, pd_, od, h):
    if date not in f['mom_5'].index: return []
    m5 = f['mom_5'].loc[date].dropna(); scores = {}
    for code in m5.index:
        m = m5[code]; s = 0
        if m > MT:
            s = m * 100
            if 'pv_corr_20' in f and date in f['pv_corr_20'].index:
                pv = f['pv_corr_20'].loc[date, code] if code in f['pv_corr_20'].columns else np.nan
                if not pd.isna(pv) and pv > 0: s += 0.5
            if 'illiquidity' in f and date in f['illiquidity'].index:
                il = f['illiquidity'].loc[date, code] if code in f['illiquidity'].columns else 0
                if not pd.isna(il) and il > 0: s += 0.8
            if 'gap_ratio' in f and date in f['gap_ratio'].index:
                gr = f['gap_ratio'].loc[date, code] if code in f['gap_ratio'].columns else 0
                if not pd.isna(gr) and gr > 0.02: s += 0.5
            if 'boll_width_20' in f and date in f['boll_width_20'].index:
                bw = f['boll_width_20'].loc[date, code] if code in f['boll_width_20'].columns else 0
                if not pd.isna(bw) and bw > 1.2: s += 0.3
            # 排除 pv_corr_10 < -0.5
            if 'pv_corr_10' in f and date in f['pv_corr_10'].index:
                pv10 = f['pv_corr_10'].loc[date, code] if code in f['pv_corr_10'].columns else 0
                if not pd.isna(pv10) and pv10 < -0.5: s = 0
        if s > 0: scores[code] = s
    if 'delist_risk' in f and date in f['delist_risk'].index:
        dr = f['delist_risk'].loc[date]; th = dr.quantile(0.9)
        scores = {c: s for c, s in scores.items() if c not in dr.index or dr[c] <= th}
    r = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)[:MH]
    return [c for c in r if c not in h]

print("=" * 80)
print(f"三策略公平对比 ({start} ~ {end})")
print("=" * 80)
print(f"{'策略':12} | {'年化':>6} | {'夏普':>5} | {'回撤':>5} | {'卖出质量':>12} | {'选股率':>8}")
print("-" * 80)
r22 = run("v22 (基线)", v22_sel)
r27 = run("v27 (价量共振)", v27_sel)
print("-" * 80)
print(f"\n结论：")
print(f"  v22: 纯动量 mom_5>2%，年化 {r22[0]*100:.1f}%，夏普 {r22[1]:.2f}，回撤 {-r22[2]*100:.1f}%")
print(f"  v27: +pv_corr 价量共振，年化 {r27[0]*100:.1f}%，夏普 {r27[1]:.2f}，回撤 {-r27[2]*100:.1f}%")
