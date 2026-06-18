#!/usr/bin/env python3
"""v13 市值分层回测 — 测试不同市值分层的业绩差异"""
import sys, os, time
import numpy as np
import pandas as pd

sys.path.insert(0, '.')
sys.path.insert(0, 'scripts')

from v13_small_mid_short import (
    V13Config, load_small_cap_panel, calc_small_cap_factors, select_stocks
)

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"))


def run_v13_with_liquidity_cap(max_liquidity, start_date='2022-01-01', end_date='2026-05-31'):
    """用指定的流动性上限跑回测"""
    close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = \
        load_small_cap_panel(start_date=start_date, end_date=end_date)

    factors = calc_small_cap_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)

    initial_capital = V13Config.initial_capital
    cash = initial_capital
    holdings = {}
    nav_list = []
    trade_log = []
    dates = close_panel.index

    for i, date in enumerate(dates):
        if i < 20:
            nav_list.append(initial_capital)
            continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else initial_capital)
            continue

        price_data = close_panel.loc[date]
        open_data = open_panel.loc[date] if open_panel is not None else price_data

        # 动态流动性过滤
        avg_amount = amount_panel.rolling(20).mean() / 1e4
        if date in avg_amount.index:
            day_am = avg_amount.loc[date]
            liq_mask = (day_am > V13Config.min_liquidity) & (day_am < max_liquidity)
            liquid_stocks = set(day_am[liq_mask].dropna().index)
        else:
            liquid_stocks = set(close_panel.columns)

        for code in holdings:
            holdings[code]['hold_days'] += 1

        to_sell = []
        for code, h in holdings.items():
            if code not in price_data.index:
                continue
            cp = price_data[code]
            if pd.isna(cp) or cp <= 0:
                continue
            pnl = (cp - h['cost']) / h['cost']
            if pnl <= V13Config.stop_loss:
                to_sell.append((code, 'SL', pnl))
            elif pnl >= V13Config.stop_profit:
                to_sell.append((code, 'TP', pnl))
            elif h['hold_days'] >= V13Config.hold_days_max:
                to_sell.append((code, 'TO', pnl))

        sold = set()
        for code, reason, pnl in to_sell:
            if code in price_data.index:
                sp = price_data[code]
                if pd.isna(sp) or sp <= 0:
                    continue
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
        for c in sold:
            holdings.pop(c, None)

        # 选股（在流动性池内）
        if date in factors['rev_5'].index:
            rev_5 = factors['rev_5'].loc[date].dropna()
            vol_ratio = factors['vol_ratio'].loc[date].dropna()
            vol_shrink = factors['vol_shrink'].loc[date].dropna()
            range_ratio = factors['range_ratio'].loc[date].dropna()
            scores = {}
            for code in liquid_stocks:
                if code not in rev_5.index or code in holdings:
                    continue
                r = rev_5.get(code, 0)
                if r < V13Config.rev_threshold:
                    s = abs(r) * 100
                    vr = vol_ratio.get(code, 1.0)
                    if vr > V13Config.vol_ratio_threshold: s += 0.5
                    vs = vol_shrink.get(code, 1.0)
                    if vs < 0.7: s += 0.3
                    rr = range_ratio.get(code, 1.0)
                    if rr < 0.8: s += 0.2
                    if s > 0: scores[code] = s
            candidates = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)
        else:
            candidates = []

        if candidates and cash > initial_capital * 0.1 and len(holdings) < V13Config.max_holdings:
            ac = cash - initial_capital * 0.1
            ps = min(ac / min(len(candidates), V13Config.max_daily_buy), initial_capital * V13Config.max_position)
            for code in candidates[:V13Config.max_daily_buy]:
                if code not in price_data.index: continue
                bp = open_data.get(code, price_data.get(code))
                if bp is None or pd.isna(bp) or bp <= 0: continue
                if i > 0:
                    pc = close_panel.iloc[i-1].get(code)
                    if pc and not pd.isna(pc) and pc > 0 and bp >= pc * 1.10 * 0.99: continue
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

    # 计算选股率
    avg_amount = amount_panel.rolling(20).mean() / 1e4
    select_days = 0
    total_days = 0
    for date in dates[20:]:
        if date in avg_amount.index:
            total_days += 1
            day_am = avg_amount.loc[date]
            mask = (day_am > V13Config.min_liquidity) & (day_am < max_liquidity)
            if mask.sum() >= V13Config.max_holdings * 2:
                select_days += 1
    pool_rate = select_days / max(total_days, 1) * 100

    return {
        'max_liq': max_liquidity, 'annual': annual, 'sharpe': sharpe,
        'max_dd': max_dd, 'total_trades': len(sells), 'win_rate': wr,
        'pool_rate': pool_rate, 'nav_final': nav.iloc[-1],
    }


if __name__ == '__main__':
    cap_list = [8000, 5000, 3000, 2000, 1500]
    results = []
    for cap in cap_list:
        print(f"Running max_liquidity={cap}w...", end=' ', flush=True)
        t0 = time.time()
        m = run_v13_with_liquidity_cap(cap)
        m['elapsed'] = time.time() - t0
        results.append(m)
        print(f"年化={m['annual']:.1f}% 夏普={m['sharpe']:.3f} 回撤={m['max_dd']:.1f}% "
              f"选股率={m['pool_rate']:.0f}% 耗时={m['elapsed']:.0f}s")

    print(f"\n{'='*80}")
    print(f"{'上限':>6} {'年化':>8} {'夏普':>7} {'回撤':>8} {'交易':>5} {'胜率':>6} {'选股率':>7}")
    for r in results:
        print(f"{r['max_liq']:>6}w {r['annual']:>7.1f}% {r['sharpe']:>7.3f} {r['max_dd']:>7.1f}% "
              f"{r['total_trades']:>5} {r['win_rate']:>5.0f}% {r['pool_rate']:>6.0f}%")
