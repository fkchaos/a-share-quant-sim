#!/usr/bin/env python3
"""
v28_walk_forward — Kronos AI 增强选股 Walk-Forward 验证
=====================================================
基于 v27 WF 框架，加入 Kronos 预测因子做二次排序。

由于 Kronos 预测每个 fold 约 2-3 分钟，16 fold 总计约 30-45 分钟。
先用 v28 框架验证（KRONOS_ENABLED=False 等同于 v27），再开启对比。

WF 参数：train=252, test=126, step=63
评价指标：测试期平均收益率、夏普、回撤、正收益 fold 比例
"""
import sys, os
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from core.db import load_panel_from_db

# WF 参数（与 v27 一致，用于公平对比）
IC = 200000; MH = 8; MDB = 8; MP = 0.30; HM = 5
SL = -0.015; SP = 0.03; CR = 0.0003; ST = 0.001; SR = 0.002

# Kronos 配置
KRONOS_ENABLED = False  # 先关闭验证框架一致性，再开启跑对比
KRONOS_CANDIDATE_N = 50
KRONOS_PRED_LEN = 5
KRONOS_LOOKBACK = 200
KRONOS_T = 0.8
KRONOS_TOP_P = 0.85
KRONOS_SAMPLE_COUNT = 5
KRONOS_DEVICE = "cpu"
KRONOS_MODEL = "small"

print("加载数据...")
tpl, _ = load_panel_from_db('2021-01-01', '2026-05-31', need_open=True, need_hl=True)
close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]

eps = 1e-10
returns = close_panel.pct_change()

# 预计算 v27 因子（与 v27 WF 完全一致）
mom_5 = close_panel.pct_change(5)
gap = (open_panel - close_panel.shift(1)) / (close_panel.shift(1) + eps)
avg_amount = amount_panel.rolling(20).mean()
illiq = 1.0 / (avg_amount / 1e8 + eps)
ma20 = close_panel.rolling(20).mean()
std20 = close_panel.rolling(20).std()
boll_w = (4 * std20) / (ma20 + eps)

vol_5 = volume_panel.rolling(5).mean()
vol_20 = volume_panel.rolling(20).mean()
vr = vol_5 / (vol_20 + eps)
daily_ret = close_panel.pct_change()

ret_mean_10 = daily_ret.rolling(10).mean()
vr_mean_10 = vr.rolling(10).mean()
cov_10 = ((daily_ret - ret_mean_10) * (vr - vr_mean_10)).rolling(10).mean()
pv_corr_10 = cov_10 / (daily_ret.rolling(10).std() * vr.rolling(10).std() + eps)

ret_mean_20 = daily_ret.rolling(20).mean()
vr_mean_20 = vr.rolling(20).mean()
cov_20 = ((daily_ret - ret_mean_20) * (vr - vr_mean_20)).rolling(20).mean()
pv_corr_20 = cov_20 / (daily_ret.rolling(20).std() * vr.rolling(20).std() + eps)

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

# ── v27 初筛函数（向量化，与 v27 WF 完全一致）──
def v27_candidates(date, win_data):
    """返回 v27 初筛后的 (code, v27_score) 列表"""
    d = date
    if d not in win_data['mom5'].index:
        return []

    m5 = win_data['mom5'].loc[d].dropna()
    mask = m5 > 0.02
    if not mask.any():
        return []

    codes = m5[mask].index

    # 排除：量价严重背离
    if d in win_data['pv10'].index:
        pv10 = win_data['pv10'].loc[d]
        pv_mask = pv10.reindex(codes, fill_value=0) >= -0.5
        codes = codes[pv_mask]

    if len(codes) == 0:
        return []

    scores = m5[codes] * 100

    if d in win_data['pv20'].index:
        pv20 = win_data['pv20'].loc[d]
        scores += (pv20[codes] > 0).astype(float) * 0.5
    if d in win_data['gap'].index:
        gr = win_data['gap'].loc[d]
        scores += (gr[codes] > 0.02).astype(float) * 0.5
    if d in win_data['illiq'].index:
        il = win_data['illiq'].loc[d]
        scores += (il[codes] > 0).astype(float) * 0.8
    if d in win_data['boll'].index:
        bw = win_data['boll'].loc[d]
        scores += (bw[codes] > 1.2).astype(float) * 0.3

    # 排除退市风险
    if d in win_data['dr'].index:
        dr = win_data['dr'].loc[d]
        thr = dr.quantile(0.9)
        dr_mask = dr.reindex(codes, fill_value=0) <= thr
        codes = codes[dr_mask]

    if len(codes) == 0:
        return []

    scores = scores.reindex(codes)
    cands = list(zip(codes, scores))
    cands.sort(key=lambda x: x[1], reverse=True)
    return cands


def run_window(win_close, win_open, train_days, fold_idx):
    """在单个 window 上跑回测"""
    cash = IC; holdings = {}; nav_list = []
    dates = win_close.index
    n = len(dates)

    # 构建窗口数据字典
    win_data = {
        'mom5': mom_5.loc[dates[0]:dates[-1]],
        'gap': gap.loc[dates[0]:dates[-1]],
        'illiq': illiq.loc[dates[0]:dates[-1]],
        'boll': boll_w.loc[dates[0]:dates[-1]],
        'pv10': pv_corr_10.loc[dates[0]:dates[-1]],
        'pv20': pv_corr_20.loc[dates[0]:dates[-1]],
        'dr': delist_risk.loc[dates[0]:dates[-1]],
    }

    for i in range(n):
        if i < 30:
            nav_list.append(cash); continue
        date = dates[i]
        if date not in win_close.index:
            nav_list.append(nav_list[-1] if nav_list else cash); continue

        pd_ = win_close.loc[date]; od = win_open.loc[date]
        for c in holdings:
            holdings[c]['hold_days'] = holdings[c].get('hold_days', 0) + 1

        # 卖出逻辑
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

        # ── v28 选股 ──
        cands = v27_candidates(date, win_data)

        if KRONOS_ENABLED and cands and fold_idx >= 0:
            # Kronos 增强：对 Top N 候选跑预测
            from scripts.strategies.v28_kronos import _load_kronos_model, _get_kline_from_db, _predict_single
            predictor, loaded = _load_kronos_model({
                "KRONOS_MODEL": KRONOS_MODEL,
                "KRONOS_DEVICE": KRONOS_DEVICE,
            })
            if loaded:
                top_cands = cands[:KRONOS_CANDIDATE_N]
                v27_scores = {c: s for c, s in top_cands}
                date_str = pd.Timestamp(date).strftime('%Y-%m-%d')
                kronos_results = {}
                for code, _ in top_cands:
                    df_hist = _get_kline_from_db(code, KRONOS_LOOKBACK, date_str)
                    if df_hist is not None:
                        result = _predict_single(predictor, df_hist, KRONOS_PRED_LEN, {
                            "KRONOS_T": KRONOS_T,
                            "KRONOS_TOP_P": KRONOS_TOP_P,
                            "KRONOS_SAMPLE_COUNT": KRONOS_SAMPLE_COUNT,
                        })
                        if result:
                            kronos_results[code] = result

                if kronos_results:
                    alpha = 0.5
                    conf_threshold = 0.3
                    final_cands = []
                    for code, v27_score in top_cands:
                        if code in kronos_results:
                            kr = kronos_results[code]
                            conf_weight = min(kr['kronos_conf'] / conf_threshold, 1.0)
                            enhance = alpha * kr['kronos_ret'] * conf_weight
                            final_cands.append((code, v27_score + enhance))
                        else:
                            final_cands.append((code, v27_score))
                    final_cands.sort(key=lambda x: x[1], reverse=True)
                    cands = final_cands

        # 买入
        cands = [c for c, s in cands[:MH] if c not in holdings]
        if cands and cash > IC * 0.03 and len(holdings) < MH:
            avail = cash - IC * 0.03
            nb = min(len(cands), MDB, MH - len(holdings))
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

        # NAV
        if holdings:
            h_codes = list(holdings.keys())
            h_shares = np.array([holdings[c]['shares'] for c in h_codes])
            p = pd_.reindex(h_codes).fillna(0).values
            nav = cash + (h_shares * p).sum()
        else:
            nav = cash
        nav_list.append(nav)

    nav_s = pd.Series(nav_list)
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

        tret, tdd, tsh, tlen = run_window(win_close, win_open, train_days, fold)

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
    mode = "v28+Kronos" if KRONOS_ENABLED else "v28框架(=v27)"
    print(f"{mode} WF 汇总 ({len(df)} folds)")
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
    print("v28 Kronos AI 增强选股 Walk-Forward 验证")
    print("=" * 60)
    print(f"  Close panel: {close_panel.shape}")
    print(f"  WF: train=252, test=126, step=63")
    print(f"  Kronos: {'开启' if KRONOS_ENABLED else '关闭(验证框架一致性)'}")
    print()

    t0 = time.time()
    walk_forward(train_days=252, test_days=126, step_days=63)
    print(f"\n  总耗时: {time.time()-t0:.1f}s")
