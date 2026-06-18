#!/usr/bin/env python3
"""
strategy_comparison — 全策略对比汇总
========================================
汇总所有已测试策略的表现
"""

import sys, os
import numpy as np
import pandas as pd

from core.db import load_panel_from_db

def run_all_strategies(close_panel, volume_panel, amount_panel, open_panel,
                       high_panel, low_panel, cfg):
    """运行所有策略变体并对比"""
    eps = 1e-10
    
    # 预计算所有因子
    mom_5 = close_panel.pct_change(5)
    prev_close = close_panel.shift(1)
    gap_ratio = (open_panel - prev_close) / (prev_close + eps)
    avg_amount = amount_panel.rolling(20).mean()
    illiq = 1.0 / (avg_amount / 1e4 + eps)
    ma20 = close_panel.rolling(20).mean()
    std20 = close_panel.rolling(20).std()
    boll_w = (4 * std20) / (ma20 + eps)
    vol_20 = close_panel.pct_change().rolling(20).std()
    
    # 因子组合
    def zscore(df):
        return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1) + 1e-10, axis=0)
    
    z_mom = zscore(mom_5)
    z_illiq = zscore(illiq)
    z_gap = zscore(gap_ratio)
    z_boll = zscore(boll_w)
    
    combo_equal = (z_mom.fillna(0) + z_illiq.fillna(0) + z_gap.fillNA(0) + z_boll.fillna(0)) / 4
    combo_top2 = (z_illiq.fillna(0) + z_gap.fillna(0)) / 2

    strategies = {
        'v22_raw': {'score': mom_5, 'threshold': 0.02, 'use_zscore': False},
        'vw': {'score': mom_5 * (1 + volume_panel.pct_change(5).fillna(0)), 'threshold': 0.02, 'use_zscore': False},
        'combo_equal': {'score': combo_equal, 'threshold': 0, 'use_zscore': True},
        'combo_top2': {'score': combo_top2, 'threshold': 0, 'use_zscore': True},
    }
    
    results = {}
    
    for name, strat in strategies.items():
        cash = cfg['initial_capital']
        holdings = {}
        nav_list = []
        sell_reasons = {'SL': 0, 'TP': 0, 'TO': 0}
        
        for i, date in enumerate(close_panel.index):
            if i < 30: nav_list.append(cash); continue
            pd2 = close_panel.loc[date]
            od = open_panel.loc[date]
            for c in holdings: holdings[c]['hold_days'] = holdings[c].get('hold_days',0)+1
            
            to_sell = []
            for c, h in holdings.items():
                if c not in pd2.index: continue
                cp = pd2[c]
                if pd.isna(cp) or cp <= 0: continue
                pnl = (cp - h['cost']) / h['cost']
                if pnl <= cfg['stop_loss']: to_sell.append((c,'SL')); continue
                if pnl >= cfg['stop_profit']: to_sell.append((c,'TP')); continue
                if h.get('hold_days',0) >= cfg['hold_days_max']: to_sell.append((c,'TO'))
            
            for c, reason in to_sell:
                if c not in pd2.index: continue
                sp = pd2[c]
                if pd.isna(sp) or sp <= 0: continue
                h = holdings[c]
                sv = h['shares'] * sp * (1 - cfg['commission_rate'] - cfg['stamp_tax'] - cfg['slippage_rate'])
                cash += sv; sell_reasons[reason] += 1
            for c, _ in to_sell: holdings.pop(c, None)
            
            if date not in strat['score'].index:
                nav_list.append(nav_list[-1] if nav_list else cash); continue
            
            s = strat['score'].loc[date].dropna()
            if strat['use_zscore']:
                s = s[s > 0]
                cands = sorted(s.index, key=lambda c: s[c], reverse=True)[:cfg['max_holdings']]
            else:
                s = s[s > strat['threshold']]
                cands = sorted(s.index, key=lambda c: s[c], reverse=True)[:cfg['max_holdings']]
            
            if holdings:
                cands = [c for c in cands if c not in holdings]
            
            if cands and cash > cfg['initial_capital'] * 0.1 and len(holding) < cfg['max_holdings']:
                avail = cash - cfg['initial_capital'] * 0.1
                nb = min(len(cands), cfg['max_daily_buy'], cfg['max_holdings'] - len(holdings))
                if nb > 0:
                    per = min(avail / nb, cfg['initial_capital'] * cfg['max_position'])
                    for c in cands[:cfg['max_daily_buy']]:
                        if len(holdings) >= cfg['max_holdings'] or nb <= 0: break
                        bp = od[c] if c in od.index else pd2[c]
                        if pd.isna(bp) or bp <= 0: continue
                        adj = bp * (1 + cfg['commission_rate'] + cfg['slippage_rate'])
                        sh = int(per / adj / 100) * 100
                        if sh <= 0: continue
                        cost = sh * adj
                        if cost > cash: continue
                        cash -= cost
                        holdings[c] = {'shares': sh, 'cost': bp, 'hold_days': 0}
                        nb -= 1
            
            nav = cash + sum(h['shares'] * pd2[c] for c, h in holdings.items() 
                           if c in pd2.index and not pd.isna(pd2[c]) and pd2[c] > 0)
            nav_list.append(nav)
        
        nav_s = pd.Series(nav_list)
        daily_ret = nav_s.pct_change().dropna()
        total = nav_s.iloc[-1] / nav_s.iloc[0] - 1
        annual = (1 + total) ** (365 / max(len(nav_list) - 30, 1)) - 1
        sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
        max_dd = ((nav_s.cummax() - nav_s) / nav_s.cummax()).max()
        total_sells = sum(sell_reasons.values())
        
        results[name] = {
            'annual': annual, 'sharpe': sharpe, 'max_dd': max_dd,
            'total': total, 'win_rate': sell_reasons['TP'] / max(total_sells, 1),
            'TP': sell_reasons['TP'], 'SL': sell_reasons['SL'],
        }
    
    return results

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=str, default="2022-01-01")
    parser.add_argument("--end", type=str, default="2026-05-31")
    parser.add_argument("--capital", type=int, default=100000)
    args = parser.parse_args()

    tpl, _ = load_panel_from_db(args.start, args.end, need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]

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

    results = run_all_strategies(close_panel, volume_panel, amount_panel,
                                  open_panel, high_panel, low_panel, cfg)

    print("\n全策略对比 (%s ~ %s)" % (args.start, args.end))
    print("=" * 70)
    print("%-18s %10s %8s %8s %8s %8s" % ('策略', '年化%', '夏普', '回撤%', '胜率', 'TP数'))
    print("-" * 70)
    
    for name, r in sorted(results.items(), key=lambda x: x[1]['sharpe'], reverse=True):
        print("%-18s %10.2f %8.3f %8.2f %8.1f %8d" % (
            name, r['annual']*100, r['sharpe'], r['max_dd']*100,
            r['win_rate']*100, r['TP']))
    print("=" * 70)

if __name__ == "__main__":
    main()
