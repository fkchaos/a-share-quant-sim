#!/usr/bin/env python3
"""v13 SL/TP 参数扫描 — 2022-2026 数据"""
import sys, os, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from scripts.v13_small_mid_short import (
    V13Config, load_small_cap_panel, calc_small_cap_factors, select_stocks
)

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"))


def run_v13_with_params(stop_loss, stop_profit, hold_days_max, start_date, end_date):
    """用指定参数跑一次回测，返回绩效指标"""
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

        for code in holdings:
            holdings[code]['hold_days'] += 1

        to_sell = []
        for code, h in holdings.items():
            if code not in price_data.index:
                continue
            current_price = price_data[code]
            if pd.isna(current_price) or current_price <= 0:
                continue
            pnl_pct = (current_price - h['cost']) / h['cost']
            if pnl_pct <= stop_loss:
                to_sell.append((code, 'stop_loss', pnl_pct))
                continue
            if pnl_pct >= stop_profit:
                to_sell.append((code, 'stop_profit', pnl_pct))
                continue
            if h['hold_days'] >= hold_days_max:
                to_sell.append((code, 'timeout', pnl_pct))
                continue

        sold_codes = set()
        for code, reason, pnl_pct in to_sell:
            if code in price_data.index:
                sell_price = price_data[code]
                if pd.isna(sell_price) or sell_price <= 0:
                    continue
                if i > 0:
                    prev_close = close_panel.iloc[i - 1][code] if code in close_panel.columns else None
                    if prev_close and not pd.isna(prev_close) and prev_close > 0:
                        if sell_price <= prev_close * 0.90 * 1.01:
                            holdings[code]['hold_days'] = max(0, holdings[code]['hold_days'] - 1)
                            continue
                h = holdings[code]
                sv = h['shares'] * sell_price * (1 - V13Config.commission_rate - V13Config.stamp_tax - V13Config.slippage_rate)
                cash += sv
                trade_log.append({'date': str(date.date()), 'code': code, 'action': 'sell',
                                  'reason': reason, 'pnl_pct': round(pnl_pct * 100, 2), 'value': round(sv, 2)})
                sold_codes.add(code)
        for code in sold_codes:
            holdings.pop(code, None)

        candidates = select_stocks(factors, date, close_panel, volume_panel, amount_panel, holdings)
        if candidates and cash > initial_capital * 0.1 and len(holdings) < V13Config.max_holdings:
            available_cash = cash - initial_capital * 0.1
            per_stock = min(available_cash / min(len(candidates), V13Config.max_daily_buy),
                            initial_capital * V13Config.max_position)
            for code in candidates[:V13Config.max_daily_buy]:
                if code not in price_data.index:
                    continue
                buy_price = open_data[code] if code in open_data.index else price_data[code]
                if pd.isna(buy_price) or buy_price <= 0:
                    continue
                if i > 0:
                    prev_close = close_panel.iloc[i - 1][code] if code in close_panel.columns else None
                    if prev_close and not pd.isna(prev_close) and prev_close > 0:
                        if buy_price >= prev_close * 1.10 * 0.99:
                            continue
                adj_price = buy_price * (1 + V13Config.commission_rate + V13Config.slippage_rate)
                shares = int(per_stock / adj_price / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * adj_price
                if cost > cash:
                    continue
                cash -= cost
                holdings[code] = {'shares': shares, 'cost': buy_price, 'hold_days': 0}
                trade_log.append({'date': str(date.date()), 'code': code, 'action': 'buy',
                                  'price': round(buy_price, 2), 'shares': shares, 'value': round(cost, 2)})

        portfolio_value = cash
        for code, h in holdings.items():
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    portfolio_value += h['shares'] * p
        nav_list.append(portfolio_value)

    nav = pd.Series(nav_list, index=dates[:len(nav_list)])
    total_return = (nav.iloc[-1] / nav.iloc[0] - 1) * 100
    n_days = len(nav)
    annual_return = ((nav.iloc[-1] / nav.iloc[0]) ** (252 / max(n_days, 1)) - 1) * 100
    daily_ret = nav.pct_change().dropna()
    sharpe = daily_ret.mean() / (daily_ret.std() + 1e-10) * np.sqrt(252)
    rolling_max = nav.cummax()
    drawdown = (nav - rolling_max) / rolling_max
    max_dd = drawdown.min() * 100

    sell_trades = [t for t in trade_log if t['action'] == 'sell']
    win_rate = sum(1 for t in sell_trades if t['pnl_pct'] > 0) / max(len(sell_trades), 1) * 100

    # 卖出原因统计
    n_sl = sum(1 for t in sell_trades if t['reason'] == 'stop_loss')
    n_tp = sum(1 for t in sell_trades if t['reason'] == 'stop_profit')
    n_to = sum(1 for t in sell_trades if 'timeout' in t['reason'])
    total_sell = max(len(sell_trades), 1)

    return {
        'total_return': total_return,
        'annual_return': annual_return,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'win_rate': win_rate,
        'total_trades': len(sell_trades),
        'sl_pct': n_sl / total_sell * 100,
        'tp_pct': n_tp / total_sell * 100,
        'to_pct': n_to / total_sell * 100,
    }


if __name__ == '__main__':
    # 扫描范围
    sl_range = [-0.01, -0.015, -0.02, -0.025, -0.03]
    tp_range = [0.03, 0.04, 0.05, 0.07, 0.10, 0.15]
    hold_range = [5, 8, 10]

    start_date = '2022-01-01'
    end_date = '2026-05-31'

    results = []
    total = len(sl_range) * len(tp_range) * len(hold_range)
    t0 = time.time()
    count = 0

    for sl in sl_range:
        for tp in tp_range:
            for hold in hold_range:
                count += 1
                m = run_v13_with_params(sl, tp, hold, start_date, end_date)
                m.update({'sl': sl, 'tp': tp, 'hold': hold})
                results.append(m)
                print(f"[{count}/{total}] SL={sl:.3f} TP={tp:.3f} hold={hold} -> "
                      f"年化={m['annual_return']:.1f}% 夏普={m['sharpe']:.3f} 回撤={m['max_dd']:.1f}% "
                      f"胜率={m['win_rate']:.0f}% TP/SL/TO={m['tp_pct']:.0f}%/{m['sl_pct']:.0f}%/{m['to_pct']:.0f}%")

    elapsed = time.time() - t0
    print(f"\n完成: {total} 组参数, 耗时 {elapsed:.0f}s")

    # 排序输出 Top 10
    results.sort(key=lambda x: x['sharpe'], reverse=True)
    print(f"\n{'='*80}")
    print(f"按夏普排序 Top 10 (2022-2026):")
    print(f"{'='*80}")
    print(f"{'SL':>7} {'TP':>7} {'Hold':>5} {'年化':>8} {'夏普':>7} {'回撤':>8} {'胜率':>6} {'交易':>5} {'TP%':>6} {'SL%':>6} {'TO%':>6}")
    for r in results[:10]:
        print(f"{r['sl']:>7.3f} {r['tp']:>7.3f} {r['hold']:>5} {r['annual_return']:>7.1f}% {r['sharpe']:>7.3f} {r['max_dd']:>7.1f}% {r['win_rate']:>5.0f}% {r['total_trades']:>5} {r['tp_pct']:>5.0f}% {r['sl_pct']:>5.0f}% {r['to_pct']:>5.0f}%")

    # 保存结果
    import json
    out_dir = os.path.join(DATA_DIR, 'backtest_results')
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, 'v13_sltp_scan_2022_2026.json')
    with open(out_file, 'w') as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n结果已保存: {out_file}")
