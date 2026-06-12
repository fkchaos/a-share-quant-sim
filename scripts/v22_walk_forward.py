#!/usr/bin/env python3
"""
v22_walk_forward — v22 策略 Walk-Forward 验证
"""

import sys, os
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from core.db import load_panel_from_db
from core.factors import calc_factors_panel


def run_v22_for_panel(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel, cfg):
    """对给定面板跑 v22 回测"""
    returns = close_panel.pct_change()
    eps = 1e-10

    # 计算因子
    factors = {}
    factors['mom_5'] = close_panel.pct_change(5)
    prev_close = close_panel.shift(1)
    factors['gap_ratio'] = (open_panel - prev_close) / (prev_close + eps) if open_panel is not None else returns * 0
    avg_amount = amount_panel.rolling(20).mean()
    factors['illiquidity'] = 1.0 / (avg_amount / 1e4 + eps)
    ma20 = close_panel.rolling(20).mean()
    std20 = close_panel.rolling(20).std()
    factors['boll_width_20'] = (4 * std20) / (ma20 + eps)
    factors['vol_20'] = returns.rolling(20).std()
    factors['amplitude'] = (high_panel - low_panel) / (close_panel + eps)

    cash = cfg['initial_capital']
    holdings = {}
    nav_list = []

    for i, date in enumerate(close_panel.index):
        if i < 30:
            nav_list.append(cash); continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1]); continue

        pd_ = close_panel.loc[date]
        od = open_panel.loc[date] if open_panel is not None else pd_

        for c in holdings:
            holdings[c]['hold_days'] = holdings[c].get('hold_days', 0) + 1

        to_sell = []
        for c, h in holdings.items():
            if c not in pd_.index: continue
            cp = pd_[c]
            if pd.isna(cp) or cp <= 0: continue
            pnl = (cp - h['cost']) / h['cost']
            if pnl <= cfg['stop_loss']: to_sell.append((c, 'SL', pnl)); continue
            if pnl >= cfg['stop_profit']: to_sell.append((c, 'TP', pnl)); continue
            hd = h.get('hold_days', 0)
            if hd >= cfg['hold_days_max']: to_sell.append((c, 'TO', pnl))

        sold = set()
        for c, reason, pnl in to_sell:
            if c not in pd_.index: continue
            sp = pd_[c]
            if pd.isna(sp) or sp <= 0: continue
            h = holdings[c]
            sv = h['shares'] * sp * (1 - cfg['commission_rate'] - cfg['stamp_tax'] - cfg['slippage_rate'])
            cash += sv; sold.add(c)
        for c in sold: holdings.pop(c, None)

        # 选股
        if date not in factors['mom_5'].index:
            nav_list.append(cash); continue

        mom_5 = factors['mom_5'].loc[date].dropna()
        scores = {}
        for code in mom_5.index:
            score = 0.0
            m = mom_5[code]
            if m > cfg.get('mom_threshold', 0.02):
                score += m * 100
                if 'gap_ratio' in factors and date in factors['gap_ratio'].index:
                    gr = factors['gap_ratio'].loc[date, code] if code in factors['gap_ratio'].columns else 0
                    if not pd.isna(gr) and gr > 0.02: score += 0.5
                if 'illiquidity' in factors and date in factors['illiquidity'].index:
                    illiq = factors['illiquidity'].loc[date, code] if code in factors['illiquidity'].columns else 0
                    if not pd.isna(illiq) and illiq > 0: score += 0.8
                if 'boll_width_20' in factors and date in factors['boll_width_20'].index:
                    bw = factors['boll_width_20'].loc[date, code] if code in factors['boll_width_20'].columns else 0
                    if not pd.isna(bw) and bw > 1.2: score += 0.3
            if score > 0: scores[code] = score

        if holdings:
            scores = {c: s for c, s in scores.items() if c not in holdings}

        cands = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)[:cfg['max_holdings']]

        if cands and cash > cfg['initial_capital'] * 0.1 and len(holdings) < cfg['max_holdings']:
            avail = cash - cfg['initial_capital'] * 0.1
            n = min(len(cands), cfg['max_daily_buy'], cfg['max_holdings'] - len(holdings))
            per = min(avail / n, cfg['initial_capital'] * cfg['max_position'])
            bought = 0
            for c in cands[:cfg['max_daily_buy']]:
                if bought >= n: break
                bp = od[c] if c in od.index else pd_[c]
                if pd.isna(bp) or bp <= 0: continue
                adj = bp * (1 + cfg['commission_rate'] + cfg['slippage_rate'])
                sh = int(per / adj / 100) * 100
                if sh <= 0: continue
                cost = sh * adj
                if cost > cash: continue
                cash -= cost
                holdings[c] = {'shares': sh, 'cost': bp, 'hold_days': 0}
                bought += 1

        nav = cash
        for c, h in holdings.items():
            if c in pd_.index:
                cp = pd_[c]
                if not pd.isna(cp) and cp > 0: nav += h['shares'] * cp
        nav_list.append(nav)

    nav_s = pd.Series(nav_list)
    total = nav_s.iloc[-1] / cfg['initial_capital'] - 1
    days = len(nav_list) - 30
    annual = (1 + total) ** (365 / max(days, 1)) - 1
    ret = nav_s.pct_change().dropna()
    sharpe = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0
    max_dd = ((nav_s.cummax() - nav_s) / nav_s.cummax()).max()

    return annual, sharpe, max_dd, total


def walk_forward_v22(train_days=252, test_days=63, step_days=63):
    """Walk-Forward 验证"""
    print("=" * 60)
    print("v22 Walk-Forward 验证")
    print("=" * 60)

    tpl, _ = load_panel_from_db('2021-01-01', '2026-05-31', need_open=True, need_hl=True)
    close_panel = tpl[0]
    total_days = close_panel.shape[0]

    cfg = {
        'initial_capital': 200000,
        'max_holdings': 8,
        'max_daily_buy': 6,
        'max_position': 0.20,
        'hold_days_max': 5,
        'stop_loss': -0.015,
        'stop_profit': 0.03,
        'commission_rate': 0.0003,
        'stamp_tax': 0.001,
        'slippage_rate': 0.002,
        'mom_threshold': 0.02,
    }

    fold_results = []
    fold = 0
    start_idx = 0

    while start_idx + train_days + test_days < total_days:
        end_idx = start_idx + train_days + test_days
        train_end = start_idx + train_days

        # 用训练期+测试期的数据（因子需要在完整窗口上计算）
        window_close = close_panel.iloc[start_idx:end_idx]
        window_vol = tpl[1].iloc[start_idx:end_idx]
        window_amt = tpl[2].iloc[start_idx:end_idx]
        window_open = tpl[3].iloc[start_idx:end_idx] if tpl[3] is not None else None
        window_high = tpl[4].iloc[start_idx:end_idx]
        window_low = tpl[5].iloc[start_idx:end_idx]

        print("Fold %d: [%d:%d] train=%d test=%d" % (fold, start_idx, end_idx, train_days, test_days))

        ar, sh, mdd, total = run_v22_for_panel(
            window_close, window_vol, window_amt, window_high, window_low, window_open, cfg
        )

        fold_results.append({
            'fold': fold,
            'annual': ar, 'sharpe': sh, 'max_dd': mdd, 'total': total
        })
        print("  -> 年化=%.2f%% 夏普=%.3f 回撤=%.2f%%" % (ar*100, sh, mdd*100))

        start_idx += step_days
        fold += 1

    # 汇总
    results_df = pd.DataFrame(fold_results)
    print("\n" + "=" * 60)
    print("WF 汇总 (%d folds)" % len(results_df))
    print("=" * 60)
    print("  平均年化: %.2f%%" % (results_df['annual'].mean() * 100))
    print("  平均夏普: %.3f" % results_df['sharpe'].mean())
    print("  平均回撤: %.2f%%" % (results_df['max_dd'].mean() * 100))
    print("  正收益 fold: %d/%d (%.0f%%)" % (
        (results_df['annual'] > 0).sum(), len(results_df),
        (results_df['annual'] > 0).mean() * 100))

    return results_df


if __name__ == "__main__":
    walk_forward_v22()
