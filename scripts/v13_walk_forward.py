#!/usr/bin/env python3
"""
v13_small_mid_short — Walk-Forward 过拟合检测
==============================================
滑动窗口：训练期 252 天 → 测试期 63 天，步长 63 天

用法：
    python scripts/v13_walk_forward.py
    python scripts/v13_walk_forward.py --train-days 252 --test-days 63 --step-days 63
"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from scripts.v13_small_mid_short import (
    V13Config,
    load_small_cap_panel,
    calc_small_cap_factors,
)

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")


def run_v13_fold(close_panel, volume_panel, amount_panel, high_panel, low_panel,
                 open_panel, warmup_days=20, label="v13_fold"):
    """跑单个 fold 的回测（与 v13 主回测逻辑一致）"""
    factors = calc_small_cap_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)

    cfg = V13Config()
    initial_capital = cfg.initial_capital
    cash = initial_capital
    holdings = {}
    nav_list = []
    trade_log = []
    dates = close_panel.index

    for i, date in enumerate(dates):
        if i < warmup_days:
            nav_list.append(initial_capital)
            continue

        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else initial_capital)
            continue

        price_data = close_panel.loc[date]
        open_data = open_panel.loc[date] if open_panel is not None else price_data

        # 0. 市场趋势判断
        if i >= 60:
            market_20ma = close_panel.iloc[i-20:i].mean().mean()
            market_60ma = close_panel.iloc[i-60:i].mean().mean()
            ma_ratio = market_20ma / market_60ma if market_60ma > 0 else 1.0
            if ma_ratio > 1.02:
                position_scale = 1.0
            elif ma_ratio > 0.98:
                position_scale = 0.5
            else:
                position_scale = 0.3
        else:
            position_scale = 1.0

        # 1. 更新持仓天数
        for code in holdings:
            holdings[code]['hold_days'] += 1

        # 2. 风控检查（止损/止盈/超时）
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
            elif pnl_pct >= cfg.stop_profit:
                to_sell.append((code, 'stop_profit', pnl_pct))
            elif h['hold_days'] >= cfg.hold_days_max:
                to_sell.append((code, 'timeout', pnl_pct))

        # 执行卖出
        sold_codes = set()
        for code, reason, pnl_pct in to_sell:
            if code not in price_data.index:
                continue
            sell_price = price_data[code]
            if pd.isna(sell_price) or sell_price <= 0:
                continue
            # 跌停检查
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
        candidates = _select_stocks_inline(factors, date, close_panel, volume_panel, amount_panel, holdings)

        # 4. 买入（结合市场趋势调整仓位）
        if candidates and cash > initial_capital * 0.1 and len(holdings) < cfg.max_holdings:
            if position_scale > 0:
                available_cash = cash - initial_capital * 0.1
                max_pos = initial_capital * cfg.max_position * position_scale
                per_stock = min(available_cash / min(len(candidates), cfg.max_daily_buy), max_pos)
            for code in candidates[:cfg.max_daily_buy]:
                if code not in price_data.index:
                    continue
                buy_price = open_data.get(code, price_data.get(code, None))
                if buy_price is None or pd.isna(buy_price) or buy_price <= 0:
                    continue
                # 涨停检查
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
    metrics = _calc_fold_metrics(nav, trade_log, initial_capital)
    return metrics, nav, trade_log


def _select_stocks_inline(factors, date, close_panel, volume_panel, amount_panel, holdings):
    """内联选股（从 select_stocks 复制，避免循环导入）"""
    if date not in factors['rev_5'].index:
        return []
    avg_amount = amount_panel.rolling(20).mean() / 1e4
    if date in avg_amount.index:
        day_amount = avg_amount.loc[date]
        liquid_mask = (day_amount > V13Config.min_liquidity) & (day_amount < V13Config.max_liquidity)
        liquid_stocks = set(day_amount[liquid_mask].dropna().index)
    else:
        liquid_stocks = set(close_panel.columns)

    try:
        rev_5 = factors['rev_5'].loc[date].dropna()
        vol_ratio = factors['vol_ratio'].loc[date].dropna()
        vol_shrink = factors['vol_shrink'].loc[date].dropna()
        range_ratio = factors['range_ratio'].loc[date].dropna()
    except KeyError:
        return []

    cond1 = rev_5[rev_5 < V13Config.rev_threshold].index
    cond2_boost = vol_ratio[vol_ratio > V13Config.vol_ratio_threshold].index
    cond2_shrink = vol_shrink[vol_shrink > 0.7].index
    cond2 = set(cond2_boost) | set(cond2_shrink)
    cond3 = range_ratio[range_ratio < 0.8].index

    candidates = (set(cond1) & cond2 & liquid_stocks) | (set(cond1) & set(cond3) & liquid_stocks)
    if holdings:
        candidates = candidates - set(holdings.keys())
    candidates = sorted(candidates, key=lambda c: rev_5.get(c, 0))
    return candidates[:V13Config.max_holdings]


def _calc_fold_metrics(nav, trade_log, initial_capital):
    rets = nav.pct_change().dropna()
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    days = max((nav.index[-1] - nav.index[0]).days, 1)
    years = days / 365
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = rets.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    peak = nav.cummax()
    max_dd = ((nav - peak) / peak).min()
    downside = rets[rets < 0].std() * np.sqrt(252)
    sortino = ann_ret / downside if downside > 0 else 0

    sells = [t for t in trade_log if t['action'] == 'sell']
    wins = [t for t in sells if t.get('pnl_pct', 0) > 0]
    win_rate = len(wins) / len(sells) * 100 if sells else 0

    return {
        'annual_return': ann_ret,
        'annual_vol': ann_vol,
        'sharpe': sharpe,
        'sortino': sortino,
        'max_drawdown': max_dd,
        'total_trades': len(trade_log),
        'win_rate': win_rate,
        'total_return': total_ret,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="v13 Walk-Forward 过拟合检测")
    parser.add_argument("--train-days", type=int, default=252, help="训练期天数 (默认252)")
    parser.add_argument("--test-days", type=int, default=63, help="测试期天数 (默认63)")
    parser.add_argument("--step-days", type=int, default=63, help="滑动步长 (默认63)")
    args = parser.parse_args()

    print("=" * 60)
    print("v13_small_mid_short — Walk-Forward 过拟合检测")
    print("=" * 60)

    # 加载完整数据
    print("\n[1/3] 加载完整数据...")
    t0 = time.time()
    close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = load_small_cap_panel()
    print(f"  Panel: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只")
    print(f"  耗时 {time.time()-t0:.1f}s")

    dates = close_panel.index
    n = len(dates)
    train_days = args.train_days
    test_days = args.test_days
    step_days = args.step_days

    fold_results = []
    fold_navs = []
    fold = 0
    train_end = train_days

    print(f"\n[2/3] Walk-Forward 回测 (train={train_days}d, test={test_days}d, step={step_days}d)")

    while train_end + test_days <= n:
        fold += 1
        train_start = max(0, train_end - train_days)
        test_start = train_end
        test_end = min(n, train_end + test_days)

        # 窗口切片
        window_dates = dates[train_start:test_end]
        sub_close = close_panel.loc[window_dates]
        sub_volume = volume_panel.loc[window_dates]
        sub_amount = amount_panel.loc[window_dates]
        sub_high = high_panel.loc[window_dates]
        sub_low = low_panel.loc[window_dates]
        sub_open = open_panel.loc[window_dates]

        # warmup = 训练期长度（跳过训练期的 NAV 不稳定期）
        warmup = train_end - train_start

        m, nav, trades = run_v13_fold(
            sub_close, sub_volume, sub_amount, sub_high, sub_low, sub_open,
            warmup_days=warmup, label=f"v13_fold{fold}"
        )

        test_start_date = dates[test_start].date()
        test_end_date = dates[test_end - 1].date()

        fold_results.append({
            'fold': fold,
            'test_period': f"{test_start_date}~{test_end_date}",
            'ann_return': m['annual_return'],
            'sharpe': m['sharpe'],
            'sortino': m['sortino'],
            'max_dd': m['max_drawdown'],
            'trades': m['total_trades'],
            'win_rate': m['win_rate'],
        })
        fold_navs.append(nav)

        print(f"  Fold {fold}: {test_start_date}~{test_end_date} | "
              f"Ret={m['annual_return']:.1%} Sharpe={m['sharpe']:.2f} "
              f"DD={m['max_drawdown']:.1%} Win={m['win_rate']:.0f}%")

        train_end += step_days

    # 汇总
    print(f"\n[3/3] WF 汇总 ({len(fold_results)} folds)")
    print(f"{'─' * 70}")

    positive_folds = sum(1 for r in fold_results if r['ann_return'] > 0)
    avg_ret = np.mean([r['ann_return'] for r in fold_results])
    avg_sharpe = np.mean([r['sharpe'] for r in fold_results])
    avg_sortino = np.mean([r['sortino'] for r in fold_results])
    avg_maxdd = np.mean([r['max_dd'] for r in fold_results])
    avg_winrate = np.mean([r['win_rate'] for r in fold_results])

    print(f"  平均年化:   {avg_ret:.1%}")
    print(f"  平均夏普:   {avg_sharpe:.2f}")
    print(f"  平均Sortino:{avg_sortino:.2f}")
    print(f"  平均MaxDD:  {avg_maxdd:.1%}")
    print(f"  平均胜率:   {avg_winrate:.0f}%")
    print(f"  正收益fold: {positive_folds}/{len(fold_results)} ({positive_folds/len(fold_results):.0%})")

    # 拼接样本外净值
    if fold_navs:
        combined_nav = fold_navs[0] / fold_navs[0].iloc[0]
        for fnav in fold_navs[1:]:
            combined_nav = combined_nav * (fnav / fnav.iloc[0])
        comb_ret = combined_nav.iloc[-1] / combined_nav.iloc[0] - 1
        comb_peak = combined_nav.cummax()
        comb_maxdd = ((combined_nav - comb_peak) / comb_peak).min()
        comb_rets = combined_nav.pct_change().dropna()
        comb_years = max(len(combined_nav) / 252, 0.01)
        comb_ann_ret = (1 + comb_ret) ** (1 / comb_years) - 1
        comb_sharpe = comb_ann_ret / (comb_rets.std() * np.sqrt(252)) if comb_rets.std() > 0 else 0
        print(f"\n  拼接样本外净值:")
        print(f"    总收益: {comb_ret:.1%} | 年化: {comb_ann_ret:.1%} | "
              f"夏普: {comb_sharpe:.2f} | MaxDD: {comb_maxdd:.1%}")

    # 保存结果
    out_dir = os.path.join(REPORT_DIR, "v13_wf_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "wf_results.json"), "w") as f:
        json.dump({
            'folds': fold_results,
            'summary': {
                'avg_return': avg_ret, 'avg_sharpe': avg_sharpe,
                'avg_sortino': avg_sortino, 'avg_maxdd': avg_maxdd,
                'positive_folds': positive_folds, 'total_folds': len(fold_results),
            }
        }, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n  结果已保存: {out_dir}/")

    return fold_results


if __name__ == '__main__':
    main()
