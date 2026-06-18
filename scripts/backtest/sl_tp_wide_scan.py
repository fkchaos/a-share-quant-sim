#!/usr/bin/env python3
"""
sl_tp_wide_scan.py — 宽范围止盈止损扫描
SL: 2%~10% (步长2%), TP: 5%~25% (步长5%)
"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime

sys.path.insert(0, os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.environ.get('PROJECT_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))

DATA_DIR = os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")
os.makedirs(REPORT_DIR, exist_ok=True)

FULL_START = "2021-01-01"
FULL_END = "2026-05-29"

# ── 宽网格 ──
WIDE_SL = [0.02, 0.03, 0.05, 0.07, 0.10]
WIDE_TP = [0.05, 0.10, 0.15, 0.20, 0.25]

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

    sl_count = len([t for t in sells if t.get('reason') == 'stop_loss'])
    tp_count = len([t for t in sells if t.get('reason') == 'stop_profit'])
    to_count = len([t for t in sells if t.get('reason') == 'timeout'])

    return {
        'strategy': strategy_name,
        'stop_loss': sl, 'take_profit': tp,
        'total_return': round(total_ret * 100, 2),
        'annual_return': round(ann_ret * 100, 2),
        'annual_vol': round(ann_vol * 100, 2),
        'sharpe': round(sharpe, 3),
        'max_drawdown': round(max_dd * 100, 2),
        'calmar': round(calmar, 3),
        'win_rate': round(win_rate, 1),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'profit_loss_ratio': round(pl_ratio, 2),
        'sl_count': sl_count, 'tp_count': tp_count, 'to_count': to_count,
        'elapsed_sec': round(elapsed, 1),
    }


def scan_v13():
    from scripts.v13_small_mid_short import (
        load_small_cap_panel, calc_small_cap_factors, select_stocks, V13Config,
    )
    cfg = V13Config()
    print(f"\n[v13] 加载数据 ({FULL_START} ~ {FULL_END})...", flush=True)
    t0 = time.time()
    close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = \
        load_small_cap_panel(start_date=FULL_START, end_date=FULL_END)
    factors = calc_small_cap_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
    print(f"[v13] 数据: {close_panel.shape}, 耗时 {time.time()-t0:.1f}s", flush=True)

    dates = close_panel.index
    initial_capital = cfg.initial_capital
    cost_rate = cfg.commission_rate + cfg.stamp_tax + cfg.slippage_rate
    total = len(WIDE_SL) * len(WIDE_TP)
    print(f"[v13] 宽扫描: {len(WIDE_SL)}×{len(WIDE_TP)} = {total} 组\n", flush=True)

    results = []
    count = 0
    for sl in WIDE_SL:
        for tp in WIDE_TP:
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
                    if code not in price_data.index: continue
                    cp = price_data[code]
                    if pd.isna(cp) or cp <= 0: continue
                    pnl = (cp - h['cost']) / h['cost']
                    if pnl <= -sl: to_sell.append((code, 'stop_loss', pnl))
                    elif pnl >= tp: to_sell.append((code, 'stop_profit', pnl))
                    elif h['hold_days'] >= cfg.hold_days_max:
                        to_sell.append((code, 'timeout', pnl))

                sold_codes = set()
                for code, reason, pnl in to_sell:
                    sp = price_data[code]
                    if pd.isna(sp) or sp <= 0: continue
                    h = holdings[code]
                    sv = h['shares'] * sp * (1 - cost_rate)
                    cash += sv
                    trade_log.append({'date': str(date.date()), 'code': code, 'action': 'sell',
                                      'reason': reason, 'pnl_pct': round(pnl * 100, 2)})
                    sold_codes.add(code)
                for code in sold_codes: holdings.pop(code, None)

                candidates = select_stocks(factors, date, close_panel, volume_panel, amount_panel, holdings)
                if candidates and cash > 0 and len(holdings) < cfg.max_holdings:
                    available_cash = cash
                    n_buy = min(len(candidates), cfg.max_daily_buy, cfg.max_holdings - len(holdings))
                    per_stock = min(available_cash / n_buy, initial_capital * cfg.max_position) if n_buy > 0 else 0
                    for code in candidates[:n_buy]:
                        if code not in open_data.index: continue
                        bp = open_data[code]
                        if pd.isna(bp) or bp <= 0: continue
                        adj = bp * (1 + cfg.commission_rate + cfg.slippage_rate)
                        shares = int(per_stock / adj / 100) * 100
                        if shares <= 0: continue
                        cost_val = shares * adj
                        if cost_val > cash: continue
                        cash -= cost_val
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
            m = _calc_metrics(nav_list, trade_log, initial_capital, dates, sl, tp, elapsed, "v13")
            results.append(m)
            print(f"夏普={m['sharpe']:.3f} 收益={m['total_return']:.1f}% 回撤={m['max_drawdown']:.1f}% ({elapsed:.0f}s)", flush=True)

    df = pd.DataFrame(results).sort_values('sharpe', ascending=False)
    print(f"\n{'='*100}")
    print(f"v13 宽扫描 — 按夏普排序 (SL: 2~10%, TP: 5~25%)")
    print(f"{'='*100}")
    print(f"{'策略':>4}  {'止损':>4}  {'止盈':>4}  {'总收益':>8}  {'年化':>6}  {'夏普':>6}  {'回撤':>7}  {'Calmar':>6}  {'胜率':>5}  {'盈亏比':>6}  {'SL':>4}  {'TP':>4}  {'超时':>4}")
    print(f"{'-'*100}")
    for _, r in df.iterrows():
        print(f"  {r['strategy']:>2}  {r['stop_loss']:>4.0%}  {r['take_profit']:>4.0%}  {r['total_return']:>8.1f}%  {r['annual_return']:>6.1f}%  {r['sharpe']:>6.3f}  {r['max_drawdown']:>7.1f}%  {r['calmar']:>6.2f}  {r['win_rate']:>5.1f}%  {r['profit_loss_ratio']:>6.2f}  {r['sl_count']:>4}  {r['tp_count']:>4}  {r['to_count']:>4}")

    best = df.iloc[0]
    print(f"\n🏆 最优 (夏普): 止损={best['stop_loss']:.0%} 止盈={best['take_profit']:.0%}")
    print(f"   夏普={best['sharpe']:.3f} 总收益={best['total_return']:.1f}% 回撤={best['max_drawdown']:.1f}%")
    print(f"   胜率={best['win_rate']:.1f}% 盈亏比={best['profit_loss_ratio']:.2f}")
    print(f"   SL触发={best['sl_count']} TP触发={best['tp_count']} 超时={best['to_count']}")
    return df


def scan_v20():
    from scripts.v20_tail_pick import (
        load_panel, calc_tail_pick_factors, V20Config,
    )
    from scripts.v20_tail_pick import select_stocks_tail_pick as v20_select_stocks

    cfg = V20Config()
    print(f"\n[v20] 加载数据 ({FULL_START} ~ {FULL_END})...", flush=True)
    t0 = time.time()
    close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = \
        load_panel(start_date=FULL_START, end_date=FULL_END)
    factors = calc_tail_pick_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
    print(f"[v20] 数据: {close_panel.shape}, 耗时 {time.time()-t0:.1f}s", flush=True)

    dates = close_panel.index
    initial_capital = cfg.initial_capital
    cost_rate = cfg.commission_rate + cfg.stamp_tax + cfg.slippage_rate
    total = len(WIDE_SL) * len(WIDE_TP)
    print(f"[v20] 宽扫描: {len(WIDE_SL)}×{len(WIDE_TP)} = {total} 组\n", flush=True)

    results = []
    count = 0
    for sl in WIDE_SL:
        for tp in WIDE_TP:
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

                # 执行待买入
                if pending_buy and cash > 0 and len(holdings) < cfg.max_holdings:
                    available_cash = cash
                    n_buy = min(len(pending_buy), cfg.max_daily_buy, cfg.max_holdings - len(holdings))
                    per_stock = min(available_cash / n_buy, initial_capital * cfg.max_position) if n_buy > 0 else 0
                    for code in pending_buy[:n_buy]:
                        if code not in open_data.index: continue
                        bp = open_data[code]
                        if pd.isna(bp) or bp <= 0: continue
                        adj = bp * (1 + cfg.commission_rate + cfg.slippage_rate)
                        shares = int(per_stock / adj / 100) * 100
                        if shares <= 0: continue
                        cost_val = shares * adj
                        if cost_val > cash: continue
                        cash -= cost_val
                        holdings[code] = {'shares': shares, 'cost': bp, 'hold_days': 0, 'buy_date': date}
                        trade_log.append({'date': str(date.date()), 'code': code, 'action': 'buy',
                                          'price': round(bp, 2), 'shares': shares})
                pending_buy = []

                for h in holdings.values():
                    h['hold_days'] += 1

                to_sell = []
                for code, h in list(holdings.items()):
                    if code not in price_data.index: continue
                    cp = price_data[code]
                    if pd.isna(cp) or cp <= 0: continue
                    pnl = (cp - h['cost']) / h['cost']
                    if pnl <= -sl: to_sell.append((code, 'stop_loss', pnl))
                    elif pnl >= tp: to_sell.append((code, 'stop_profit', pnl))
                    elif h['hold_days'] >= cfg.hold_days_max:
                        to_sell.append((code, 'timeout', pnl))

                sold_codes = set()
                for code, reason, pnl in to_sell:
                    sp = price_data[code]
                    if pd.isna(sp) or sp <= 0: continue
                    h = holdings[code]
                    sv = h['shares'] * sp * (1 - cost_rate)
                    cash += sv
                    trade_log.append({'date': str(date.date()), 'code': code, 'action': 'sell',
                                      'reason': reason, 'pnl_pct': round(pnl * 100, 2)})
                    sold_codes.add(code)
                for code in sold_codes: holdings.pop(code, None)

                # T日选股，T+1日买
                c = v20_select_stocks(factors, date, close_panel, volume_panel, amount_panel, high_panel, low_panel, holdings)
                if c:
                    pending_buy = c

                pv = cash
                for code, h in holdings.items():
                    if code in price_data.index:
                        p = price_data[code]
                        if not pd.isna(p) and p > 0:
                            pv += h['shares'] * p
                nav_list.append(pv)

            elapsed = time.time() - t0
            m = _calc_metrics(nav_list, trade_log, initial_capital, dates, sl, tp, elapsed, "v20")
            results.append(m)
            print(f"夏普={m['sharpe']:.3f} 收益={m['total_return']:.1f}% 回撤={m['max_drawdown']:.1f}% ({elapsed:.0f}s)", flush=True)

    df = pd.DataFrame(results).sort_values('sharpe', ascending=False)
    print(f"\n{'='*100}")
    print(f"v20 宽扫描 — 按夏普排序 (SL: 2~10%, TP: 5~25%)")
    print(f"{'='*100}")
    print(f"{'策略':>4}  {'止损':>4}  {'止盈':>4}  {'总收益':>8}  {'年化':>6}  {'夏普':>6}  {'回撤':>7}  {'Calmar':>6}  {'胜率':>5}  {'盈亏比':>6}  {'SL':>4}  {'TP':>4}  {'超时':>4}")
    print(f"{'-'*100}")
    for _, r in df.iterrows():
        print(f"  {r['strategy']:>2}  {r['stop_loss']:>4.0%}  {r['take_profit']:>4.0%}  {r['total_return']:>8.1f}%  {r['annual_return']:>6.1f}%  {r['sharpe']:>6.3f}  {r['max_drawdown']:>7.1f}%  {r['calmar']:>6.2f}  {r['win_rate']:>5.1f}%  {r['profit_loss_ratio']:>6.2f}  {r['sl_count']:>4}  {r['tp_count']:>4}  {r['to_count']:>4}")

    best = df.iloc[0]
    print(f"\n🏆 最优 (夏普): 止损={best['stop_loss']:.0%} 止盈={best['take_profit']:.0%}")
    print(f"   夏普={best['sharpe']:.3f} 总收益={best['total_return']:.1f}% 回撤={best['max_drawdown']:.1f}%")
    print(f"   胜率={best['win_rate']:.1f}% 盈亏比={best['profit_loss_ratio']:.2f}")
    print(f"   SL触发={best['sl_count']} TP触发={best['tp_count']} 超时={best['to_count']}")
    return df


if __name__ == '__main__':
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    print("█" * 60)
    print("  宽范围 SL/TP 扫描")
    print(f"  SL: {[f'{x:.0%}' for x in WIDE_SL]}")
    print(f"  TP: {[f'{x:.0%}' for x in WIDE_TP]}")
    print("█" * 60)

    v13_df = scan_v13()
    v20_df = scan_v20()

    # 保存
    out = {
        'v13': v13_df.to_dict('records'),
        'v20': v20_df.to_dict('records'),
        'params': {'sl': WIDE_SL, 'tp': WIDE_TP},
    }
    path = os.path.join(REPORT_DIR, f"{ts}_sl_tp_wide_scan.json")
    with open(path, 'w') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    v13_df.to_csv(path.replace('.json', '_v13.csv'), index=False)
    v20_df.to_csv(path.replace('.json', '_v20.csv'), index=False)

    print(f"\n{'█'*60}")
    print(f"  最终汇总")
    print(f"{'█'*60}")
    print(f"\n  v13: 最优 SL={v13_df.iloc[0]['stop_loss']:.0%} TP={v13_df.iloc[0]['take_profit']:.0%}")
    print(f"       夏普={v13_df.iloc[0]['sharpe']:.3f} 收益={v13_df.iloc[0]['total_return']:.1f}% 回撤={v13_df.iloc[0]['max_drawdown']:.1f}%")
    print(f"\n  v20: 最优 SL={v20_df.iloc[0]['stop_loss']:.0%} TP={v20_df.iloc[0]['take_profit']:.0%}")
    print(f"       夏普={v20_df.iloc[0]['sharpe']:.3f} 收益={v20_df.iloc[0]['total_return']:.1f}% 回撤={v20_df.iloc[0]['max_drawdown']:.1f}%")
    print(f"\n结果: {path}")
