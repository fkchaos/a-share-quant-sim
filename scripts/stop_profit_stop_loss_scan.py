#!/usr/bin/env python3
"""
stop_profit_stop_loss_scan.py — 止盈止损参数网格扫描
======================================================
对 v13 和 v20 分别扫描非对称止盈止损组合
扫描范围：止损 2%~8%，止盈 3%~15%
评价指标：夏普比率、最大回撤、总收益、胜率、盈亏比

用法：
    python scripts/stop_profit_stop_loss_scan.py
"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime
from itertools import product

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")
os.makedirs(REPORT_DIR, exist_ok=True)

# ── 扫描参数 ──────────────────────────────────────────────────────
# 止损：2%, 3%, 4%, 5%, 6%, 8%
# 止盈：3%, 5%, 7%, 10%, 12%, 15%
STOP_LOSS_RANGE = [0.02, 0.03, 0.04, 0.05, 0.06, 0.08]
TAKE_PROFIT_RANGE = [0.03, 0.05, 0.07, 0.10, 0.12, 0.15]


# ── 通用回测引擎 ──────────────────────────────────────────────────
def run_backtest_generic(
    strategy_name: str,
    load_panel_fn,
    calc_factors_fn,
    select_fn,
    stop_loss: float,
    take_profit: float,
    hold_days_max: int,
    initial_capital: int = 200000,
    max_holdings: int = 8,
    max_position: float = 0.20,
    max_daily_buy: int = 6,
    commission_rate: float = 0.0003,
    stamp_tax: float = 0.001,
    slippage_rate: float = 0.002,
    warmup_pass: int = 20,
):
    """
    通用日频回测引擎（收盘价买卖，简化版 — 不做 T+1 隔夜）
    返回 metrics dict
    """
    t0 = time.time()

    # 加载数据
    loaded = load_panel_fn()
    if len(loaded) == 6:
        close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = loaded
    else:
        close_panel, volume_panel, amount_panel, high_panel, low_panel = loaded
        open_panel = None

    # 计算因子
    factors = calc_factors_fn(close_panel, volume_panel, amount_panel, high_panel, low_panel)

    # 回测循环
    cash = initial_capital
    holdings = {}  # {code: {shares, cost, hold_days}}
    nav_list = []
    trade_log = []
    dates = close_panel.index

    for i, date in enumerate(dates):
        if i < warmup_pass:
            nav_list.append(initial_capital)
            continue

        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else initial_capital)
            continue

        price_data = close_panel.loc[date]
        open_data = open_panel.loc[date] if open_panel is not None else price_data

        # 更新持仓天数
        for code in holdings:
            holdings[code]['hold_days'] += 1

        # 风控检查（止损/止盈/超时）
        to_sell = []
        for code, h in list(holdings.items()):
            if code not in price_data.index:
                continue
            cp = price_data[code]
            if pd.isna(cp) or cp <= 0:
                continue
            pnl = (cp - h['cost']) / h['cost']

            if pnl <= -stop_loss:
                to_sell.append((code, 'stop_loss', pnl))
                continue
            if pnl >= take_profit:
                to_sell.append((code, 'stop_profit', pnl))
                continue
            if h['hold_days'] >= hold_days_max:
                to_sell.append((code, 'timeout', pnl))
                continue

        sold_codes = set()
        for code, reason, pnl in to_sell:
            if code not in price_data.index:
                continue
            sp = price_data[code]
            if pd.isna(sp) or sp <= 0:
                continue
            h = holdings[code]
            sv = h['shares'] * sp * (1 - commission_rate - stamp_tax - slippage_rate)
            cash += sv
            trade_log.append({
                'date': str(date.date()), 'code': code, 'action': 'sell',
                'reason': reason, 'pnl_pct': round(pnl * 100, 2),
            })
            sold_codes.add(code)

        for code in sold_codes:
            holdings.pop(code, None)

        # 选股
        candidates = select_fn(factors, date, close_panel, volume_panel, amount_panel, holdings)
        if not candidates:
            candidates = []

        # 买入
        if candidates and cash > initial_capital * 0.1 and len(holdings) < max_holdings:
            available_cash = cash - initial_capital * 0.1
            n_buy = min(len(candidates), max_daily_buy, max_holdings - len(holdings))
            per_stock = min(available_cash / n_buy, initial_capital * max_position) if n_buy > 0 else 0

            for code in candidates[:n_buy]:
                if code not in price_data.index:
                    continue
                bp = open_data[code] if code in open_data.index else price_data[code]
                if pd.isna(bp) or bp <= 0:
                    continue
                adj = bp * (1 + commission_rate + slippage_rate)
                shares = int(per_stock / adj / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * adj
                if cost > cash:
                    continue
                cash -= cost
                holdings[code] = {'shares': shares, 'cost': bp, 'hold_days': 0}
                trade_log.append({
                    'date': str(date.date()), 'code': code, 'action': 'buy',
                    'price': round(bp, 2), 'shares': shares,
                })

        # NAV
        pv = cash
        for code, h in holdings.items():
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    pv += h['shares'] * p
        nav_list.append(pv)

    elapsed = time.time() - t0

    # 计算绩效
    nav = pd.Series(nav_list, index=dates[:len(nav_list)])
    rets = nav.pct_change().dropna()
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    days = (nav.index[-1] - nav.index[0]).days
    years = max(days / 365, 0.01)
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = rets.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    peak = nav.cummax()
    max_dd = ((nav - peak) / peak).min()
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0

    sells = [t for t in trade_log if t['action'] == 'sell']
    wins = [t for t in sells if t.get('pnl_pct', 0) > 0]
    losses = [t for t in sells if t.get('pnl_pct', 0) <= 0]
    win_rate = len(wins) / len(sells) * 100 if sells else 0
    avg_win = np.mean([t['pnl_pct'] for t in wins]) if wins else 0
    avg_loss = abs(np.mean([t['pnl_pct'] for t in losses])) if losses else 0
    profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')

    sl_trades = len([t for t in sells if t.get('reason') == 'stop_loss'])
    tp_trades = len([t for t in sells if t.get('reason') == 'stop_profit'])
    to_trades = len([t for t in sells if t.get('reason') == 'timeout'])

    metrics = {
        'strategy': strategy_name,
        'stop_loss': stop_loss,
        'take_profit': take_profit,
        'total_return': round(total_ret * 100, 2),
        'annual_return': round(ann_ret * 100, 2),
        'annual_vol': round(ann_vol * 100, 2),
        'sharpe': round(sharpe, 3),
        'max_drawdown': round(max_dd * 100, 2),
        'calmar': round(calmar, 3),
        'total_trades': len(trade_log),
        'sell_trades': len(sells),
        'win_rate': round(win_rate, 1),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'profit_loss_ratio': round(profit_loss_ratio, 2),
        'stop_loss_trades': sl_trades,
        'stop_profit_trades': tp_trades,
        'timeout_trades': to_trades,
        'elapsed_sec': round(elapsed, 1),
    }
    return metrics


def run_v13_scan():
    """v13 止盈止损扫描"""
    from scripts.v13_small_mid_short import (
        load_small_cap_panel, calc_small_cap_factors, select_stocks, V13Config
    )

    cfg = V13Config()
    results = []

    total = len(STOP_LOSS_RANGE) * len(TAKE_PROFIT_RANGE)
    print(f"\n{'='*60}")
    print(f"v13 止盈止损扫描: {len(STOP_LOSS_RANGE)}×{len(TAKE_PROFIT_RANGE)} = {total} 组")
    print(f"止损: {[f'{x:.0%}' for x in STOP_LOSS_RANGE]}")
    print(f"止盈: {[f'{x:.0%}' for x in TAKE_PROFIT_RANGE]}")
    print(f"{'='*60}\n")

    # 只加载一次数据
    print("加载数据...")
    t0 = time.time()
    panel = load_small_cap_panel()
    print(f"数据加载耗时 {time.time()-t0:.1f}s\n")

    count = 0
    for sl, tp in product(STOP_LOSS_RANGE, TAKE_PROFIT_RANGE):
        count += 1
        print(f"[{count}/{total}] SL={sl:.0%} TP={tp:.0%} ...", end=" ", flush=True)

        def load_fn(): return panel

        def select_fn(factors, date, close_panel, volume_panel, amount_panel, holdings):
            return select_stocks(factors, date, close_panel, volume_panel, amount_panel, holdings)

        m = run_backtest_generic(
            strategy_name="v13",
            load_panel_fn=load_fn,
            calc_factors_fn=calc_small_cap_factors,
            select_fn=select_fn,
            stop_loss=sl,
            take_profit=tp,
            hold_days_max=cfg.hold_days_max,
            initial_capital=cfg.initial_capital,
            max_holdings=cfg.max_holdings,
            max_position=cfg.max_position,
            max_daily_buy=cfg.max_daily_buy,
            commission_rate=cfg.commission_rate,
            stamp_tax=cfg.stamp_tax,
            slippage_rate=cfg.slippage_rate,
        )
        results.append(m)
        print(f"夏普={m['sharpe']:.3f} 收益={m['total_return']:.1f}% 回撤={m['max_drawdown']:.1f}%")

    return results


def run_v20_scan():
    """v20 止盈止损扫描"""
    from scripts.v20_tail_pick import (
        load_panel, calc_tail_pick_factors, select_stocks_tail_pick, V20Config
    )

    cfg = V20Config()
    results = []

    total = len(STOP_LOSS_RANGE) * len(TAKE_PROFIT_RANGE)
    print(f"\n{'='*60}")
    print(f"v20 止盈止损扫描: {len(STOP_LOSS_RANGE)}×{len(TAKE_PROFIT_RANGE)} = {total} 组")
    print(f"止损: {[f'{x:.0%}' for x in STOP_LOSS_RANGE]}")
    print(f"止盈: {[f'{x:.0%}' for x in TAKE_PROFIT_RANGE]}")
    print(f"{'='*60}\n")

    print("加载数据...")
    t0 = time.time()
    panel = load_panel()
    print(f"数据加载耗时 {time.time()-t0:.1f}s\n")

    count = 0
    for sl, tp in product(STOP_LOSS_RANGE, TAKE_PROFIT_RANGE):
        count += 1
        print(f"[{count}/{total}] SL={sl:.0%} TP={tp:.0%} ...", end=" ", flush=True)

        def load_fn(): return panel

        # v20 选股需要 high/low panel，从已加载的 panel 中取
        _high_panel = panel[3] if len(panel) > 3 else None
        _low_panel = panel[4] if len(panel) > 4 else None

        def select_fn(factors, date, close_panel, volume_panel, amount_panel, holdings):
            return select_stocks_tail_pick(
                factors, date, close_panel, volume_panel, amount_panel,
                _high_panel, _low_panel, holdings
            )

        m = run_backtest_generic(
            strategy_name="v20",
            load_panel_fn=load_fn,
            calc_factors_fn=calc_tail_pick_factors,
            select_fn=select_fn,
            stop_loss=sl,
            take_profit=tp,
            hold_days_max=cfg.hold_days_max,
            initial_capital=cfg.initial_capital,
            max_holdings=cfg.max_holdings,
            max_position=cfg.max_position,
            max_daily_buy=cfg.max_daily_buy,
            commission_rate=cfg.commission_rate,
            stamp_tax=cfg.stamp_tax,
            slippage_rate=cfg.slippage_rate,
        )
        results.append(m)
        print(f"夏普={m['sharpe']:.3f} 收益={m['total_return']:.1f}% 回撤={m['max_drawdown']:.1f}%")

    return results


def print_scan_table(results, strategy_name):
    """打印扫描结果表格"""
    df = pd.DataFrame(results)
    df = df.sort_values('sharpe', ascending=False)

    print(f"\n{'='*80}")
    print(f"{strategy_name} 扫描结果 — 按夏普比率排序 (Top 15)")
    print(f"{'='*80}")
    print(f"{'止损':>6} {'止盈':>6} {'总收益':>8} {'年化':>8} {'夏普':>7} {'回撤':>8} {'Calmar':>7} {'胜率':>6} {'盈亏比':>7} {'SL次数':>6} {'TP次数':>6} {'超时':>5}")
    print("-" * 80)

    for _, row in df.head(15).iterrows():
        print(
            f"{row['stop_loss']:>5.0%} "
            f"{row['take_profit']:>5.0%} "
            f"{row['total_return']:>7.1f}% "
            f"{row['annual_return']:>7.1f}% "
            f"{row['sharpe']:>6.3f} "
            f"{row['max_drawdown']:>7.1f}% "
            f"{row['calmar']:>6.3f} "
            f"{row['win_rate']:>5.1f}% "
            f"{row['profit_loss_ratio']:>6.2f} "
            f"{int(row['stop_loss_trades']):>5} "
            f"{int(row['stop_profit_trades']):>5} "
            f"{int(row['timeout_trades']):>4}"
        )

    # 最优参数
    best = df.iloc[0]
    print(f"\n🏆 最优参数 (按夏普):")
    print(f"   止损: {best['stop_loss']:.0%}  止盈: {best['take_profit']:.0%}")
    print(f"   夏普: {best['sharpe']:.3f}  总收益: {best['total_return']:.1f}%  回撤: {best['max_drawdown']:.1f}%")
    print(f"   胜率: {best['win_rate']:.1f}%  盈亏比: {best['profit_loss_ratio']:.2f}")
    print(f"   止损触发: {int(best['stop_loss_trades'])} 次  止盈触发: {int(best['stop_profit_trades'])} 次  超时: {int(best['timeout_trades'])} 次")

    # 按 Calmar 排序
    df_calmar = df.sort_values('calmar', ascending=False)
    best_cm = df_calmar.iloc[0]
    print(f"\n🏆 最优参数 (按Calmar):")
    print(f"   止损: {best_cm['stop_loss']:.0%}  止盈: {best_cm['take_profit']:.0%}")
    print(f"   Calmar: {best_cm['calmar']:.3f}  夏普: {best_cm['sharpe']:.3f}  回撤: {best_cm['max_drawdown']:.1f}%")

    return df


if __name__ == '__main__':
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    # v13 扫描
    v13_results = run_v13_scan()
    v13_df = print_scan_table(v13_results, "v13")

    # v20 扫描
    v20_results = run_v20_scan()
    v20_df = print_scan_table(v20_results, "v20")

    # 保存结果
    all_results = {
        "scan_time": ts,
        "stop_loss_range": STOP_LOSS_RANGE,
        "take_profit_range": TAKE_PROFIT_RANGE,
        "v13": v13_results,
        "v20": v20_results,
    }
    out_path = os.path.join(REPORT_DIR, f"{ts}_sl_tp_scan.json")
    with open(out_path, 'w') as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")

    # CSV 格式
    csv_path = os.path.join(REPORT_DIR, f"{ts}_sl_tp_scan.csv")
    pd.concat([v13_df, v20_df]).to_csv(csv_path, index=False)
    print(f"CSV 已保存: {csv_path}")
