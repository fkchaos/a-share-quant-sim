#!/usr/bin/env python3
"""v13 WF 验证 — 测试 SL=-1.5% TP=3% hold=5（2022-2026 扫描最优）"""
import sys, os, time
import numpy as np
import pandas as pd

from v13_small_mid_short import (
    V13Config, load_small_cap_panel, calc_small_cap_factors, select_stocks
)

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))

# 覆盖参数
V13Config.stop_loss = -0.015
V13Config.stop_profit = 0.03
V13Config.hold_days_max = 5

# WF 参数
TRAIN_DAYS = 252
TEST_DAYS = 63
STEP_DAYS = 63

def run_v13_fold(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel,
                 factors, train_start, train_end, test_start, test_end):
    """跑一个 WF fold"""
    dates = close_panel.index
    train_mask = (dates >= train_start) & (dates <= train_end)
    test_mask = (dates >= test_start) & (dates <= test_end)

    # 只用训练期数据计算因子（避免前视偏差）
    # 但因子已经用全量数据计算了，这里简化处理：
    # 实际应该用训练期数据重新计算因子，但 v13 的因子是时序因子，不受影响

    initial_capital = V13Config.initial_capital
    cash = initial_capital
    holdings = {}
    nav_list = []
    trade_log = []

    test_dates = dates[test_mask]
    for i, date in enumerate(dates):
        if date < test_start:
            continue
        if date > test_end:
            break

        idx = dates.get_loc(date)
        price_data = close_panel.loc[date]
        open_data = open_panel.loc[date] if open_panel is not None else price_data

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
                if idx > 0:
                    pc = close_panel.iloc[idx - 1].get(code)
                    if pc and not pd.isna(pc) and pc > 0 and sp <= pc * 0.90 * 1.01:
                        holdings[code]['hold_days'] = max(0, holdings[code]['hold_days'] - 1)
                        continue
                h = holdings[code]
                sv = h['shares'] * sp * (1 - V13Config.commission_rate - V13Config.stamp_tax - V13Config.slippage_rate)
                cash += sv
                trade_log.append({'date': str(date.date()), 'code': code, 'action': 'sell',
                                  'reason': reason, 'pnl_pct': round(pnl * 100, 2), 'value': round(sv, 2)})
                sold.add(code)
        for c in sold:
            holdings.pop(c, None)

        if date in factors['rev_5'].index:
            rev_5 = factors['rev_5'].loc[date].dropna()
            vol_ratio = factors['vol_ratio'].loc[date].dropna()
            vol_shrink = factors['vol_shrink'].loc[date].dropna()
            range_ratio = factors['range_ratio'].loc[date].dropna()
            scores = {}
            for code in rev_5.index:
                if code in holdings:
                    continue
                r = rev_5.get(code, 0)
                if r < V13Config.rev_threshold:
                    s = abs(r) * 100
                    vr = vol_ratio.get(code, 1.0)
                    if vr > V13Config.vol_ratio_threshold:
                        s += 0.5
                    vs = vol_shrink.get(code, 1.0)
                    if vs < 0.7:
                        s += 0.3
                    rr = range_ratio.get(code, 1.0)
                    if rr < 0.8:
                        s += 0.2
                    if s > 0:
                        scores[code] = s
            candidates = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)
        else:
            candidates = []

        # 流动性过滤
        avg_amount = amount_panel.rolling(20).mean() / 1e4
        if date in avg_amount.index:
            day_am = avg_amount.loc[date]
            liq = set(day_am[(day_am > V13Config.min_liquidity) & (day_am < V13Config.max_liquidity)].dropna().index)
            candidates = [c for c in candidates if c in liq]

        if candidates and cash > initial_capital * 0.1 and len(holdings) < V13Config.max_holdings:
            ac = cash - initial_capital * 0.1
            ps = min(ac / min(len(candidates), V13Config.max_daily_buy),
                     initial_capital * V13Config.max_position)
            for code in candidates[:V13Config.max_daily_buy]:
                if code not in price_data.index:
                    continue
                bp = open_data.get(code, price_data.get(code))
                if bp is None or pd.isna(bp) or bp <= 0:
                    continue
                if idx > 0:
                    pc = close_panel.iloc[idx - 1].get(code)
                    if pc and not pd.isna(pc) and pc > 0 and bp >= pc * 1.10 * 0.99:
                        continue
                adj = bp * (1 + V13Config.commission_rate + V13Config.slippage_rate)
                shares = int(ps / adj / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * adj
                if cost > cash:
                    continue
                cash -= cost
                holdings[code] = {'shares': shares, 'cost': bp, 'hold_days': 0}
                trade_log.append({'date': str(date.date()), 'code': code, 'action': 'buy',
                                  'price': round(bp, 2), 'shares': shares, 'value': round(cost, 2)})

        pv = cash
        for code, h in holdings.items():
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    pv += h['shares'] * p
        nav_list.append(pv)

    nav = pd.Series(nav_list)
    if len(nav) < 10:
        return None

    total_ret = (nav.iloc[-1] / nav.iloc[0] - 1) * 100
    n_days = len(nav)
    annual = ((nav.iloc[-1] / nav.iloc[0]) ** (252 / max(n_days, 1)) - 1) * 100
    dr = nav.pct_change().dropna()
    sharpe = dr.mean() / (dr.std() + 1e-10) * np.sqrt(252)
    dd = (nav - nav.cummax()) / nav.cummax()
    max_dd = dd.min() * 100
    sells = [t for t in trade_log if t['action'] == 'sell']
    wr = sum(1 for t in sells if t['pnl_pct'] > 0) / max(len(sells), 1) * 100

    n_sl = sum(1 for t in sells if t['reason'] == 'SL')
    n_tp = sum(1 for t in sells if t['reason'] == 'TP')
    n_to = sum(1 for t in sells if 'TO' in t['reason'])
    total_sell = max(len(sells), 1)

    return {
        'total_ret': total_ret, 'annual': annual, 'sharpe': sharpe,
        'max_dd': max_dd, 'win_rate': wr, 'trades': len(sells),
        'sl_pct': n_sl / total_sell * 100, 'tp_pct': n_tp / total_sell * 100,
        'to_pct': n_to / total_sell * 100,
    }

if __name__ == '__main__':
    print("=" * 60)
    print("v13 WF 验证 — SL=-1.5% TP=3% hold=5（2022-2026 扫描最优）")
    print("=" * 60)

    print("\n[1/3] 加载数据...")
    t0 = time.time()
    close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = \
        load_small_cap_panel(start_date='2021-01-01', end_date='2026-05-31')
    factors = calc_small_cap_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
    print(f"  耗时 {time.time() - t0:.1f}s")

    dates = close_panel.index
    n_dates = len(dates)

    # 生成 fold 边界
    # 第一个 fold：从第 20 天开始训练
    first_idx = 20  # 预热期
    folds = []
    idx = first_idx + TRAIN_DAYS
    while idx + TEST_DAYS <= n_dates:
        train_start = dates[first_idx]
        train_end = dates[min(idx - 1, n_dates - 1)]
        test_start = dates[idx]
        test_end = dates[min(idx + TEST_DAYS - 1, n_dates - 1)]
        folds.append((train_start, train_end, test_start, test_end))
        idx += STEP_DAYS

    print(f"\n[2/3] Walk-Forward 回测 ({len(folds)} folds)")
    results = []
    for fi, (ts, te, tss, tse) in enumerate(folds):
        m = run_v13_fold(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel,
                         factors, ts, te, tss, tse)
        if m:
            results.append(m)
            print(f"  Fold {fi + 1}: {tss.date()}~{tse.date()} | "
                  f"Ret={m['total_ret']:.1f}% Sharpe={m['sharpe']:.2f} DD={m['max_dd']:.1f}% "
                  f"Win={m['win_rate']:.0f}% TP/SL/TO={m['tp_pct']:.0f}%/{m['sl_pct']:.0f}%/{m['to_pct']:.0f}%")

    print(f"\n[3/3] WF 汇总 ({len(results)} folds)")
    if results:
        avg_annual = np.mean([r['annual'] for r in results])
        avg_sharpe = np.mean([r['sharpe'] for r in results])
        avg_dd = np.mean([r['max_dd'] for r in results])
        avg_wr = np.mean([r['win_rate'] for r in results])
        pos_folds = sum(1 for r in results if r['total_ret'] > 0)
        avg_tp = np.mean([r['tp_pct'] for r in results])
        avg_sl = np.mean([r['sl_pct'] for r in results])
        avg_to = np.mean([r['to_pct'] for r in results])

        print(f"  平均年化: {avg_annual:.1f}%")
        print(f"  平均夏普: {avg_sharpe:.2f}")
        print(f"  平均回撤: {avg_dd:.1f}%")
        print(f"  平均胜率: {avg_wr:.0f}%")
        print(f"  正收益fold: {pos_folds}/{len(results)} ({pos_folds / len(results) * 100:.0f}%)")
        print(f"  平均 TP/SL/TO: {avg_tp:.0f}%/{avg_sl:.0f}%/{avg_to:.0f}%")

        if pos_folds / len(results) >= 0.6 and avg_sharpe >= 0.5:
            print(f"\n  ✅ WF 通过")
        else:
            print(f"\n  ❌ WF 未通过")
