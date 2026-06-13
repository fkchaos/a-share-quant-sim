#!/usr/bin/env python3
"""
v22_walk_forward — v22 策略 Walk-Forward 验证
用测试期总收益（非年化）避免短窗口放大问题
v13 同框架对比
"""

import sys, os
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from core.db import load_panel_from_db


def run_v22_on_window(close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel, cfg):
    """在给定窗口上跑 v22 回测（窗口 = train + test）"""
    eps = 1e-10

    # 因子在整个窗口上计算（rolling只用过去，无未来泄露）
    mom_5 = close_panel.pct_change(5)
    prev_close = close_panel.shift(1)
    gap_ratio = (open_panel - prev_close) / (prev_close + eps)
    avg_amount = amount_panel.rolling(20).mean()
    illiq = 1.0 / (avg_amount / 1e4 + eps)
    ma20 = close_panel.rolling(20).mean()
    std20 = close_panel.rolling(20).std()
    boll_w = (4 * std20) / (ma20 + eps)

    n = close_panel.shape[0]
    train_days = cfg.get('train_days', 252)

    cash = cfg['initial_capital']
    holdings = {}
    nav_list = []

    for i, date in enumerate(close_panel.index):
        if i < 30:
            nav_list.append(cash); continue

        pd_ = close_panel.loc[date]
        od = open_panel.loc[date]

        for c in holdings:
            holdings[c]['hold_days'] = holdings[c].get('hold_days', 0) + 1

        # 卖出
        to_sell = []
        for c, h in holdings.items():
            if c not in pd_.index: continue
            cp = pd_[c]
            if pd.isna(cp) or cp <= 0: continue
            pnl = (cp - h['cost']) / h['cost']
            if pnl <= cfg['stop_loss']: to_sell.append(c); continue
            if pnl >= cfg['stop_profit']: to_sell.append(c); continue
            hd = h.get('hold_days', 0)
            if hd >= cfg['hold_days_max']: to_sell.append(c)

        sold = set()
        for c in to_sell:
            if c not in pd_.index: continue
            sp = pd_[c]
            if pd.isna(sp) or sp <= 0: continue
            h = holdings[c]
            sv = h['shares'] * sp * (1 - cfg['commission_rate'] - cfg['stamp_tax'] - cfg['slippage_rate'])
            cash += sv; sold.add(c)
        for c in sold: holdings.pop(c, None)

        # 选股
        if date not in mom_5.index:
            nav_list.append(cash); continue

        m5 = mom_5.loc[date].dropna()
        scores = {}
        for code in m5.index:
            score = 0.0
            m = m5[code]
            if m > cfg.get('mom_threshold', 0.02):
                score += m * 100
                if date in gap_ratio.index and code in gap_ratio.columns:
                    gr = gap_ratio.loc[date, code]
                    if not pd.isna(gr) and gr > 0.02: score += 0.5
                if date in illiq.index and code in illiq.columns:
                    il = illiq.loc[date, code]
                    if not pd.isna(il) and il > 0: score += 0.8
                if date in boll_w.index and code in boll_w.columns:
                    bw = boll_w.loc[date, code]
                    if not pd.isna(bw) and bw > 1.2: score += 0.3
            if score > 0: scores[code] = score

        if holdings:
            scores = {c: s for c, s in scores.items() if c not in holdings}

        cands = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)[:cfg['max_holdings']]

        if cands and cash > cfg['initial_capital'] * 0.1 and len(holdings) < cfg['max_holdings']:
            avail = cash - cfg['initial_capital'] * 0.1
            nb = min(len(cands), cfg['max_daily_buy'], cfg['max_holdings'] - len(holdings))
            per = min(avail / nb, cfg['initial_capital'] * cfg['max_position'])
            for c in cands[:cfg['max_daily_buy']]:
                if len(holdings) >= cfg['max_holdings'] or nb <= 0: break
                bp = od[c] if c in od.index else pd_[c]
                if pd.isna(bp) or bp <= 0: continue
                adj = bp * (1 + cfg['commission_rate'] + cfg['slippage_rate'])
                sh = int(per / adj / 100) * 100
                if sh <= 0: continue
                cost = sh * adj
                if cost > cash: continue
                cash -= cost
                holdings[c] = {'shares': sh, 'cost': bp, 'hold_days': 0}
                nb -= 1

        nav = cash
        for c, h in holdings.items():
            if c in pd_.index:
                cp = pd_[c]
                if not pd.isna(cp) and cp > 0: nav += h['shares'] * cp
        nav_list.append(nav)

    # 分割 train/test NAV
    nav_s = pd.Series(nav_list)
    train_nav = nav_s[:train_days]
    test_nav = nav_s[train_days:]

    if len(test_nav) == 0:
        return 0, 0, 0, 0, 0

    # 训练期
    train_ret = train_nav.iloc[-1] / train_nav.iloc[0] - 1 if train_nav.iloc[0] > 0 else 0
    train_dd = ((train_nav.cummax() - train_nav) / train_nav.cummax()).max()

    # 测试期
    test_ret = test_nav.iloc[-1] / test_nav.iloc[0] - 1 if test_nav.iloc[0] > 0 else 0
    test_dd = ((test_nav.cummax() - test_nav) / test_nav.cummax()).max()
    test_daily = test_nav.pct_change().dropna()
    test_sharpe = test_daily.mean() / test_daily.std() * np.sqrt(252) if test_daily.std() > 0 else 0

    return train_ret, train_dd, test_ret, test_dd, test_sharpe


def walk_forward_v22(train_days=252, test_days=126, step_days=63):
    """Walk-Forward: 训练期因子预热，测试期验证"""
    print("=" * 60)
    print("v22 Walk-Forward 验证 (train=%d, test=%d, step=%d)" % (train_days, test_days, step_days))
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
        'train_days': train_days,
    }

    fold_results = []
    fold = 0
    start_idx = 0

    while start_idx + train_days + test_days < total_days:
        end_idx = min(start_idx + train_days + test_days, total_days)

        win_close = close_panel.iloc[start_idx:end_idx]
        win_vol = tpl[1].iloc[start_idx:end_idx]
        win_amt = tpl[2].iloc[start_idx:end_idx]
        win_open = tpl[3].iloc[start_idx:end_idx]
        win_high = tpl[4].iloc[start_idx:end_idx]
        win_low = tpl[5].iloc[start_idx:end_idx]

        tr, tdd, tret, tdd2, tsh = run_v22_on_window(
            win_close, win_vol, win_amt, win_high, win_low, win_open, cfg
        )

        fold_results.append({
            'fold': fold,
            'train_ret': tr, 'train_dd': tdd,
            'test_ret': tret, 'test_dd': tdd2, 'test_sharpe': tsh,
            'test_days': test_days,
        })
        print("Fold %d | 训练: %.2f%% (DD=%.1f%%) | 测试: %.2f%% (DD=%.1f%%, Sharpe=%.2f)" % (
            fold, tr*100, tdd*100, tret*100, tdd2*100, tsh))

        start_idx += step_days
        fold += 1

    if not fold_results:
        print("数据不足，无法生成 fold")
        return pd.DataFrame()

    df = pd.DataFrame(fold_results)
    print("\n" + "=" * 60)
    print("v22 WF 汇总 (%d folds)" % len(df))
    print("=" * 60)
    print("  测试期平均收益率: %.2f%%" % (df['test_ret'].mean() * 100))
    print("  测试期平均夏普:   %.3f" % df['test_sharpe'].mean())
    print("  测试期平均回撤:   %.2f%%" % (df['test_dd'].mean() * 100))
    print("  正收益 fold:      %d/%d (%.0f%%)" % (
        (df['test_ret'] > 0).sum(), len(df),
        (df['test_ret'] > 0).mean() * 100))
    print("  年化(均值+):      ~%.1f%%" % (
        ((1 + df['test_ret'].mean()) ** (252.0 / test_days) - 1) * 100))

    return df


if __name__ == "__main__":
    walk_forward_v22(train_days=252, test_days=126, step_days=63)
