#!/usr/bin/env python3
"""v20 Walk-Forward 过拟合检测"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime

from v20_tail_pick import (
    V20Config, load_panel, calc_tail_pick_factors, select_stocks_tail_pick,
)

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")

def run_v20_fold(sub_close, sub_volume, sub_amount, sub_high, sub_low, sub_open,
                 warmup_days=20, label="v20_fold"):
    """跑单个 fold 的回测"""
    factors = calc_tail_pick_factors(sub_close, sub_volume, sub_amount, sub_high, sub_low)

    cfg = V20Config()
    initial_capital = cfg.initial_capital
    cash = initial_capital
    holdings = {}
    nav_list = []
    trade_log = []
    dates = sub_close.index
    pending_buy = []

    for i, date in enumerate(dates):
        if i < warmup_days:
            nav_list.append(initial_capital)
            continue
        if date not in sub_close.index:
            nav_list.append(nav_list[-1] if nav_list else initial_capital)
            continue

        price_data = sub_close.loc[date]
        open_data = sub_open.loc[date] if sub_open is not None else price_data

        # 1. 执行待买入
        if pending_buy and cash > initial_capital * 0.1 and len(holdings) < cfg.max_holdings:
            available_cash = cash - initial_capital * 0.1
            n_buy = min(len(pending_buy), cfg.max_daily_buy, cfg.max_holdings - len(holdings))
            per_stock = available_cash / n_buy if n_buy > 0 else 0
            per_stock = min(per_stock, initial_capital * cfg.max_position)

            for code, score in pending_buy[:n_buy]:
                if code not in open_data.index:
                    continue
                buy_price = open_data[code]
                if pd.isna(buy_price) or buy_price <= 0:
                    continue
                if i > 0:
                    prev_close = sub_close.iloc[i-1].get(code, None) if code in sub_close.columns else None
                    if prev_close and not pd.isna(prev_close) and prev_close > 0:
                        if buy_price >= prev_close * 1.10 * 0.99:
                            continue
                adj = buy_price * (1 + cfg.commission_rate + cfg.slippage_rate)
                shares = int(per_stock / adj / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * adj
                if cost > cash:
                    continue
                cash -= cost
                holdings[code] = {'shares': shares, 'cost': buy_price, 'hold_days': 0}
                trade_log.append({'date': str(date.date()), 'code': code, 'action': 'buy',
                                  'price': round(buy_price, 2), 'shares': shares})

        pending_buy = []

        # 2. 更新持仓
        for code in holdings:
            holdings[code]['hold_days'] += 1

        # 3. 风控
        to_sell = []
        for code, h in list(holdings.items()):
            if code not in price_data.index:
                continue
            cp = price_data[code]
            if pd.isna(cp) or cp <= 0:
                continue
            pnl_pct = (cp - h['cost']) / h['cost']
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
                    prev_close = sub_close.iloc[i-1].get(code, None) if code in sub_close.columns else None
                    if prev_close and not pd.isna(prev_close) and prev_close > 0:
                        if sell_price <= prev_close * 0.90 * 1.01:
                            holdings[code]['hold_days'] = max(0, holdings[code]['hold_days'] - 1)
                            continue
                h = holdings[code]
                sv = h['shares'] * sell_price * (1 - cfg.commission_rate - cfg.stamp_tax - cfg.slippage_rate)
                cash += sv
                trade_log.append({'date': str(date.date()), 'code': code, 'action': 'sell',
                                  'reason': reason, 'pnl_pct': round(pnl_pct * 100, 2)})
                sold_codes.add(code)
        for code in sold_codes:
            holdings.pop(code, None)

        # 4. 选股
        if len(holdings) < cfg.max_holdings:
            candidates = select_stocks_tail_pick(
                factors, date, sub_close, sub_volume, sub_amount, sub_high, sub_low, holdings
            )
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
        pv = cash
        for code, h in holdings.items():
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    pv += h['shares'] * p
        nav_list.append(pv)

    nav = pd.Series(nav_list, index=dates[:len(nav_list)])
    rets = nav.pct_change().dropna()
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    days = max((nav.index[-1] - nav.index[0]).days, 1)
    years = days / 365
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = rets.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    peak = nav.cummax()
    max_dd = ((nav - peak) / peak).min()
    sells = [t for t in trade_log if t['action'] == 'sell']
    wins = [t for t in sells if t.get('pnl_pct', 0) > 0]
    win_rate = len(wins) / len(sells) * 100 if sells else 0

    return {
        'annual_return': ann_ret, 'sharpe': sharpe, 'max_dd': max_dd,
        'win_rate': win_rate, 'total_trades': len(trade_log),
    }

def main():
    print("=" * 60)
    print("v20_tail_pick — Walk-Forward 过拟合检测")
    print("=" * 60)

    print("\n[1/3] 加载数据...")
    t0 = time.time()
    close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = load_panel()
    print(f"  Panel: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只")
    print(f"  耗时 {time.time()-t0:.1f}s")

    dates = close_panel.index
    n = len(dates)
    train_days, test_days, step_days = 252, 63, 63

    fold_results = []
    fold = 0
    train_end = train_days

    print(f"\n[2/3] Walk-Forward 回测 (train={train_days}d, test={test_days}d, step={step_days}d)")

    while train_end + test_days <= n:
        fold += 1
        train_start = max(0, train_end - train_days)
        test_start = train_end
        test_end = min(n, train_end + test_days)

        window_dates = dates[train_start:test_end]
        sub_close = close_panel.loc[window_dates]
        sub_volume = volume_panel.loc[window_dates]
        sub_amount = amount_panel.loc[window_dates]
        sub_high = high_panel.loc[window_dates]
        sub_low = low_panel.loc[window_dates]
        sub_open = open_panel.loc[window_dates]
        warmup = train_end - train_start

        m = run_v20_fold(sub_close, sub_volume, sub_amount, sub_high, sub_low, sub_open,
                          warmup_days=warmup, label=f"v20_fold{fold}")

        test_start_date = dates[test_start].date()
        test_end_date = dates[test_end - 1].date()

        fold_results.append({
            'fold': fold,
            'test_period': f"{test_start_date}~{test_end_date}",
            'ann_return': m['annual_return'],
            'sharpe': m['sharpe'],
            'max_dd': m['max_dd'],
            'trades': m['total_trades'],
            'win_rate': m['win_rate'],
        })

        print(f"  Fold {fold}: {test_start_date}~{test_end_date} | "
              f"Ret={m['annual_return']:.1%} Sharpe={m['sharpe']:.2f} "
              f"DD={m['max_dd']:.1%} Win={m['win_rate']:.0f}%")

        train_end += step_days

    print(f"\n[3/3] WF 汇总 ({len(fold_results)} folds)")
    print(f"{'─' * 70}")

    positive_folds = sum(1 for r in fold_results if r['ann_return'] > 0)
    avg_ret = np.mean([r['ann_return'] for r in fold_results])
    avg_sharpe = np.mean([r['sharpe'] for r in fold_results])
    avg_maxdd = np.mean([r['max_dd'] for r in fold_results])
    avg_winrate = np.mean([r['win_rate'] for r in fold_results])

    print(f"  平均年化:   {avg_ret:.1%}")
    print(f"  平均夏普:   {avg_sharpe:.2f}")
    print(f"  平均MaxDD:  {avg_maxdd:.1%}")
    print(f"  平均胜率:   {avg_winrate:.0f}%")
    print(f"  正收益fold: {positive_folds}/{len(fold_results)} ({positive_folds/len(fold_results):.0%})")

    return fold_results

if __name__ == '__main__':
    main()
