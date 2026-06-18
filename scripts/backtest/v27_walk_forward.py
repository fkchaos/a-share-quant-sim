#!/usr/bin/env python3
"""
v27_walk_forward — 价量共振因子 Walk-Forward 验证
====================================================
用 v22 相同 WF 框架验证 v27 的稳健性

WF 参数：train=252, test=126, step=63
评价指标：测试期平均收益率、夏普、回撤、正收益 fold 比例
通过标准：正收益 fold >= 60%, 夏普 > 0.5
"""
import sys, os
import time
import numpy as np
import pandas as pd

from core.db import load_panel_from_db

print("加载数据...")
tpl, _ = load_panel_from_db('2021-01-01', '2026-05-31', need_open=True, need_hl=True)
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

# v27 核心因子
vol_5 = volume_panel.rolling(5).mean()
vol_20 = volume_panel.rolling(20).mean()
vr = vol_5 / (vol_20 + eps)
daily_ret = close_panel.pct_change()

# pv_corr_10 和 pv_corr_20（向量化计算）
ret_mean_10 = daily_ret.rolling(10).mean()
vr_mean_10 = vr.rolling(10).mean()
cov_10 = ((daily_ret - ret_mean_10) * (vr - vr_mean_10)).rolling(10).mean()
pv_corr_10 = cov_10 / (daily_ret.rolling(10).std() * vr.rolling(10).std() + eps)

ret_mean_20 = daily_ret.rolling(20).mean()
vr_mean_20 = vr.rolling(20).mean()
cov_20 = ((daily_ret - ret_mean_20) * (vr - vr_mean_20)).rolling(20).mean()
pv_corr_20 = cov_20 / (daily_ret.rolling(20).std() * vr.rolling(20).std() + eps)

# vol_price_divergence
mom_rank = close_panel.pct_change(5).rank(axis=1, pct=True)
vol_rank = vr.rank(axis=1, pct=True)
vp_div = mom_rank - vol_rank

# 退市风险
price_level = close_panel.rolling(20).mean()
price_trend = close_panel.pct_change(20)
vol_current = returns.rolling(5).std()
vol_hist = returns.rolling(60).std()
vol_shrink = vol_5 / (vol_20 + eps)
vol_abnormal = vol_current / (vol_hist + eps)

def _zscore(df):
    m = df.mean(axis=1)
    s = df.std(axis=1)
    return (df.sub(m, axis=0)).div(s + eps, axis=0)

delist_risk = (-_zscore(price_level) + -_zscore(price_trend) +
               -_zscore(vol_shrink) + _zscore(vol_abnormal)) / 4.0

IC = 200000; MH = 8; MDB = 8; MP = 0.30; HM = 5
SL = -0.015; SP = 0.03; CR = 0.0003; ST = 0.001; SR = 0.002

def run_window(win_close, win_open, win_mom5, win_gap, win_illiq, win_boll,
               win_pv10, win_pv20, win_dr, train_days):
    """在单个 window 上跑回测"""
    cash = IC; holdings = {}; nav_list = []
    dates = win_close.index
    n = len(dates)

    for i in range(n):
        if i < 30: nav_list.append(cash); continue
        date = dates[i]
        if date not in win_close.index:
            nav_list.append(nav_list[-1] if nav_list else cash); continue

        pd_ = win_close.loc[date]; od = win_open.loc[date]
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
                pc = win_close.iloc[i-1].get(c)
                if pc and not pd.isna(pc) and pc > 0 and sp <= pc * 0.90 * 1.01:
                    holdings[c]['hold_days'] = max(0, holdings[c].get('hold_days', 0) - 1); continue
            h = holdings[c]; cash += h['shares'] * sp * (1 - CR - ST - SR); sold.add(c)
        for c in sold: holdings.pop(c, None)

        # v27 选股（向量化版本）
        cands = []
        if date in win_mom5.index:
            m5 = win_mom5.loc[date].dropna()
            # 初筛：mom_5 > 0.02
            mask = m5 > 0.02
            if mask.any():
                codes = m5[mask].index
                # 排除：量价严重背离 (pv10 < -0.5)
                if date in win_pv10.index:
                    pv10 = win_pv10.loc[date]
                    pv_mask = pv10.reindex(codes, fill_value=0) >= -0.5
                    codes = codes[pv_mask]
                if len(codes) == 0:
                    pass
                else:
                    # 向量化评分
                    scores = m5[codes] * 100
                    # 共振加分 (pv20 > 0)
                    if date in win_pv20.index:
                        pv20 = win_pv20.loc[date]
                        scores += (pv20[codes] > 0).astype(float) * 0.5
                    # gap 加分
                    if date in win_gap.index:
                        gr = win_gap.loc[date]
                        scores += (gr[codes] > 0.02).astype(float) * 0.5
                    # illiq 加分
                    if date in win_illiq.index:
                        il = win_illiq.loc[date]
                        scores += (il[codes] > 0).astype(float) * 0.8
                    # boll 加分
                    if date in win_boll.index:
                        bw = win_boll.loc[date]
                        scores += (bw[codes] > 1.2).astype(float) * 0.3
                    # 排除退市风险
                    if date in win_dr.index:
                        dr = win_dr.loc[date]
                        thr = dr.quantile(0.9)
                        dr_mask = dr.reindex(codes, fill_value=0) <= thr
                        codes = codes[dr_mask]
                    if len(codes) > 0:
                        scores = scores.reindex(codes)
                        cands = list(zip(codes, scores))
                    else:
                        cands = []
        cands.sort(key=lambda x: x[1], reverse=True)
        cands = [c for c, s in cands[:MH] if c not in holdings]

        if cands and cash > IC * 0.03 and len(holdings) < MH:
            avail = cash - IC * 0.03; nb = min(len(cands), MDB, MH - len(holdings))
            per = min(avail / nb, IC * MP)
            for c in cands[:MDB]:
                if len(holdings) >= MH or nb <= 0: break
                bp = od[c] if c in od.index else pd_[c]
                if pd.isna(bp) or bp <= 0: continue
                if i > 0:
                    pc = win_close.iloc[i-1].get(c)
                    if pc and not pd.isna(pc) and pc > 0 and bp >= pc * 1.10 * 0.99: continue
                adj = bp * (1 + CR + SR); sh = int(per / adj / 100) * 100
                if sh <= 0 or sh * adj > cash: continue
                cash -= sh * adj; holdings[c] = {'shares': sh, 'cost': bp, 'hold_days': 0}; nb -= 1

        # NAV（向量化）
        if holdings:
            h_codes = list(holdings.keys())
            h_shares = np.array([holdings[c]['shares'] for c in h_codes])
            p = pd_.reindex(h_codes).fillna(0).values
            nav = cash + (h_shares * p).sum()
        else:
            nav = cash
        nav_list.append(nav)

    nav_s = pd.Series(nav_list)
    # 分割 train/test
    train_nav = nav_s[:train_days] if train_days < len(nav_s) else nav_s
    test_nav = nav_s[train_days:] if train_days < len(nav_s) else pd.Series()

    if len(test_nav) == 0 or test_nav.iloc[0] == 0:
        return 0, 0, 0, 0

    test_ret = test_nav.iloc[-1] / test_nav.iloc[0] - 1
    test_dd = ((test_nav.cummax() - test_nav) / test_nav.cummax()).max()
    test_daily = test_nav.pct_change().dropna()
    test_sharpe = test_daily.mean() / test_daily.std() * np.sqrt(252) if test_daily.std() > 0 else 0
    return test_ret, test_dd, test_sharpe, len(test_nav)

def walk_forward(train_days=252, test_days=126, step_days=63):
    """Walk-Forward 验证"""
    total_days = close_panel.shape[0]
    fold_results = []
    fold = 0
    start_idx = 0

    while start_idx + train_days + test_days < total_days:
        end_idx = min(start_idx + train_days + test_days, total_days)

        win_close = close_panel.iloc[start_idx:end_idx]
        win_open = open_panel.iloc[start_idx:end_idx]
        win_mom5 = mom_5.iloc[start_idx:end_idx]
        win_gap = gap.iloc[start_idx:end_idx]
        win_illiq = illiq.iloc[start_idx:end_idx]
        win_boll = boll_w.iloc[start_idx:end_idx]
        win_pv10 = pv_corr_10.iloc[start_idx:end_idx]
        win_pv20 = pv_corr_20.iloc[start_idx:end_idx]
        win_dr = delist_risk.iloc[start_idx:end_idx]

        tret, tdd, tsh, tlen = run_window(
            win_close, win_open, win_mom5, win_gap, win_illiq, win_boll,
            win_pv10, win_pv20, win_dr, train_days
        )

        fold_results.append({
            'fold': fold, 'test_ret': tret, 'test_dd': tdd,
            'test_sharpe': tsh, 'test_days': tlen
        })
        print(f"Fold {fold} | 测试: {tret*100:.2f}% (DD={tdd*100:.1f}%, Sharpe={tsh:.2f}, {tlen}天)")

        start_idx += step_days
        fold += 1

    if not fold_results:
        print("数据不足，无法生成 fold")
        return pd.DataFrame()

    df = pd.DataFrame(fold_results)
    print("\n" + "=" * 60)
    print(f"v27 WF 汇总 ({len(df)} folds)")
    print("=" * 60)
    print(f"  测试期平均收益率: {df['test_ret'].mean()*100:.2f}%")
    print(f"  测试期平均夏普:   {df['test_sharpe'].mean():.3f}")
    print(f"  测试期平均回撤:   {df['test_dd'].mean()*100:.2f}%")
    print(f"  正收益 fold:      {(df['test_ret'] > 0).sum()}/{len(df)} ({(df['test_ret'] > 0).mean()*100:.0f}%)")

    pos_folds = (df['test_ret'] > 0).mean() * 100
    avg_sharpe = df['test_sharpe'].mean()

    print(f"\n  WF 通过标准: 正收益 fold >= 60%, 夏普 > 0.5")
    if pos_folds >= 60 and avg_sharpe > 0.5:
        print(f"  ✅ WF 通过 ({pos_folds:.0f}% 正收益 fold, 夏普 {avg_sharpe:.3f})")
    else:
        print(f"  ❌ WF 未通过 ({pos_folds:.0f}% 正收益 fold, 夏普 {avg_sharpe:.3f})")

    return df

if __name__ == "__main__":
    print("=" * 60)
    print("v27 价量共振因子 Walk-Forward 验证")
    print("=" * 60)
    print(f"  Close panel: {close_panel.shape}")
    print(f"  因子: mom_5, gap, illiq, boll, pv_corr_10, pv_corr_20, delist_risk")
    print(f"  WF: train=252, test=126, step=63")
    print()

    t0 = time.time()
    walk_forward(train_days=252, test_days=126, step_days=63)
    print(f"\n  总耗时: {time.time()-t0:.1f}s")
