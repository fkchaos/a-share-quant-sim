#!/usr/bin/env python3
"""
scripts/backtest/wf_runner.py — 通用 Walk-Forward 运行器
=====================================================
支持任意策略的 WF 验证，通过 strategy_adapter 调用选股+风控，
通过 core/account.py 的 buy/sell 执行交易（与模拟盘完全一致）。

用法:
    python scripts/backtest/wf_runner.py --strategy v27
    python scripts/backtest/wf_runner.py --strategy v27 --train 252 --test 126 --step 63  # v20c 已退役
    python scripts/backtest/wf_runner.py --strategy v27  # 默认 train=252, test=252, step=252
"""
import sys
import os
import time
import argparse
import numpy as np
import pandas as pd

# 确保项目根目录在 path
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.db import get_kline, load_panel_from_db
from core.account import PortfolioState, buy, sell, portfolio_value
from core.config import TradingCosts
from scripts.backtest.strategy_adapter import get_adapter


def run_wf(strategy_name, train_days=252, test_days=126, step_days=63,
           start_date='2021-01-01', end_date='2026-05-31'):
    """
    运行 Walk-Forward 验证。
    交易逻辑使用 core/account.py 的 buy/sell，与模拟盘完全一致。
    """
    adapter = get_adapter()
    if strategy_name not in adapter.list_strategies():
        print(f"❌ 未知策略: {strategy_name}，可用: {adapter.list_strategies()}")
        return None

    print("=" * 60)
    print(f"{strategy_name} Walk-Forward 验证")
    print(f"  WF: train={train_days}, test={test_days}, step={step_days}")
    print(f"  区间: {start_date} ~ {end_date}")
    print("=" * 60)

    # ── 加载数据 ──
    print("\n[1/4] 加载数据...")
    t0 = time.time()
    tpl, codes = load_panel_from_db(start_date, end_date, need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]
    print(f"  Panel: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只")
    print(f"  耗时 {time.time()-t0:.1f}s")

    # ── 计算因子 ──
    print("\n[2/4] 计算因子...")
    t0 = time.time()
    factors = _calc_factors(strategy_name, close_panel, volume_panel, amount_panel,
                            high_panel, low_panel, open_panel)
    print(f"  耗时 {time.time()-t0:.1f}s")

    # ── 获取策略参数 ──
    risk_params = adapter.get_risk_params(strategy_name)
    regime_params = adapter.get_regime_params(strategy_name)
    initial_capital = risk_params.get("INITIAL_CAPITAL", 100000 if strategy_name == "v27" else 200000)
    max_holdings = risk_params.get("HOLD_DAYS_MAX", 8)
    max_daily_buy = risk_params.get("MAX_DAILY_BUY", 8)
    max_position = risk_params.get("MAX_POSITION", 0.30)

    # ── WF 循环 ──
    print("\n[3/4] 运行 Walk-Forward...")
    t0 = time.time()
    total_days = close_panel.shape[0]
    fold_results = []
    fold = 0
    start_idx = 0

    while start_idx + train_days + test_days < total_days:
        end_idx = min(start_idx + train_days + test_days, total_days)

        win_close = close_panel.iloc[start_idx:end_idx]
        win_vol = volume_panel.iloc[start_idx:end_idx]
        win_amt = amount_panel.iloc[start_idx:end_idx]
        win_open = open_panel.iloc[start_idx:end_idx]
        win_hi = high_panel.iloc[start_idx:end_idx]
        win_lo = low_panel.iloc[start_idx:end_idx]
        win_factors = _slice_factors(factors, start_idx, end_idx)

        # 初始化账户（用 core/account.py 的 PortfolioState）
        state = PortfolioState(cash=initial_capital, initial_capital=initial_capital)
        nav_list = []
        dates = win_close.index

        for i in range(len(dates)):
            if i < 30:
                nav_list.append(initial_capital)
                continue

            date = dates[i]
            if date not in win_close.index:
                nav_list.append(nav_list[-1] if nav_list else initial_capital)
                continue

            price_data = win_close.loc[date]
            open_data = win_open.loc[date]

            # 更新 hold_days（从 entry_date 计算，用交易日天数而非日历天数）
            for code in list(state.holdings.keys()):
                info = state.holdings[code]
                try:
                    entry = pd.Timestamp(info.get('entry_date', str(date)))
                    today = pd.Timestamp(date)
                    # 用面板索引差值计算交易日天数（跳过非交易日）
                    if entry in win_close.index and today in win_close.index:
                        entry_idx = win_close.index.get_loc(entry)
                        today_idx = win_close.index.get_loc(today)
                        info['hold_days'] = today_idx - entry_idx
                    else:
                        # 回退到日历天数
                        info['hold_days'] = (today - entry).days
                except Exception:
                    info['hold_days'] = info.get('hold_days', 0) + 1

            # 风控检查（用 strategy_adapter）
            prev_close = win_close.iloc[i - 1] if i > 0 else None
            to_sell = adapter.risk_check(strategy_name, state, date, price_data,
                                          risk_params, prev_close=prev_close)

            # 执行卖出（含跌停封板跳过，与旧版 v20_walk_forward 一致）
            for code, reason, pnl in to_sell:
                if code in state.holdings and code in price_data.index:
                    sell_price = price_data[code]
                    if not pd.isna(sell_price) and sell_price > 0:
                        # 跌停封板检查：卖价 <= 前日收盘×0.90×1.01 → 卖不出，hold_days 回退
                        if i > 0 and prev_close is not None and code in prev_close.index:
                            prev_c = prev_close[code]
                            if not pd.isna(prev_c) and prev_c > 0:
                                if sell_price <= prev_c * 0.90 * 1.01:
                                    info = state.holdings[code]
                                    info['hold_days'] = max(0, info.get('hold_days', 0) - 1)
                                    continue
                        state = sell(state, code, sell_price, date, reason=reason)
                    sold += 1
                    print(f"  SELL {code} @ {sell_price:.2f} reason {reason} cash {state.cash:.2f}")

            # 选股（每天调用，和模拟盘一致）
            if len(state.holdings) < max_holdings:
                cands = adapter.select(strategy_name, win_factors, date,
                                       win_close, win_vol, win_amt,
                                       win_hi, win_lo, win_open,
                                       current_holdings=state.holdings,
                                       params=risk_params)

                if cands and state.cash > initial_capital * 0.03:
                    avail = state.cash - initial_capital * 0.03
                    nb = min(len(cands), max_daily_buy, max_holdings - len(state.holdings))
                    per_stock = min(avail / nb, initial_capital * max_position) if nb > 0 else 0

                    bought = 0
                    sold = 0
                    for code, score in cands[:max_daily_buy]:
                        if len(state.holdings) >= max_holdings or bought >= nb:
                            break
                        if code not in open_data.index:
                            continue
                        buy_price = open_data[code]
                        if pd.isna(buy_price) or buy_price <= 0:
                            print(f"  SKIP {code} invalid buy_price {buy_price}")
                            continue
                        # 涨停封板检查：买价 >= 前日收盘×1.10×0.99 → 买不进
                        if i > 0 and prev_close is not None and code in prev_close.index:
                            prev_c = prev_close[code]
                            if not pd.isna(prev_c) and prev_c > 0:
                                if buy_price >= prev_c * 1.10 * 0.99:
                                    continue
                        # 用 core/account.py 的 buy（shares 模式）
                        adj = buy_price * (1 + TradingCosts().slippage_rate)
                        shares = int(per_stock / adj / 100) * 100
                        if shares <= 0:
                            continue
                        state = buy(state, code, buy_price, date, shares=shares)
                        if code in state.holdings:
                            bought += 1
                            print(f"  BUY {code} {shares} @ {buy_price:.2f} cash {state.cash:.2f}")

            # NAV（用 core/account.py 的 portfolio_value）
            pv = portfolio_value(state, date, price_data)
            nav_list.append(pv)

        # 分割 train/test
        nav_s = pd.Series(nav_list)
        train_nav = nav_s[:train_days] if train_days < len(nav_s) else nav_s
        test_nav = nav_s[train_days:] if train_days < len(nav_s) else pd.Series()

        if len(test_nav) > 0 and test_nav.iloc[0] > 0:
            test_ret = test_nav.iloc[-1] / test_nav.iloc[0] - 1
            test_dd = ((test_nav.cummax() - test_nav) / test_nav.cummax()).max()
            test_daily = test_nav.pct_change().dropna()
            test_sharpe = test_daily.mean() / test_daily.std() * np.sqrt(252) if test_daily.std() > 0 else 0

            fold_results.append({
                'fold': fold, 'test_ret': test_ret, 'test_dd': test_dd,
                'test_sharpe': test_sharpe, 'test_days': len(test_nav)
            })
            print(f"  Fold {fold} | 测试: {test_ret*100:.2f}% (DD={test_dd*100:.1f}%, Sharpe={test_sharpe:.2f}, {len(test_nav)}天)")

        start_idx += step_days
        fold += 1

    elapsed = time.time() - t0
    print(f"\n  耗时 {elapsed:.1f}s")

    # ── 汇总 ──
    print("\n[4/4] 汇总...")
    if not fold_results:
        print("数据不足，无法生成 fold")
        return None

    df = pd.DataFrame(fold_results)
    print("\n" + "=" * 60)
    print(f"{strategy_name} WF 汇总 ({len(df)} folds)")
    print("=" * 60)
    print(f"  测试期平均收益率: {df['test_ret'].mean()*100:.2f}%")
    print(f"  测试期平均夏普:   {df['test_sharpe'].mean():.3f}")
    print(f"  测试期平均回撤:   {df['test_dd'].mean()*100:.2f}%")
    print(f"  正收益 fold:      {(df['test_ret'] > 0).sum()}/{len(df)} ({(df['test_ret'] > 0).mean()*100:.0f}%)")

    pos_folds = (df['test_ret'] > 0).mean() * 100
    avg_sharpe = df['test_sharpe'].mean()
    print(f"\n  WF 通过标准: 正收益 fold >= 60%, 夏普 > 0.5")
    if pos_folds >= 60 and avg_sharpe > 0.5:
        print(f"  ✅ WF 通过 ({pos_folds:.0f}% 正收益 fold, 夏普 {avg_sharpe:.3f})")
    else:
        print(f"  ❌ WF 未通过 ({pos_folds:.0f}% 正收益 fold, 夏普 {avg_sharpe:.3f})")

    return df


def _calc_factors(strategy_name, close_panel, volume_panel, amount_panel,
                  high_panel, low_panel, open_panel):
    """根据策略名计算因子"""
    if strategy_name == "v27":
        from scripts.strategies.v27_select import calc_factors
        return calc_factors(close_panel, volume_panel, amount_panel,
                           high_panel, low_panel, open_panel, params=None)
    elif strategy_name == "v31":
        from scripts.strategies.v29_select import calc_factors
        return calc_factors(close_panel, volume_panel, amount_panel,
                           high_panel, low_panel, open_panel, params=None)
    elif strategy_name == "v32":
        from scripts.strategies.v32_analyst_expectation import calc_factors
        from core.strategy_map import load_strategy
        strategy = load_strategy("v32")
        return calc_factors(close_panel, volume_panel, amount_panel,
                           high_panel, low_panel, open_panel, strategy["params"])
    elif strategy_name == "v33":
        from scripts.strategies.v33_residual_momentum import calc_factors
        from core.strategy_map import load_strategy
        strategy = load_strategy("v33")
        return calc_factors(close_panel, volume_panel, amount_panel,
                           high_panel, low_panel, open_panel, strategy["params"])
    elif strategy_name == "v35":
        from scripts.strategies.v35_sector_rotation import calc_factors
        from core.strategy_map import load_strategy
        strategy = load_strategy("v35")
        return calc_factors(close_panel, volume_panel, amount_panel,
                           high_panel, low_panel, open_panel, strategy["params"])
    # v20c 已退役
    else:
        raise ValueError(f"不支持的策略: {strategy_name}")


def _slice_factors(factors, start_idx, end_idx):
    """切片因子面板到指定窗口"""
    sliced = {}
    for name, df in factors.items():
        if hasattr(df, 'iloc'):
            sliced[name] = df.iloc[start_idx:end_idx]
        else:
            sliced[name] = df
    return sliced


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="通用 Walk-Forward 运行器")
    parser.add_argument("--strategy", required=True, help="策略名 (v27)，v20c 已退役")
    parser.add_argument("--train", type=int, default=252, help="训练期天数 (默认: 252)")
    parser.add_argument("--test", type=int, default=252, help="测试期天数 (默认: 252)")
    parser.add_argument("--step", type=int, default=252, help="滑动步长 (默认: 252)")
    parser.add_argument("--start", default="2021-01-01", help="回测起始日期")
    parser.add_argument("--end", default="2026-05-31", help="回测结束日期")
    args = parser.parse_args()

    run_wf(args.strategy, args.train, args.test, args.step, args.start, args.end)
