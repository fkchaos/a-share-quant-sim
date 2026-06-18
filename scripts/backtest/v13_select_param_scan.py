#!/usr/bin/env python3
"""
v13_select_param_scan — 选股参数扫描（全量回测）
====================================================
扫描 v13 选股条件参数，找出最优组合。

评价指标：年化、夏普、回撤、交易次数、胜率、选股天数占比

用法：
    python scripts/v13_select_param_scan.py
    python scripts/v13_select_param_scan.py --rev-threshold -0.03 -0.02 -0.015
"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime
from itertools import product

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from scripts.v13_small_mid_short import (
    V13Config,
    load_small_cap_panel,
    calc_small_cap_factors,
    select_stocks,
    calc_v13_metrics,
)

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"))
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")


def run_v13_with_params(rev_threshold, vol_ratio_threshold, min_liquidity, max_liquidity,
                         close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel):
    """用指定参数跑 v13 全量回测"""
    orig_rev = V13Config.rev_threshold
    orig_vol = V13Config.vol_ratio_threshold
    orig_min_liq = V13Config.min_liquidity
    orig_max_liq = V13Config.max_liquidity

    V13Config.rev_threshold = rev_threshold
    V13Config.vol_ratio_threshold = vol_ratio_threshold
    V13Config.min_liquidity = min_liquidity
    V13Config.max_liquidity = max_liquidity

    try:
        factors = calc_small_cap_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)

        cfg = V13Config()
        initial_capital = cfg.initial_capital
        cash = initial_capital
        holdings = {}
        nav_list = []
        trade_log = []
        dates = close_panel.index
        select_days = 0
        total_signal_days = 0

        for i, date in enumerate(dates):
            if i < 20:
                nav_list.append(initial_capital)
                continue

            if date not in close_panel.index:
                nav_list.append(nav_list[-1] if nav_list else initial_capital)
                continue

            price_data = close_panel.loc[date]
            open_data = open_panel.loc[date] if open_panel is not None else price_data

            # 1. 更新持仓天数
            for code in holdings:
                holdings[code]['hold_days'] += 1

            # 2. 风控检查
            to_sell = []
            for code, h in list(holdings.items()):
                if code not in price_data.index:
                    continue
                current_price = price_data[code]
                if pd.isna(current_price) or current_price <= 0:
                    continue
                pnl_pct = (current_price - h['cost']) / h['cost']

                if pnl_pct <= cfg.stop_loss:
                    to_sell.append((code, 'stop_loss', pnl_pct))
                    continue
                if pnl_pct >= cfg.stop_profit:
                    to_sell.append((code, 'stop_profit', pnl_pct))
                    continue
                hd = h['hold_days']
                if pnl_pct >= cfg.hold_days_extend_pnl and hd >= cfg.hold_days_max:
                    if hd >= cfg.hold_days_extend:
                        to_sell.append((code, 'timeout_extend', pnl_pct))
                        continue
                elif hd >= cfg.hold_days_max:
                    to_sell.append((code, 'timeout', pnl_pct))
                    continue

            sold_codes = set()
            for code, reason, pnl_pct in to_sell:
                if code in price_data.index:
                    sell_price = price_data[code]
                    if pd.isna(sell_price) or sell_price <= 0:
                        continue
                    if i > 0:
                        prev_close = close_panel.iloc[i-1][code] if code in close_panel.columns else None
                        if prev_close and not pd.isna(prev_close) and prev_close > 0:
                            limit_down = prev_close * 0.90
                            if sell_price <= limit_down * 1.01:
                                holdings[code]['hold_days'] = max(0, holdings[code]['hold_days'] - 1)
                                continue
                    h = holdings[code]
                    sell_value = h['shares'] * sell_price * (1 - cfg.commission_rate - cfg.stamp_tax - cfg.slippage_rate)
                    cash += sell_value
                    trade_log.append({
                        'date': str(date.date()), 'code': code, 'action': 'sell',
                        'reason': reason, 'pnl_pct': round(pnl_pct * 100, 2),
                    })
                    sold_codes.add(code)
            for code in sold_codes:
                holdings.pop(code, None)

            # 3. 选股
            candidates = select_stocks(factors, date, close_panel, volume_panel, amount_panel, holdings)

            # 统计选股情况
            avg_amount = amount_panel.rolling(20).mean() / 1e4
            if date in avg_amount.index:
                day_amount = avg_amount.loc[date]
                liquid_mask = (day_amount > cfg.min_liquidity) & (day_amount < cfg.max_liquidity)
                n_liquid = liquid_mask.sum()
                if n_liquid > 0:
                    total_signal_days += 1
                    if len(candidates) > 0:
                        select_days += 1

            # 4. 买入
            if candidates and cash > initial_capital * 0.1 and len(holdings) < cfg.max_holdings:
                available_cash = cash - initial_capital * 0.1
                per_stock = min(available_cash / min(len(candidates), cfg.max_daily_buy),
                                initial_capital * cfg.max_position)
                for code in candidates[:cfg.max_daily_buy]:
                    if code not in price_data.index:
                        continue
                    buy_price = open_data.get(code, price_data.get(code, None))
                    if buy_price is None or pd.isna(buy_price) or buy_price <= 0:
                        continue
                    if i > 0:
                        prev_close = close_panel.iloc[i-1].get(code, None)
                        if prev_close and not pd.isna(prev_close) and prev_close > 0:
                            limit_up = prev_close * 1.10
                            if buy_price >= limit_up * 0.99:
                                continue
                    adj_price = buy_price * (1 + cfg.commission_rate + cfg.slippage_rate)
                    shares = int(per_stock / adj_price / 100) * 100
                    if shares <= 0:
                        continue
                    cost = shares * adj_price
                    if cost > cash:
                        continue
                    cash -= cost
                    holdings[code] = {'shares': shares, 'cost': buy_price, 'hold_days': 0}
                    trade_log.append({
                        'date': str(date.date()), 'code': code, 'action': 'buy',
                        'price': round(buy_price, 2), 'shares': shares,
                    })

            # 5. NAV
            portfolio_value = cash
            for code, h in holdings.items():
                if code in price_data.index:
                    p = price_data[code]
                    if not pd.isna(p) and p > 0:
                        portfolio_value += h['shares'] * p
            nav_list.append(portfolio_value)

        nav = pd.Series(nav_list, index=dates[:len(nav_list)])
        metrics = calc_v13_metrics(nav, trade_log, initial_capital)
        metrics['select_days_ratio'] = select_days / total_signal_days if total_signal_days > 0 else 0
        metrics['avg_daily_select'] = select_days / max(total_signal_days, 1)
        metrics['total_signal_days'] = total_signal_days
        metrics['select_days'] = select_days

    finally:
        V13Config.rev_threshold = orig_rev
        V13Config.vol_ratio_threshold = orig_vol
        V13Config.min_liquidity = orig_min_liq
        V13Config.max_liquidity = orig_max_liq

    return metrics


def main():
    import argparse
    parser = argparse.ArgumentParser(description="v13 选股参数扫描")
    parser.add_argument("--rev-threshold", nargs='+', type=float,
                        default=[-0.03, -0.02, -0.015],
                        help="反转阈值列表（默认3个）")
    parser.add_argument("--vol-ratio", nargs='+', type=float,
                        default=[1.0, 1.3, 1.5],
                        help="放量阈值列表（默认3个）")
    parser.add_argument("--min-liquidity", nargs='+', type=float,
                        default=[200, 300, 500],
                        help="最小日均成交额（万，默认3个）")
    parser.add_argument("--max-liquidity", nargs='+', type=float,
                        default=[8000, 10000, 15000],
                        help="最大日均成交额（万，默认3个）")
    args = parser.parse_args()

    print("=" * 70)
    print("v13 选股参数扫描")
    print("=" * 70)

    # 加载数据（只加载一次）
    print("\n[1/2] 加载数据...")
    t0 = time.time()
    close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = load_small_cap_panel()
    print(f"  Panel: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只")
    print(f"  耗时 {time.time()-t0:.1f}s")

    # 生成参数组合
    param_grid = list(product(args.rev_threshold, args.vol_ratio,
                              args.min_liquidity, args.max_liquidity))
    param_grid = [(r, v, mn, mx) for r, v, mn, mx in param_grid if mn < mx]
    total = len(param_grid)
    print(f"\n参数网格: rev={args.rev_threshold} vol={args.vol_ratio} min_l={args.min_liquidity} max_l={args.max_liquidity}")
    print(f"总组合数: {total}")
    print(f"\n[2/2] 扫描 {total} 组参数...")

    results = []
    for idx, (rev, vol, min_liq, max_liq) in enumerate(param_grid):
        t0 = time.time()
        metrics = run_v13_with_params(rev, vol, min_liq, max_liq,
                                       close_panel, volume_panel, amount_panel,
                                       high_panel, low_panel, open_panel)
        elapsed = time.time() - t0

        result = {
            'rev_threshold': rev,
            'vol_ratio_threshold': vol,
            'min_liquidity': min_liq,
            'max_liquidity': max_liq,
            'annual_return': metrics['annual_return'],
            'sharpe': metrics['sharpe'],
            'max_drawdown': metrics['max_drawdown'],
            'total_trades': metrics['total_trades'],
            'win_rate': metrics['win_rate'],
            'select_days_ratio': round(metrics['select_days_ratio'], 3),
            'avg_daily_select': round(metrics['avg_daily_select'], 1),
            'elapsed_s': round(elapsed, 1),
        }
        results.append(result)

        print(f"  [{idx+1}/{total}] rev={rev:.3f} vol={vol:.1f} liq={min_liq:.0f}-{max_liq:.0f}万 | "
              f"Ret={metrics['annual_return']:.1f}% Sharpe={metrics['sharpe']:.3f} "
              f"DD={metrics['max_drawdown']:.1f}% Trades={metrics['total_trades']} "
              f"Win={metrics['win_rate']:.0f}% Select={metrics['select_days_ratio']:.0%} "
              f"({elapsed:.0f}s)")

    # 排序：按夏普降序
    results.sort(key=lambda x: x['sharpe'], reverse=True)

    # 打印 Top 10
    print(f"\n{'='*70}")
    print("Top 10（按夏普排序）")
    print(f"{'='*70}")
    print(f"{'rev':>6} {'vol':>5} {'min_l':>6} {'max_l':>6} | {'年化':>7} {'夏普':>6} {'回撤':>7} {'交易':>5} {'胜率':>5} {'选股率':>6}")
    print(f"{'-'*70}")
    for r in results[:10]:
        print(f"{r['rev_threshold']:>6.3f} {r['vol_ratio_threshold']:>5.1f} "
              f"{r['min_liquidity']:>6.0f} {r['max_liquidity']:>6.0f} | "
              f"{r['annual_return']:>6.1f}% {r['sharpe']:>6.3f} "
              f"{r['max_drawdown']:>6.1f}% {r['total_trades']:>5} "
              f"{r['win_rate']:>4.0f}% {r['select_days_ratio']:>5.0%}")

    # 保存结果
    out_dir = os.path.join(REPORT_DIR, "v13_select_scan_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "scan_results.json"), "w") as f:
        json.dump({'results': results}, f, indent=2, ensure_ascii=False)
    print(f"\n结果已保存: {out_dir}/scan_results.json")

    return results


if __name__ == '__main__':
    main()
