#!/usr/bin/env python3
"""
scan_v39c_params.py — v39c 参数扫描（精简版）
"""
import sys, os, time, json
sys.path.insert(0, 'scripts')
sys.path.insert(0, 'scripts/tools')
sys.path.insert(0, 'scripts/backtest')

import pandas as pd
import numpy as np
from scripts.backtest.strategy_adapter import get_adapter
from scripts.backtest.wf_runner import _calc_factors
from core.db import load_panel_from_db
from core.account import PortfolioState, buy, sell, portfolio_value
from core.config import TradingCosts

# ── 扫描配置（精简）──
SCAN_PARAMS = {
    "MOM_THRESHOLD": [0.03, 0.04, 0.05, 0.06, 0.07],
    "PV_CORR_20_MIN": [-0.1, 0.0, 0.05, 0.10, 0.15],
    "W_MOM": [0.15, 0.20, 0.25, 0.30, 0.35],
    "W_PV_CORR": [0.10, 0.15, 0.20, 0.25, 0.30],
}

from scripts.strategies.v39c_pv_resonance import DEFAULT_PARAMS
BASE_PARAMS = dict(DEFAULT_PARAMS)

def run_backtest_with_params(params):
    adapter = get_adapter()
    strategy_name = "v39c"

    tpl, codes = load_panel_from_db(need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]

    codes_no_688 = [c for c in codes if not c.startswith('688')]
    close_panel = close_panel[codes_no_688]
    volume_panel = volume_panel[codes_no_688]
    amount_panel = amount_panel[codes_no_688]
    open_panel = open_panel[codes_no_688]
    high_panel = high_panel[codes_no_688]
    low_panel = low_panel[codes_no_688]

    factors = _calc_factors(strategy_name, close_panel, volume_panel, amount_panel,
                            high_panel, low_panel, open_panel)

    initial_capital = 200000
    state = PortfolioState(cash=initial_capital, initial_capital=initial_capital)
    nav_list = []
    dates = close_panel.index
    sold_recently = {}
    cooldown_days = params.get("COOLDOWN_DAYS", 0)
    max_holdings = params.get("MAX_HOLDINGS", 8)
    max_daily_buy = params.get("MAX_DAILY_BUY", 4)
    max_position = params.get("MAX_POSITION", 0.20)

    for i in range(len(dates)):
        if i < 30:
            nav_list.append(initial_capital)
            continue

        date = dates[i]
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else initial_capital)
            continue

        price_data = close_panel.loc[date]
        prev_close = close_panel.iloc[i - 1] if i > 0 else None

        for code in list(state.holdings.keys()):
            info = state.holdings[code]
            try:
                entry = pd.Timestamp(info.get('entry_date', str(date)))
                today = pd.Timestamp(date)
                if entry in close_panel.index and today in close_panel.index:
                    entry_idx = close_panel.index.get_loc(entry)
                    today_idx = close_panel.index.get_loc(today)
                    info['hold_days'] = today_idx - entry_idx
                else:
                    info['hold_days'] = (today - entry).days
            except Exception:
                info['hold_days'] = info.get('hold_days', 0) + 1

        to_sell = adapter.risk_check(strategy_name, state, date, price_data,
                                      params, prev_close=prev_close)

        for code, reason, pnl in to_sell:
            if code in state.holdings and code in price_data.index:
                sell_price = price_data[code]
                if not pd.isna(sell_price) and sell_price > 0:
                    if i > 0 and prev_close is not None and code in prev_close.index:
                        prev_c = prev_close[code]
                        if not pd.isna(prev_c) and prev_c > 0:
                            if sell_price <= prev_c * 0.90 * 1.01:
                                continue
                    state = sell(state, code, sell_price, date, reason=reason)
                    sold_recently[code] = date

        if i >= 30:
            cands = adapter.select(strategy_name, factors, date,
                                   close_panel, volume_panel, amount_panel,
                                   high_panel, low_panel, open_panel,
                                   current_holdings=state.holdings,
                                   params=params,
                                   sold_recently=sold_recently if cooldown_days > 0 else None)

            if cands and state.cash > initial_capital * 0.03:
                avail = state.cash - initial_capital * 0.03
                nb = min(len(cands), max_daily_buy, max_holdings - len(state.holdings))
                per_stock = min(avail / nb, initial_capital * max_position) if nb > 0 else 0

                bought = 0
                for code, score in cands[:max_daily_buy]:
                    if len(state.holdings) >= max_holdings or bought >= nb:
                        break
                    if code not in price_data.index:
                        continue
                    buy_price = price_data[code]
                    if pd.isna(buy_price) or buy_price <= 0:
                        continue
                    if i > 0 and prev_close is not None and code in prev_close.index:
                        prev_c = prev_close[code]
                        if not pd.isna(prev_c) and prev_c > 0:
                            if buy_price >= prev_c * 1.10 * 0.99:
                                continue
                    adj = buy_price * (1 + TradingCosts().slippage_rate)
                    shares = int(per_stock / adj / 100) * 100
                    if shares <= 0:
                        continue
                    state = buy(state, code, buy_price, date, shares=shares)
                    if code in state.holdings:
                        bought += 1

        pv = portfolio_value(state, date, price_data)
        nav_list.append(pv)

    nav_s = pd.Series(nav_list)
    total_ret = nav_s.iloc[-1] / nav_s.iloc[0] - 1
    max_dd = ((nav_s.cummax() - nav_s) / nav_s.cummax()).max()
    daily_ret = nav_s.pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * 252**0.5 if daily_ret.std() > 0 else 0

    return {
        "return": round(total_ret * 100, 2),
        "max_dd": round(max_dd * 100, 2),
        "sharpe": round(sharpe, 3),
        "nav": round(nav_s.iloc[-1], 0),
    }


def main():
    print("=" * 60)
    print("v39c 参数扫描")
    print("=" * 60)

    all_results = {}

    for param_name, values in SCAN_PARAMS.items():
        print(f"\n── 扫描 {param_name} ──")
        results = []
        for val in values:
            params = dict(BASE_PARAMS)
            params[param_name] = val
            try:
                result = run_backtest_with_params(params)
                result["value"] = val
                results.append(result)
                print(f"  {param_name}={val}: 收益={result['return']}%, 回撤={result['max_dd']}%, 夏普={result['sharpe']}")
            except Exception as e:
                results.append({"value": val, "return": None, "error": str(e)})
                print(f"  {param_name}={val}: 错误 {e}")
        all_results[param_name] = results

        valid = [r for r in results if r.get("return") is not None]
        if valid:
            best = max(valid, key=lambda x: x["sharpe"])
            print(f"  ★ 最优: {param_name}={best['value']} (夏普={best['sharpe']}, 收益={best['return']}%)")

    print("\n" + "=" * 60)
    print("扫描结果汇总")
    print("=" * 60)
    for param_name, results in all_results.items():
        valid = [r for r in results if r.get("return") is not None]
        if valid:
            best = max(valid, key=lambda x: x["sharpe"])
            print(f"{param_name:20s}: 最优值={best['value']}, 夏普={best['sharpe']}, 收益={best['return']}%, 回撤={best['max_dd']}%")

    with open("/tmp/v39c_scan_results.json", "w") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    print(f"\n结果已保存到 /tmp/v39c_scan_results.json")


if __name__ == "__main__":
    main()
