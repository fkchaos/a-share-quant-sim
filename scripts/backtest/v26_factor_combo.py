#!/usr/bin/env python3
"""
v26_factor_combo — 因子组合优化
========================================

基于 IC 分析结果的最优因子组合：
- mom_5: IR=0.019（动量因子，方向为正）
- illiquidity: IR=0.275（最强因子）
- gap_ratio: IR=0.162（第二强）
- boll_width: IR=0.132（第三）

组合方法：
1. 等权组合：zscore(mom) + zscore(illiq) + zscore(gap) + zscore(boll)
2. IC 加权组合：用 IC 值作为权重
3. 分层组合：先按 illiquidity 分层，再在层内按动量排序
"""

import sys, os
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from core.db import load_panel_from_db


def calc_factor_combo(close_panel, volume_panel, amount_panel, open_panel,
                       high_panel, low_panel):
    """计算因子组合得分"""
    eps = 1e-10
    
    # 原始因子
    mom_5 = close_panel.pct_change(5)
    prev_close = close_panel.shift(1)
    gap_ratio = (open_panel - prev_close) / (prev_close + eps)
    avg_amount = amount_panel.rolling(20).mean()
    illiq = 1.0 / (avg_amount / 1e4 + eps)
    ma20 = close_panel.rolling(20).mean()
    std20 = close_panel.rolling(20).std()
    boll_w = (4 * std20) / (ma20 + eps)
    vol_20 = close_panel.pct_change().rolling(20).std()
    
    # 截面标准化
    def zscore(df):
        return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1) + 1e-10, axis=0)
    
    z_mom = zscore(mom_5)
    z_illiq = zscore(illiq)
    z_gap = zscore(gap_ratio)
    z_boll = zscore(boll_w)
    z_vol = zscore(vol_20)
    
    # 组合1: 等权
    combo_equal = (z_mom.fillna(0) + z_illiq.fillna(0) + 
                   z_gap.fillna(0) + z_boll.fillna(0)) / 4
    
    # 组合2: IC 加权（IC 值作为权重）
    # mom_5 IR=0.019, illiq IR=0.275, gap IR=0.162, boll IR=0.132
    w_mom = 0.019
    w_illiq = 0.275
    w_gap = 0.162
    w_boll = 0.132
    w_total = w_mom + w_illiq + w_gap + w_boll
    
    combo_ic = (
        z_mom.fillna(0) * w_mom +
        z_illiq.fillna(0) * w_illiq +
        z_gap.fillna(0) * w_gap +
        z_boll.fillna(0) * w_boll
    ) / w_total
    
    # 组合3: 仅用最强两个（illiq + gap）
    combo_top2 = (z_illiq.fillna(0) + z_gap.fillna(0)) / 2
    
    # 组合4: 动量+illiq（选股用小市值+动量）
    combo_mom_illiq = (z_mom.fillna(0) + z_illiq.fillna(0)) / 2
    
    return {
        'combo_equal': combo_equal,
        'combo_ic': combo_ic,
        'combo_top2': combo_top2,
        'combo_mom_illiq': combo_mom_illiq,
        'mom_5': mom_5,  # 基线
    }


def run_backtest(close_panel, volume_panel, amount_panel, open_panel,
                  high_panel, low_panel, factors, score_key, cfg, label=""):
    """通用回测"""
    eps = 1e-10
    score_panel = factors[score_key]
    gap_ratio = (open_panel - close_panel.shift(1)) / (close_panel.shift(1) + eps)
    avg_amount = amount_panel.rolling(20).mean()
    illiq = 1.0 / (avg_amount / 1e4 + eps)

    cash = cfg['initial_capital']
    holdings = {}
    nav_list = []
    sell_reasons = {'SL': 0, 'TP': 0, 'TO': 0}

    for i, date in enumerate(close_panel.index):
        if i < 30:
            nav_list.append(cash); continue

        pd_ = close_panel.loc[date]
        od = open_panel.loc[date]
        for c in holdings:
            holdings[c]['hold_days'] = holdings[c].get('hold_days', 0) + 1

        to_sell = []
        for c, h in holdings.items():
            if c not in pd_.index: continue
            cp = pd_[c]
            if pd.isna(cp) or cp <= 0: continue
            pnl = (cp - h['cost']) / h['cost']
            if pnl <= cfg['stop_loss']: to_sell.append((c, 'SL')); continue
            if pnl >= cfg['stop_profit']: to_sell.append((c, 'TP')); continue
            if h.get('hold_days', 0) >= cfg['hold_days_max']: to_sell.append((c, 'TO'))

        for c, reason in to_sell:
            if c not in pd_.index: continue
            sp = pd_[c]
            if pd.isna(sp) or sp <= 0: continue
            h = holdings[c]
            sv = h['shares'] * sp * (1 - cfg['commission_rate'] - cfg['stamp_tax'] - cfg['slippage_rate'])
            cash += sv
            sell_reasons[reason] = sell_reasons.get(reason, 0) + 1
        for c, _ in to_sell: holdings.pop(c, None)

        if date not in score_panel.index:
            nav_list.append(nav_list[-1] if nav_list else cash); continue

        scores = score_panel.loc[date].dropna()
        # 只选正分数的
        scores = scores[scores > 0]
        cands = sorted(scores.index, key=lambda c: scores[c], reverse=True)[:cfg['max_holdings']]

        if holdings:
            cands = [c for c in cands if c not in holdings]

        if cands and cash > cfg['initial_capital'] * 0.1 and len(holdings) < cfg['max_holdings']:
            avail = cash - cfg['initial_capital'] * 0.1
            nb = min(len(cands), cfg['max_daily_buy'], cfg['max_holdings'] - len(holdings))
            if nb > 0:
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

    nav_s = pd.Series(nav_list)
    daily_ret = nav_s.pct_change().dropna()
    total = nav_s.iloc[-1] / nav_s.iloc[0] - 1
    annual = (1 + total) ** (365 / max(len(nav_list) - 30, 1)) - 1
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    max_dd = ((nav_s.cummax() - nav_s) / nav_s.cummax()).max()
    total_sells = sum(sell_reasons.values())

    return {
        'label': label, 'annual': annual, 'sharpe': sharpe,
        'max_dd': max_dd, 'total': total,
        'TP': sell_reasons.get('TP', 0),
        'SL': sell_reasons.get('SL', 0),
        'win_rate': sell_reasons.get('TP', 0) / max(total_sells, 1),
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="因子组合优化回测")
    parser.add_argument("--start", type=str, default="2022-01-01")
    parser.add_argument("--end", type=str, default="2026-05-31")
    parser.add_argument("--capital", type=int, default=100000)
    args = parser.parse_args()

    t_start = time.time()
    print("=" * 60)
    print("因子组合优化回测")
    print("=" * 60)

    tpl, _ = load_panel_from_db(args.start, args.end, need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]

    print("数据: %d 天 x %d 只" % (close_panel.shape[0], close_panel.shape[1]))

    print("\n计算因子组合...")
    t0 = time.time()
    factors = calc_factor_combo(close_panel, volume_panel, amount_panel,
                                 open_panel, high_panel, low_panel)
    print("  耗时: %.1fs" % (time.time() - t0))

    cfg = {
        'initial_capital': args.capital,
        'max_holdings': 8,
        'max_daily_buy': 6,
        'max_position': 0.20,
        'hold_days_max': 5,
        'stop_loss': -0.015,
        'stop_profit': 0.03,
        'commission_rate': 0.0003,
        'stamp_tax': 0.001,
        'slippage_rate': 0.002,
    }

    variants = [
        ('mom_5', 'mom_5', '原始动量 (基线)'),
        ('combo_equal', 'combo_equal', '等权4因子'),
        ('combo_ic', 'combo_ic', 'IC加权4因子'),
        ('combo_top2', 'combo_top2', '最强2因子(illiq+gap)'),
        ('combo_mom_illiq', 'combo_mom_illiq', '动量+illiquidity'),
    ]

    results = []
    print("\n" + "=" * 60)
    print("回测结果")
    print("=" * 60)
    print("%-22s %10s %8s %8s %8s" % ('策略', '年化%', '夏普', '回撤%', '胜率'))
    print("-" * 62)

    for key, score_key, label in variants:
        result = run_backtest(close_panel, volume_panel, amount_panel, open_panel,
                               high_panel, low_panel, factors, score_key, cfg, label)
        results.append(result)
        print("%-22s %10.2f %8.3f %8.2f %8.1f" % (
            label, result['annual']*100, result['sharpe'],
            result['max_dd']*100, result['win_rate']*100))

    print("-" * 62)
    print("总耗时: %.1fs" % (time.time() - t_start))
    print("=" * 60)

    return results


if __name__ == "__main__":
    main()
