#!/usr/bin/env python3
"""
v20_select_param_scan — 选股参数扫描（全量回测）
====================================================
扫描 v20 尾盘选股条件参数，找出最优组合。

重点扫描（解决选不到股的问题）：
- vol_vs_avg_max: 缩量阈值（当前0.8，太严）
- range_vs_avg: 振幅收窄阈值（当前0.8，太严）
- amount_vs_avg_min/max: 成交额范围（当前0.5~3.0）
- recent_limit_up: 涨停史天数窗口（当前20天，太严）
- price_above_ma5: 是否要求价格>MA5

评价指标：年化、夏普、回撤、交易次数、选股天数占比

用法：
    python scripts/v20_select_param_scan.py
    python scripts/v20_select_param_scan.py --quick  # 快速模式（小网格）
"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime
from itertools import product

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from scripts.v20_tail_pick import (
    V20Config,
    load_panel,
    calc_tail_pick_factors,
    select_stocks_tail_pick,
    calc_v20_metrics,
)

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"))
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")


def run_v20_with_params(vol_vs_avg_max, range_vs_avg, amount_vs_avg_min, amount_vs_avg_max,
                         recent_limit_up, price_above_ma5,
                         close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel):
    """用指定参数跑 v20 全量回测"""
    orig_vol = V20Config.vol_vs_avg_max
    orig_range = V20Config.range_vs_avg
    orig_ar_min = V20Config.amount_vs_avg_min
    orig_ar_max = V20Config.amount_vs_avg_max
    orig_lu = V20Config.recent_limit_up
    orig_ma5 = V20Config.price_above_ma5

    V20Config.vol_vs_avg_max = vol_vs_avg_max
    V20Config.range_vs_avg = range_vs_avg
    V20Config.amount_vs_avg_min = amount_vs_avg_min
    V20Config.amount_vs_avg_max = amount_vs_avg_max
    V20Config.recent_limit_up = recent_limit_up
    V20Config.price_above_ma5 = price_above_ma5

    try:
        factors = calc_tail_pick_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)

        cfg = V20Config()
        initial_capital = cfg.initial_capital
        cash = initial_capital
        holdings = {}
        nav_list = []
        trade_log = []
        dates = close_panel.index
        pending_buy = []
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

            # 1. 执行待买入队列
            if pending_buy and cash > initial_capital * 0.1 and len(holdings) < cfg.max_holdings:
                available_cash = cash - initial_capital * 0.1
                n_buy = min(len(pending_buy), cfg.max_daily_buy,
                            cfg.max_holdings - len(holdings))
                per_stock = available_cash / n_buy if n_buy > 0 else 0
                per_stock = min(per_stock, initial_capital * cfg.max_position)

                for code, score in pending_buy[:n_buy]:
                    if code not in open_data.index:
                        continue
                    buy_price = open_data[code]
                    if pd.isna(buy_price) or buy_price <= 0:
                        continue
                    if i > 0:
                        prev_close = close_panel.iloc[i-1].get(code, None) if code in close_panel.columns else None
                        if prev_close and not pd.isna(prev_close) and prev_close > 0:
                            limit_up = prev_close * 1.10
                            if buy_price >= limit_up * 0.99:
                                continue
                    adj = buy_price * (1 + cfg.commission_rate + cfg.slippage_rate)
                    shares = int(per_stock / adj / 100) * 100
                    if shares <= 0:
                        continue
                    cost = shares * adj
                    if cost > cash:
                        continue
                    cash -= cost
                    holdings[code] = {
                        'shares': shares, 'cost': buy_price,
                        'hold_days': 0, 'buy_date': date,
                    }
                    trade_log.append({
                        'date': str(date.date()), 'code': code, 'action': 'buy',
                        'price': round(buy_price, 2), 'shares': shares,
                        'score': round(score, 2),
                    })

            pending_buy = []

            # 2. 更新持仓天数
            for code in holdings:
                holdings[code]['hold_days'] += 1

            # 3. 风控检查
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
                if h['hold_days'] >= cfg.hold_days_max:
                    to_sell.append((code, 'timeout', pnl_pct))
                    continue

            sold_codes = set()
            for code, reason, pnl_pct in to_sell:
                if code in price_data.index:
                    sell_price = price_data[code]
                    if pd.isna(sell_price) or sell_price <= 0:
                        continue
                    if i > 0:
                        prev_close = close_panel.iloc[i-1].get(code, None) if code in close_panel.columns else None
                        if prev_close and not pd.isna(prev_close) and prev_close > 0:
                            if sell_price <= prev_close * 0.90 * 1.01:
                                holdings[code]['hold_days'] = max(0, holdings[code]['hold_days'] - 1)
                                continue
                    h = holdings[code]
                    sv = h['shares'] * sell_price * (1 - cfg.commission_rate - cfg.stamp_tax - cfg.slippage_rate)
                    cash += sv
                    trade_log.append({
                        'date': str(date.date()), 'code': code, 'action': 'sell',
                        'reason': reason, 'pnl_pct': round(pnl_pct * 100, 2),
                    })
                    sold_codes.add(code)
            for code in sold_codes:
                holdings.pop(code, None)

            # 4. 尾盘选股
            if len(holdings) < cfg.max_holdings:
                candidates = select_stocks_tail_pick(
                    factors, date, close_panel, volume_panel, amount_panel,
                    high_panel, low_panel, holdings
                )

                avg_amount = amount_panel.rolling(20).mean() / 1e4
                if date in avg_amount.index:
                    day_amount = avg_amount.loc[date]
                    liquid_mask = (day_amount > cfg.min_liquidity) & (day_amount < cfg.max_liquidity)
                    n_liquid = liquid_mask.sum()
                    if n_liquid > 0:
                        total_signal_days += 1
                        if len(candidates) > 0:
                            select_days += 1

                if candidates:
                    vol_ratio = factors['vol_ratio'].loc[date]
                    range_ratio = factors['range_ratio'].loc[date]
                    recent_lu = factors['recent_limit_up'].loc[date]
                    scored = []
                    for code in candidates:
                        vr = vol_ratio.get(code, 999)
                        rr = range_ratio.get(code, 999)
                        lu = recent_lu.get(code, 0)
                        score = (1.0 / (vr + 0.1)) * 2.0 + (1.0 / (rr + 0.1)) * 1.0 + lu * 0.5
                        scored.append((code, score))
                    scored.sort(key=lambda x: x[1], reverse=True)
                    pending_buy = scored[:cfg.max_daily_buy]

            # 5. NAV
            portfolio_value = cash
            for code, h in holdings.items():
                if code in price_data.index:
                    p = price_data[code]
                    if not pd.isna(p) and p > 0:
                        portfolio_value += h['shares'] * p
            nav_list.append(portfolio_value)

        nav = pd.Series(nav_list, index=dates[:len(nav_list)])
        metrics = calc_v20_metrics(nav, trade_log, initial_capital)
        metrics['select_days_ratio'] = select_days / total_signal_days if total_signal_days > 0 else 0
        metrics['avg_daily_select'] = select_days / max(total_signal_days, 1)
        metrics['total_signal_days'] = total_signal_days
        metrics['select_days'] = select_days

    finally:
        V20Config.vol_vs_avg_max = orig_vol
        V20Config.range_vs_avg = orig_range
        V20Config.amount_vs_avg_min = orig_ar_min
        V20Config.amount_vs_avg_max = orig_ar_max
        V20Config.recent_limit_up = orig_lu
        V20Config.price_above_ma5 = orig_ma5

    return metrics


def main():
    import argparse
    parser = argparse.ArgumentParser(description="v20 选股参数扫描")
    parser.add_argument("--quick", action="store_true", help="快速模式（小网格）")
    parser.add_argument("--vol-max", nargs='+', type=float, help="缩量阈值列表")
    parser.add_argument("--range-max", nargs='+', type=float, help="振幅收窄阈值列表")
    parser.add_argument("--ar-min", nargs='+', type=float, help="成交额下限列表")
    parser.add_argument("--ar-max", nargs='+', type=float, help="成交额上限列表")
    parser.add_argument("--limit-up-days", nargs='+', type=int, help="涨停史天数窗口")
    parser.add_argument("--no-ma5-filter", action='store_true', help="不要求价格>MA5")
    args = parser.parse_args()

    # 参数网格
    if args.quick:
        vol_list = [0.8, 1.2]
        range_list = [0.8, 1.0]
        ar_min_list = [0.3, 0.5]
        ar_max_list = [3.0, 5.0]
        lu_list = [20, 60]
    else:
        vol_list = args.vol_max if args.vol_max else [0.5, 0.8, 1.0, 1.2, 1.5]
        range_list = args.range_max if args.range_max else [0.5, 0.8, 1.0, 1.2]
        ar_min_list = args.ar_min if args.ar_min else [0.3, 0.5, 0.8, 1.0]
        ar_max_list = args.ar_max if args.ar_max else [2.0, 3.0, 5.0]
        lu_list = args.limit_up_days if args.limit_up_days else [10, 20, 30, 60]

    ma5_options = [False] if args.no_ma5_filter else [True, False]

    print("=" * 70)
    print("v20 选股参数扫描" + (" (快速模式)" if args.quick else ""))
    print("=" * 70)

    print("\n[1/2] 加载数据...")
    t0 = time.time()
    close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = load_panel()
    print(f"  Panel: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只")
    print(f"  耗时 {time.time()-t0:.1f}s")

    # 生成参数组合
    param_grid = list(product(vol_list, range_list, ar_min_list, ar_max_list, lu_list, ma5_options))
    param_grid = [(vr, rr, armin, armax, lu, ma5) for vr, rr, armin, armax, lu, ma5 in param_grid if armin < armax]
    total = len(param_grid)
    print(f"\n参数网格: vol={vol_list} range={range_list} ar_min={ar_min_list} ar_max={ar_max_list} lu={lu_list} ma5={ma5_options}")
    print(f"总组合数: {total}")
    print(f"\n[2/2] 扫描 {total} 组参数...")

    results = []
    for idx, (vr, rr, armin, armax, lu, ma5) in enumerate(param_grid):
        t0 = time.time()
        metrics = run_v20_with_params(vr, rr, armin, armax, lu, ma5,
                                       close_panel, volume_panel, amount_panel,
                                       high_panel, low_panel, open_panel)
        elapsed = time.time() - t0

        result = {
            'vol_vs_avg_max': vr,
            'range_vs_avg': rr,
            'amount_vs_avg_min': armin,
            'amount_vs_avg_max': armax,
            'recent_limit_up': lu,
            'price_above_ma5': ma5,
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

        print(f"  [{idx+1}/{total}] vol<{vr:.1f} range<{rr:.1f} ar={armin:.1f}-{armax:.1f} "
              f"lu={lu}d ma5={ma5} | "
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
    print(f"{'vol<':>5} {'rng<':>5} {'ar_min':>6} {'ar_max':>6} {'lu':>4} {'ma5':>4} | "
          f"{'年化':>7} {'夏普':>6} {'回撤':>7} {'交易':>5} {'胜率':>5} {'选股率':>6}")
    print(f"{'-'*70}")
    for r in results[:10]:
        print(f"{r['vol_vs_avg_max']:>5.1f} {r['range_vs_avg']:>5.1f} "
              f"{r['amount_vs_avg_min']:>6.1f} {r['amount_vs_avg_max']:>6.1f} "
              f"{r['recent_limit_up']:>4} {str(r['price_above_ma5']):>4} | "
              f"{r['annual_return']:>6.1f}% {r['sharpe']:>6.3f} "
              f"{r['max_drawdown']:>6.1f}% {r['total_trades']:>5} "
              f"{r['win_rate']:>4.0f}% {r['select_days_ratio']:>5.0%}")

    # 额外：打印选股率>50%的 Top 10
    high_select = sorted([r for r in results if r['select_days_ratio'] > 0.5],
                         key=lambda x: x['sharpe'], reverse=True)
    if high_select:
        print(f"\n{'='*70}")
        print("选股率>50% 的 Top 10（按夏普排序）")
        print(f"{'='*70}")
        for r in high_select[:10]:
            print(f"{r['vol_vs_avg_max']:>5.1f} {r['range_vs_avg']:>5.1f} "
                  f"{r['amount_vs_avg_min']:>6.1f} {r['amount_vs_avg_max']:>6.1f} "
                  f"{r['recent_limit_up']:>4} {str(r['price_above_ma5']):>4} | "
                  f"{r['annual_return']:>6.1f}% {r['sharpe']:>6.3f} "
                  f"{r['max_drawdown']:>6.1f}% {r['total_trades']:>5} "
                  f"{r['win_rate']:>4.0f}% {r['select_days_ratio']:>5.0%}")

    # 保存结果
    out_dir = os.path.join(REPORT_DIR, "v20_select_scan_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "scan_results.json"), "w") as f:
        json.dump({'results': results}, f, indent=2, ensure_ascii=False)
    print(f"\n结果已保存: {out_dir}/scan_results.json")

    return results


if __name__ == '__main__':
    main()
