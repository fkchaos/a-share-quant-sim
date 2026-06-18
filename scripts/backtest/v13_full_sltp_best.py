#!/usr/bin/env python3
"""v13 全量回测 — SL=-1.5% TP=3% hold=5"""
import sys, os, time
import numpy as np
import pandas as pd

from v13_small_mid_short import (
    V13Config, load_small_cap_panel, calc_small_cap_factors, select_stocks
)

# 覆盖参数
V13Config.stop_loss = -0.015
V13Config.stop_profit = 0.03
V13Config.hold_days_max = 5

close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = load_small_cap_panel()
factors = calc_small_cap_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)

initial_capital = V13Config.initial_capital
cash = initial_capital
holdings = {}
nav_list = []
trade_log = []
dates = close_panel.index

t0 = time.time()
for i, date in enumerate(dates):
    if i < 20:
        nav_list.append(initial_capital)
        continue
    if date not in close_panel.index:
        nav_list.append(nav_list[-1] if nav_list else initial_capital)
        continue

    price_data = close_panel.loc[date]
    open_data = open_panel.loc[date] if open_panel is not None else price_data

    avg_amount = amount_panel.rolling(20).mean() / 1e4
    if date in avg_amount.index:
        day_am = avg_amount.loc[date]
        liq = set(day_am[(day_am > V13Config.min_liquidity) & (day_am < V13Config.max_liquidity)].dropna().index)
    else:
        liq = set(close_panel.columns)

    for code in holdings:
        holdings[code]['hold_days'] += 1

    to_sell = []
    for code, h in holdings.items():
        if code not in price_data.index: continue
        cp = price_data[code]
        if pd.isna(cp) or cp <= 0: continue
        pnl = (cp - h['cost']) / h['cost']
        if pnl <= V13Config.stop_loss: to_sell.append((code, 'SL', pnl))
        elif pnl >= V13Config.stop_profit: to_sell.append((code, 'TP', pnl))
        elif h['hold_days'] >= V13Config.hold_days_max: to_sell.append((code, 'TO', pnl))

    sold = set()
    for code, reason, pnl in to_sell:
        if code in price_data.index:
            sp = price_data[code]
            if pd.isna(sp) or sp <= 0: continue
            if i > 0:
                pc = close_panel.iloc[i-1].get(code)
                if pc and not pd.isna(pc) and pc > 0 and sp <= pc * 0.90 * 1.01:
                    holdings[code]['hold_days'] = max(0, holdings[code]['hold_days'] - 1)
                    continue
            h = holdings[code]
            sv = h['shares'] * sp * (1 - V13Config.commission_rate - V13Config.stamp_tax - V13Config.slippage_rate)
            cash += sv
            trade_log.append({'date': str(date.date()), 'code': code, 'action': 'sell',
                              'reason': reason, 'pnl_pct': round(pnl*100, 2), 'value': round(sv, 2)})
            sold.add(code)
    for c in sold: holdings.pop(c, None)

    candidates = select_stocks(factors, date, close_panel, volume_panel, amount_panel, holdings)
    candidates = [c for c in candidates if c in liq]

    if candidates and cash > initial_capital * 0.1 and len(holdings) < V13Config.max_holdings:
        ac = cash - initial_capital * 0.1
        ps = min(ac / min(len(candidates), V13Config.max_daily_buy), initial_capital * V13Config.max_position)
        for code in candidates[:V13Config.max_daily_buy]:
            if code not in price_data.index: continue
            bp = open_data.get(code, price_data.get(code))
            if bp is None or pd.isna(bp) or bp <= 0: continue
            adj = bp * (1 + V13Config.commission_rate + V13Config.slippage_rate)
            shares = int(ps / adj / 100) * 100
            if shares <= 0: continue
            cost = shares * adj
            if cost > cash: continue
            cash -= cost
            holdings[code] = {'shares': shares, 'cost': bp, 'hold_days': 0}
            trade_log.append({'date': str(date.date()), 'code': code, 'action': 'buy',
                              'price': round(bp, 2), 'shares': shares, 'value': round(cost, 2)})

    pv = cash
    for code, h in holdings.items():
        if code in price_data.index:
            p = price_data[code]
            if not pd.isna(p) and p > 0: pv += h['shares'] * p
    nav_list.append(pv)

elapsed = time.time() - t0

nav = pd.Series(nav_list, index=dates[:len(nav_list)])
total_ret = (nav.iloc[-1]/nav.iloc[0] - 1) * 100
n_days = len(nav)
annual = ((nav.iloc[-1]/nav.iloc[0]) ** (252/max(n_days,1)) - 1) * 100
dr = nav.pct_change().dropna()
sharpe = dr.mean() / (dr.std()+1e-10) * np.sqrt(252)
dd = (nav - nav.cummax()) / nav.cummax()
max_dd = dd.min() * 100
sells = [t for t in trade_log if t['action'] == 'sell']
wr = sum(1 for t in sells if t['pnl_pct']>0) / max(len(sells),1) * 100
n_sl = sum(1 for t in sells if t['reason']=='SL')
n_tp = sum(1 for t in sells if t['reason']=='TP')
n_to = sum(1 for t in sells if 'TO' in t['reason'])
total_s = max(len(sells), 1)

print(f"\n{'='*60}")
print(f"v13 全量回测结果（SL=-1.5% TP=3% hold=5）")
print(f"{'='*60}")
print(f"回测区间: {dates[0].date()} ~ {dates[-1].date()}")
print(f"初始资金: {initial_capital:,.0f}")
print(f"最终资金: {nav.iloc[-1]:,.0f}")
print(f"总收益率: {total_ret:.2f}%")
print(f"年化收益: {annual:.2f}%")
print(f"夏普比率: {sharpe:.3f}")
print(f"最大回撤: {max_dd:.2f}%")
print(f"交易次数: {len(sells)}")
print(f"胜率: {wr:.1f}%")
print(f"TP/SL/TO: {n_tp/total_s*100:.0f}%/{n_sl/total_s*100:.0f}%/{n_to/total_s*100:.0f}%")
print(f"耗时: {elapsed:.1f}s")
