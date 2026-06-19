#!/usr/bin/env python3
"""
sl_tp_full_scan.py — 三策略止盈止损全量数据网格扫描
=====================================================
使用完整回测区间 (2021-01-01 ~ 2026-05-31)，数据全量可靠
两阶段：粗扫描(3×3) → 细扫描(3×3，基于粗扫描最优)

用法:
    python scripts/sl_tp_full_scan.py           # 粗+细扫描
    python scripts/sl_tp_full_scan.py --coarse   # 只做粗扫描
    python scripts/sl_tp_full_scan.py --fine     # 只做细扫描(基于之前粗扫描结果)
"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")
os.makedirs(REPORT_DIR, exist_ok=True)

# ── 粗网格参数 ──
COARSE_SL = [0.02, 0.03, 0.05]
COARSE_TP = [0.04, 0.05, 0.07]

# ── 完整回测区间 ──
FULL_START = "2021-01-01"
FULL_END = "2026-05-29"

def _calc_metrics(nav_list, trade_log, initial_capital, dates, sl, tp, elapsed, strategy_name):
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
    pl_ratio = avg_win / avg_loss if avg_loss > 0 else float('inf')

    return {
        'strategy': strategy_name,
        'stop_loss': sl,
        'take_profit': tp,
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
        'profit_loss_ratio': round(pl_ratio, 2),
        'stop_loss_trades': len([t for t in sells if t.get('reason') == 'stop_loss']),
        'stop_profit_trades': len([t for t in sells if t.get('reason') == 'stop_profit']),
        'timeout_trades': len([t for t in sells if t.get('reason') == 'timeout']),
        'elapsed_sec': round(elapsed, 1),
    }

# ═══════════════════════════════════════════════
# v13 扫描
# ═══════════════════════════════════════════════
def scan_v13(sl_list, tp_list, phase_name):
    from scripts.v13_small_mid_short import (
        load_small_cap_panel, calc_small_cap_factors, select_stocks, V13Config,
    )

    cfg = V13Config()

    print(f"\n[v13] {phase_name}: 加载数据 ({FULL_START} ~ {FULL_END})...", flush=True)
    t0 = time.time()
    close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = \
        load_small_cap_panel(start_date=FULL_START, end_date=FULL_END)
    factors = calc_small_cap_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
    print(f"[v13] 数据: {close_panel.shape}, 耗时 {time.time()-t0:.1f}s", flush=True)

    dates = close_panel.index
    initial_capital = cfg.initial_capital
    cost_rate = cfg.commission_rate + cfg.stamp_tax + cfg.slippage_rate
    total = len(sl_list) * len(tp_list)
    print(f"[v13] {phase_name}: {len(sl_list)}×{len(tp_list)} = {total} 组\n", flush=True)

    results = []
    count = 0
    for sl, tp in [(s, t) for s in sl_list for t in tp_list]:
        count += 1
        t0 = time.time()
        print(f"  [{count}/{total}] SL={sl:.0%} TP={tp:.0%} ...", end=" ", flush=True)

        cash = initial_capital
        holdings = {}
        nav_list = []
        trade_log = []

        for i, date in enumerate(dates):
            if i < 20:
                nav_list.append(initial_capital)
                continue
            if date not in close_panel.index:
                nav_list.append(nav_list[-1] if nav_list else initial_capital)
                continue

            price_data = close_panel.loc[date]
            open_data = open_panel.loc[date] if open_panel is not None else price_data
            for h in holdings.values():
                h['hold_days'] += 1

            to_sell = []
            for code, h in list(holdings.items()):
                if code not in price_data.index:
                    continue
                cp = price_data[code]
                if pd.isna(cp) or cp <= 0:
                    continue
                pnl = (cp - h['cost']) / h['cost']
                if pnl <= -sl:
                    to_sell.append((code, 'stop_loss', pnl))
                elif pnl >= tp:
                    to_sell.append((code, 'stop_profit', pnl))
                elif h['hold_days'] >= cfg.hold_days_max:
                    to_sell.append((code, 'timeout', pnl))

            sold_codes = set()
            for code, reason, pnl in to_sell:
                if code not in price_data.index:
                    continue
                sp = price_data[code]
                if pd.isna(sp) or sp <= 0:
                    continue
                h = holdings[code]
                sv = h['shares'] * sp * (1 - cost_rate)
                cash += sv
                trade_log.append({'date': str(date.date()), 'code': code, 'action': 'sell',
                                  'reason': reason, 'pnl_pct': round(pnl * 100, 2)})
                sold_codes.add(code)
            for code in sold_codes:
                holdings.pop(code, None)

            candidates = select_stocks(factors, date, close_panel, volume_panel, amount_panel, holdings)
            if candidates and cash > 0 and len(holdings) < cfg.max_holdings:
                available_cash = cash
                n_buy = min(len(candidates), cfg.max_daily_buy, cfg.max_holdings - len(holdings))
                per_stock = min(available_cash / n_buy, initial_capital * cfg.max_position) if n_buy > 0 else 0
                for code in candidates[:n_buy]:
                    if code not in price_data.index:
                        continue
                    bp = open_data[code] if code in open_data.index else price_data[code]
                    if pd.isna(bp) or bp <= 0:
                        continue
                    adj = bp * (1 + cfg.commission_rate + cfg.slippage_rate)
                    shares = int(per_stock / adj / 100) * 100
                    if shares <= 0:
                        continue
                    cost = shares * adj
                    if cost > cash:
                        continue
                    cash -= cost
                    holdings[code] = {'shares': shares, 'cost': bp, 'hold_days': 0}
                    trade_log.append({'date': str(date.date()), 'code': code, 'action': 'buy',
                                      'price': round(bp, 2), 'shares': shares})

            pv = cash
            for code, h in holdings.items():
                if code in price_data.index:
                    p = price_data[code]
                    if not pd.isna(p) and p > 0:
                        pv += h['shares'] * p
            nav_list.append(pv)

        elapsed = time.time() - t0
        metrics = _calc_metrics(nav_list, trade_log, initial_capital, dates, sl, tp, elapsed, "v13")
        results.append(metrics)
        print(f"夏普={metrics['sharpe']:.3f} 收益={metrics['total_return']:.1f}% 回撤={metrics['max_drawdown']:.1f}% ({elapsed:.0f}s)", flush=True)

    return results

# ═══════════════════════════════════════════════
# v20 扫描
# ═══════════════════════════════════════════════
def scan_v20(sl_list, tp_list, phase_name):
    from scripts.v20_tail_pick import (
        load_panel, calc_tail_pick_factors, select_stocks_tail_pick, V20Config,
    )

    cfg = V20Config()

    print(f"\n[v20] {phase_name}: 加载数据 ({FULL_START} ~ {FULL_END})...", flush=True)
    t0 = time.time()
    close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = \
        load_panel(start_date=FULL_START, end_date=FULL_END)
    factors = calc_tail_pick_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
    print(f"[v20] 数据: {close_panel.shape}, 耗时 {time.time()-t0:.1f}s", flush=True)

    dates = close_panel.index
    initial_capital = cfg.initial_capital
    cost_rate = cfg.commission_rate + cfg.stamp_tax + cfg.slippage_rate
    total = len(sl_list) * len(tp_list)
    print(f"[v20] {phase_name}: {len(sl_list)}×{len(tp_list)} = {total} 组\n", flush=True)

    results = []
    count = 0
    for sl, tp in [(s, t) for s in sl_list for t in tp_list]:
        count += 1
        t0 = time.time()
        print(f"  [{count}/{total}] SL={sl:.0%} TP={tp:.0%} ...", end=" ", flush=True)

        cash = initial_capital
        holdings = {}
        nav_list = []
        trade_log = []
        pending_buy = []

        for i, date in enumerate(dates):
            if i < 20:
                nav_list.append(initial_capital)
                continue
            if date not in close_panel.index:
                nav_list.append(nav_list[-1] if nav_list else initial_capital)
                continue

            price_data = close_panel.loc[date]
            open_data = open_panel.loc[date] if open_panel is not None else price_data

            # 执行待买入队列
            if pending_buy and cash > 0 and len(holdings) < cfg.max_holdings:
                available_cash = cash
                n_buy = min(len(pending_buy), cfg.max_daily_buy, cfg.max_holdings - len(holdings))
                per_stock = min(available_cash / n_buy, initial_capital * cfg.max_position) if n_buy > 0 else 0
                for code, score in pending_buy[:n_buy]:
                    if code not in open_data.index:
                        continue
                    bp = open_data[code]
                    if pd.isna(bp) or bp <= 0:
                        continue
                    adj = bp * (1 + cfg.commission_rate + cfg.slippage_rate)
                    shares = int(per_stock / adj / 100) * 100
                    if shares <= 0:
                        continue
                    cost = shares * adj
                    if cost > cash:
                        continue
                    cash -= cost
                    holdings[code] = {'shares': shares, 'cost': bp, 'hold_days': 0, 'buy_date': date}
                    trade_log.append({'date': str(date.date()), 'code': code, 'action': 'buy',
                                      'price': round(bp, 2), 'shares': shares})
            pending_buy = []

            for h in holdings.values():
                h['hold_days'] += 1

            to_sell = []
            for code, h in list(holdings.items()):
                if code not in price_data.index:
                    continue
                cp = price_data[code]
                if pd.isna(cp) or cp <= 0:
                    continue
                pnl = (cp - h['cost']) / h['cost']
                if pnl <= -sl:
                    to_sell.append((code, 'stop_loss', pnl))
                elif pnl >= tp:
                    to_sell.append((code, 'stop_profit', pnl))
                elif h['hold_days'] >= cfg.hold_days_max:
                    to_sell.append((code, 'timeout', pnl))

            sold_codes = set()
            for code, reason, pnl in to_sell:
                if code not in price_data.index:
                    continue
                sp = price_data[code]
                if pd.isna(sp) or sp <= 0:
                    continue
                h = holdings[code]
                sv = h['shares'] * sp * (1 - cost_rate)
                cash += sv
                trade_log.append({'date': str(date.date()), 'code': code, 'action': 'sell',
                                  'reason': reason, 'pnl_pct': round(pnl * 100, 2)})
                sold_codes.add(code)
            for code in sold_codes:
                holdings.pop(code, None)

            # 尾盘选股（T日选，T+1日买）
            if len(holdings) < cfg.max_holdings:
                candidates = select_stocks_tail_pick(
                    factors, date, close_panel, volume_panel, amount_panel,
                    high_panel, low_panel, holdings
                )
                if candidates:
                    vol_ratio = factors['vol_ratio'].loc[date] if date in factors['vol_ratio'].index else None
                    range_ratio = factors['range_ratio'].loc[date] if date in factors['range_ratio'].index else None
                    recent_lu = factors['recent_limit_up'].loc[date] if date in factors['recent_limit_up'].index else None
                    scored = []
                    for code in candidates:
                        vr = vol_ratio.get(code, 999) if vol_ratio is not None else 999
                        rr = range_ratio.get(code, 999) if range_ratio is not None else 999
                        lu = recent_lu.get(code, 0) if recent_lu is not None else 0
                        score = (1.0 / (vr + 0.1)) * 2.0 + (1.0 / (rr + 0.1)) * 1.0 + lu * 0.5
                        scored.append((code, score))
                    scored.sort(key=lambda x: x[1], reverse=True)
                    pending_buy = scored[:cfg.max_daily_buy]

            pv = cash
            for code, h in holdings.items():
                if code in price_data.index:
                    p = price_data[code]
                    if not pd.isna(p) and p > 0:
                        pv += h['shares'] * p
            nav_list.append(pv)

        elapsed = time.time() - t0
        metrics = _calc_metrics(nav_list, trade_log, initial_capital, dates, sl, tp, elapsed, "v20")
        results.append(metrics)
        print(f"夏普={metrics['sharpe']:.3f} 收益={metrics['total_return']:.1f}% 回撤={metrics['max_drawdown']:.1f}% ({elapsed:.0f}s)", flush=True)

    return results

# ═══════════════════════════════════════════════
# v11 扫描
# ═══════════════════════════════════════════════
def scan_v11(sl_list, tp_list, phase_name):
    """v11b 扫描 — 复用 sim_account1 的回测逻辑"""
    from scripts.sim_account1 import (
        load_data as v11_load_data,
        calc_factors as v11_calc_factors,
        select_stocks as v11_select_stocks,
        CONFIG as v11_config,
        run_backtest as v11_run_backtest,
    )

    print(f"\n[v11] {phase_name}: 加载数据 ({FULL_START} ~ {FULL_END})...", flush=True)
    t0 = time.time()
    # v11 用 sim_account1 的数据加载
    close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = \
        v11_load_data(start_date=FULL_START, end_date=FULL_END)
    factors = v11_calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
    print(f"[v11] 数据: {close_panel.shape}, 耗时 {time.time()-t0:.1f}s", flush=True)

    dates = close_panel.index
    initial_capital = v11_config['initial_capital']
    cost_rate = v11_config['commission_rate'] + v11_config['stamp_tax'] + v11_config['slippage_rate']
    total = len(sl_list) * len(tp_list)
    print(f"[v11] {phase_name}: {len(sl_list)}×{len(tp_list)} = {total} 组\n", flush=True)

    results = []
    count = 0
    for sl, tp in [(s, t) for s in sl_list for t in tp_list]:
        count += 1
        t0 = time.time()
        print(f"  [{count}/{total}] SL={sl:.0%} TP={tp:.0%} ...", end=" ", flush=True)

        cash = initial_capital
        holdings = {}
        nav_list = []
        trade_log = []

        for i, date in enumerate(dates):
            if i < 20:
                nav_list.append(initial_capital)
                continue
            if date not in close_panel.index:
                nav_list.append(nav_list[-1] if nav_list else initial_capital)
                continue

            price_data = close_panel.loc[date]
            open_data = open_panel.loc[date] if open_panel is not None else price_data
            for h in holdings.values():
                h['hold_days'] += 1

            to_sell = []
            for code, h in list(holdings.items()):
                if code not in price_data.index:
                    continue
                cp = price_data[code]
                if pd.isna(cp) or cp <= 0:
                    continue
                pnl = (cp - h['cost']) / h['cost']
                if pnl <= -sl:
                    to_sell.append((code, 'stop_loss', pnl))
                elif pnl >= tp:
                    to_sell.append((code, 'stop_profit', pnl))
                elif h['hold_days'] >= v11_config['hold_days_max']:
                    to_sell.append((code, 'timeout', pnl))

            sold_codes = set()
            for code, reason, pnl in to_sell:
                if code not in price_data.index:
                    continue
                sp = price_data[code]
                if pd.isna(sp) or sp <= 0:
                    continue
                h = holdings[code]
                sv = h['shares'] * sp * (1 - cost_rate)
                cash += sv
                trade_log.append({'date': str(date.date()), 'code': code, 'action': 'sell',
                                  'reason': reason, 'pnl_pct': round(pnl * 100, 2)})
                sold_codes.add(code)
            for code in sold_codes:
                holdings.pop(code, None)

            candidates = v11_select_stocks(factors, date, close_panel, volume_panel, amount_panel, holdings)
            if candidates and cash > 0 and len(holdings) < v11_config['max_holdings']:
                available_cash = cash
                n_buy = min(len(candidates), v11_config['max_daily_buy'], v11_config['max_holdings'] - len(holdings))
                per_stock = min(available_cash / n_buy, initial_capital * v11_config['max_position']) if n_buy > 0 else 0
                for code in candidates[:n_buy]:
                    if code not in price_data.index:
                        continue
                    bp = open_data[code] if code in open_data.index else price_data[code]
                    if pd.isna(bp) or bp <= 0:
                        continue
                    adj = bp * (1 + v11_config['commission_rate'] + v11_config['slippage_rate'])
                    shares = int(per_stock / adj / 100) * 100
                    if shares <= 0:
                        continue
                    cost = shares * adj
                    if cost > cash:
                        continue
                    cash -= cost
                    holdings[code] = {'shares': shares, 'cost': bp, 'hold_days': 0}
                    trade_log.append({'date': str(date.date()), 'code': code, 'action': 'buy',
                                      'price': round(bp, 2), 'shares': shares})

            pv = cash
            for code, h in holdings.items():
                if code in price_data.index:
                    p = price_data[code]
                    if not pd.isna(p) and p > 0:
                        pv += h['shares'] * p
            nav_list.append(pv)

        elapsed = time.time() - t0
        metrics = _calc_metrics(nav_list, trade_log, initial_capital, dates, sl, tp, elapsed, "v11")
        results.append(metrics)
        print(f"夏普={metrics['sharpe']:.3f} 收益={metrics['total_return']:.1f}% 回撤={metrics['max_drawdown']:.1f}% ({elapsed:.0f}s)", flush=True)

    return results

# ═══════════════════════════════════════════════
# 打印 & 主流程
# ═══════════════════════════════════════════════
def print_results(results, title):
    df = pd.DataFrame(results).sort_values('sharpe', ascending=False)
    print(f"\n{'='*100}")
    print(f"{title} — 按夏普比率排序")
    print(f"{'='*100}")
    print(f"{'策略':>5} {'止损':>6} {'止盈':>6} {'总收益':>8} {'年化':>8} {'夏普':>7} {'回撤':>8} {'Calmar':>7} {'胜率':>6} {'盈亏比':>7} {'SL':>4} {'TP':>4} {'超时':>4}")
    print("-" * 100)
    for _, row in df.iterrows():
        print(
            f"{row['strategy']:>5} {row['stop_loss']:>5.0%} {row['take_profit']:>5.0%} "
            f"{row['total_return']:>7.1f}% {row['annual_return']:>7.1f}% "
            f"{row['sharpe']:>6.3f} {row['max_drawdown']:>7.1f}% {row['calmar']:>6.3f} "
            f"{row['win_rate']:>5.1f}% {row['profit_loss_ratio']:>6.2f} "
            f"{int(row['stop_loss_trades']):>3} {int(row['stop_profit_trades']):>3} {int(row['timeout_trades']):>3}"
        )
    best = df.iloc[0]
    print(f"\n🏆 最优 (夏普): 止损={best['stop_loss']:.0%} 止盈={best['take_profit']:.0%}")
    print(f"   夏普={best['sharpe']:.3f} 总收益={best['total_return']:.1f}% 回撤={best['max_drawdown']:.1f}%")
    print(f"   胜率={best['win_rate']:.1f}% 盈亏比={best['profit_loss_ratio']:.2f}")
    print(f"   SL触发={int(best['stop_loss_trades'])} TP触发={int(best['stop_profit_trades'])} 超时={int(best['timeout_trades'])}")
    return df

if __name__ == '__main__':
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    coarse_only = '--coarse' in sys.argv
    fine_only = '--fine' in sys.argv

    if fine_only:
        # 细扫描：基于之前粗扫描结果
        prev_path = os.path.join(REPORT_DIR, "20260610_094454_sl_tp_scan.json")
        if not os.path.exists(prev_path):
            print("未找到粗扫描结果，请先运行粗扫描")
            sys.exit(1)
        with open(prev_path) as f:
            prev = json.load(f)
        v13_prev = pd.DataFrame(prev.get('v13', []))
        v20_prev = pd.DataFrame(prev.get('v20', []))
        # ... 细扫描逻辑
        print("细扫描需手动指定参数")
        sys.exit(0)

    # ── 阶段1: 粗扫描 ──
    print("\n" + "█" * 60)
    print("  阶段1: 粗扫描 (完整数据)")
    print("█" * 60)

    v13_coarse = scan_v13(COARSE_SL, COARSE_TP, "粗扫描")
    v13_df = print_results(v13_coarse, "v13 粗扫描")

    v20_coarse = scan_v20(COARSE_SL, COARSE_TP, "粗扫描")
    v20_df = print_results(v20_coarse, "v20 粗扫描")

    # v11 暂不参与扫描（架构不同，需单独处理）
    v11_df = pd.DataFrame()

    if not coarse_only:
        # ── 阶段2: 细扫描 ──
        print("\n" + "█" * 60)
        print("  阶段2: 细扫描")
        print("█" * 60)

        # v13 细扫描：基于粗扫描最优
        best_v13 = v13_df.iloc[0]
        fine_sl_v13 = sorted(list(set([max(0.01, best_v13['stop_loss'] - 0.005),
                                       best_v13['stop_loss'],
                                       min(0.08, best_v13['stop_loss'] + 0.005)])))
        fine_tp_v13 = sorted(list(set([max(0.02, best_v13['take_profit'] - 0.01),
                                       best_v13['take_profit'],
                                       min(0.15, best_v13['take_profit'] + 0.01)])))
        print(f"\n📌 v13 细扫描: SL={fine_sl_v13}, TP={fine_tp_v13}")
        v13_fine = scan_v13(fine_sl_v13, fine_tp_v13, "细扫描")
        v13_df2 = print_results(v13_fine, "v13 细扫描")

        # v20 细扫描
        best_v20 = v20_df.iloc[0]
        fine_sl_v20 = sorted(list(set([max(0.01, best_v20['stop_loss'] - 0.005),
                                       best_v20['stop_loss'],
                                       min(0.08, best_v20['stop_loss'] + 0.005)])))
        fine_tp_v20 = sorted(list(set([max(0.02, best_v20['take_profit'] - 0.01),
                                       best_v20['take_profit'],
                                       min(0.15, best_v20['take_profit'] + 0.01)])))
        print(f"\n📌 v20 细扫描: SL={fine_sl_v20}, TP={fine_tp_v20}")
        v20_fine = scan_v20(fine_sl_v20, fine_tp_v20, "细扫描")
        v20_df2 = print_results(v20_fine, "v20 细扫描")

        # v11 细扫描 — 跳过（架构不同）

        # 合并
        v13_all = pd.concat([v13_df, v13_df2]).drop_duplicates(subset=['stop_loss', 'take_profit']).sort_values('sharpe', ascending=False)
        v20_all = pd.concat([v20_df, v20_df2]).drop_duplicates(subset=['stop_loss', 'take_profit']).sort_values('sharpe', ascending=False)
        v11_all = pd.DataFrame()
    else:
        v13_all = v13_df
        v20_all = v20_df
        v11_all = pd.DataFrame()

    # ── 汇总 ──
    print("\n" + "█" * 60)
    print("  最终汇总")
    print("█" * 60)
    for name, df in [("v13", v13_all), ("v20", v20_all)]:
        if df.empty:
            continue
        best = df.iloc[0]
        print(f"\n  {name}: 最优 SL={best['stop_loss']:.0%} TP={best['take_profit']:.0%}")
        print(f"       夏普={best['sharpe']:.3f} 收益={best['total_return']:.1f}% 回撤={best['max_drawdown']:.1f}% 胜率={best['win_rate']:.1f}%")

    # 保存
    out = {"scan_time": ts, "data_range": f"{FULL_START} ~ {FULL_END}",
           "v13": v13_all.to_dict('records'), "v20": v20_all.to_dict('records')}
    out_path = os.path.join(REPORT_DIR, f"{ts}_sl_tp_full_scan.json")
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    csv_path = os.path.join(REPORT_DIR, f"{ts}_sl_tp_full_scan.csv")
    pd.concat([v13_all, v20_all]).to_csv(csv_path, index=False)
    print(f"\n结果已保存: {out_path}")
    print(f"CSV 已保存: {csv_path}")
