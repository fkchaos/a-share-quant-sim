#!/usr/bin/env python3
"""v13 vs v22 对比分析"""
import sys, os
import numpy as np
import pandas as pd

from core.db import load_panel_from_db

def run_strategy(name, start_date, end_date, select_fn, cfg_dict):
    """通用回测函数"""
    tpl, _ = load_panel_from_db(start_date, end_date, need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel = tpl[3]
    high_panel = tpl[4]
    low_panel = tpl[5]

    from core.factors import calc_factors_panel
    factors = calc_factors_panel(close_panel, volume_panel, amount_panel,
                                  open_panel=open_panel, high_panel=high_panel, low_panel=low_panel)

    cash = cfg_dict['initial_capital']
    holdings = {}
    nav_list = []
    dates = close_panel.index[close_panel.index >= pd.Timestamp(start_date)]

    for i, date in enumerate(dates):
        if i < 30:
            nav_list.append(cash); continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1]); continue

        pd_ = close_panel.loc[date]
        od = open_panel.loc[date] if open_panel is not None else pd_

        for c in holdings:
            holdings[c]['hold_days'] = holdings[c].get('hold_days', 0) + 1

        # 风控
        to_sell = []
        for c, h in holdings.items():
            if c not in pd_.index: continue
            cp = pd_[c]
            if pd.isna(cp) or cp <= 0: continue
            pnl = (cp - h['cost']) / h['cost']
            hd = h.get('hold_days', 0)
            if pnl <= cfg_dict['stop_loss']: to_sell.append((c, 'SL', pnl)); continue
            if pnl >= cfg_dict['stop_profit']: to_sell.append((c, 'TP', pnl)); continue
            if hd >= cfg_dict['hold_days_max']: to_sell.append((c, 'TO', pnl))

        sold = set()
        for c, reason, pnl in to_sell:
            if c not in pd_.index: continue
            sp = pd_[c]
            if pd.isna(sp) or sp <= 0: continue
            if i > 0:
                pc = close_panel.iloc[i-1].get(c)
                if pc and not pd.isna(pc) and pc > 0 and sp <= pc * 0.90 * 1.01:
                    holdings[c]['hold_days'] = max(0, holdings[c].get('hold_days', 0) - 1)
                    continue
            h = holdings[c]
            sv = h['shares'] * sp * (1 - cfg_dict['commission_rate'] - cfg_dict['stamp_tax'] - cfg_dict['slippage_rate'])
            cash += sv; sold.add(c)
        for c in sold: holdings.pop(c, None)

        cands = select_fn(factors, date, close_panel, volume_panel, amount_panel, holdings, cfg_dict)
        if cands and cash > cfg_dict['initial_capital'] * 0.1 and len(holdings) < cfg_dict['max_holdings']:
            avail = cash - cfg_dict['initial_capital'] * 0.1
            n = min(len(cands), cfg_dict['max_daily_buy'], cfg_dict['max_holdings'] - len(holdings))
            per = min(avail / n, cfg_dict['initial_capital'] * cfg_dict['max_position'])
            bought = 0
            for c in cands[:cfg_dict['max_daily_buy']]:
                if bought >= n: break
                bp = od[c] if c in od.index else pd_[c]
                if pd.isna(bp) or bp <= 0: continue
                if i > 0:
                    pc = close_panel.iloc[i-1].get(c)
                    if pc and not pd.isna(pc) and pc > 0 and bp >= pc * 1.10 * 0.99: continue
                adj = bp * (1 + cfg_dict['commission_rate'] + cfg_dict['slippage_rate'])
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
    ret = nav_s.pct_change().dropna()
    total = nav_s.iloc[-1] / cfg_dict['initial_capital'] - 1
    days = len(nav_list) - 30
    ar = (1 + total) ** (365 / max(days, 1)) - 1
    sh = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0
    mdd = ((nav_s.cummax() - nav_s) / nav_s.cummax()).max()
    return ar, sh, mdd, nav_s

def v13_select(factors, date, cp, vp, ap, holdings, cfg):
    from scripts.v13_small_mid_short import select_stocks
    rev_threshold = cfg.get('rev_threshold', -0.02)
    max_holdings = cfg.get('max_holdings', 8)
    return select_stocks(factors, date, cp, vp, ap, holdings)[:max_holdings]

def v22_select(factors, date, cp, vp, ap, holdings, cfg):
    from scripts.v22_ai_factor import calc_v22_factors, select_stocks_v22
    v22_cfg = cfg.get('v22_cfg', type('C', (), {'mom_threshold': 0.02, 'max_holdings': 8})())
    return select_stocks_v22(factors, date, cp, vp, ap, holdings, v22_cfg)

if __name__ == "__main__":
    start, end = '2022-01-01', '2026-05-31'

    base_cfg = {
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
    }

    print("跑 v13...")
    v13_result = run_strategy('v13', start, end, v13_select, base_cfg)
    print("跑 v22...")
    v22_result = run_strategy('v22', start, end, v22_select, {**base_cfg, 'v22_cfg': type('C', (), {'mom_threshold': 0.02, 'max_holdings': 8})()})

    print("\n" + "=" * 50)
    print("对比结果 (%s ~ %s)" % (start, end))
    print("=" * 50)
    print("v13: 年化=%.2f%%  夏普=%.3f  回撤=%.2f%%" % (v13_result[0]*100, v13_result[1], v13_result[2]*100))
    print("v22: 年化=%.2f%%  夏普=%.3f  回撤=%.2f%%" % (v22_result[0]*100, v22_result[1], v22_result[2]*100))
