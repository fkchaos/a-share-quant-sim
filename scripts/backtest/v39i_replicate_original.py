#!/usr/bin/env python3
"""
scripts/backtest/v39i_replicate_original.py
复现原报告结果：amount=0 + 2023-2025 + full + 原始参数
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import numpy as np
import pandas as pd
from core.db import load_panel_from_db
from core.account import PortfolioState
from scripts.strategies.v39c_pv_resonance import calc_factors
from scripts.strategies.v39i_optimized import select_stocks_v39i


def run_simulation(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel,
                   start_date, end_date, params, initial_cash=200000):
    """跑全量模拟盘回测"""
    factors = calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel, params)

    # 获取交易日期
    dates = close_panel.index[(close_panel.index >= start_date) & (close_panel.index <= end_date)]

    portfolio = PortfolioState(cash=initial_cash)
    trade_log = []

    for i, date in enumerate(dates):
        current_prices = close_panel.loc[date]

        # 风控检查：止损/止盈/超时
        to_sell = []
        for code, holding in list(portfolio.holdings.items()):
            if code not in current_prices.index or pd.isna(current_prices[code]):
                continue
            price = current_prices[code]
            cost = holding['cost']
            hold_days = (date - holding['date']).days

            if price / cost - 1 <= params['STOP_LOSS']:
                to_sell.append((code, 'stop_loss'))
            elif price / cost - 1 >= params['TAKE_PROFIT']:
                to_sell.append((code, 'take_profit'))
            elif hold_days >= params['HOLD_DAYS_MAX']:
                to_sell.append((code, 'timeout'))

        # 卖出
        for code, reason in to_sell:
            if code in current_prices.index and not pd.isna(current_prices[code]):
                portfolio.sell(code, current_prices[code], date, reason)
                trade_log.append({'date': date, 'code': code, 'action': 'sell',
                                  'price': current_prices[code], 'reason': reason})

        # 选股
        current_holdings = list(portfolio.holdings.keys())
        selected = select_stocks_v39i(factors, date, current_holdings=current_holdings, params=params)

        # 买入
        if selected:
            total_value = portfolio.get_total_value(current_prices)
            per_stock = min((portfolio.cash - 50000) / len(selected), total_value * params['MAX_POSITION'])
            per_stock = max(per_stock, 0)

            for code, score in selected:
                if code not in current_prices.index or pd.isna(current_prices[code]):
                    continue
                price = current_prices[code]
                shares = int(per_stock / (price * 100)) * 100
                if shares >= 100 and portfolio.cash >= shares * price * 1.001:
                    portfolio.buy(code, price, shares, date)
                    trade_log.append({'date': date, 'code': code, 'action': 'buy',
                                      'price': price, 'shares': shares})

    # 最终净值
    final_value = portfolio.get_total_value(close_panel.iloc[-1])
    total_return = (final_value / initial_cash - 1) * 100

    # 计算夏普
    nav_series = []
    for date in dates[::5]:
        val = portfolio.get_total_value(close_panel.loc[date])
        nav_series.append(val)
    nav_series = pd.Series(nav_series)
    returns = nav_series.pct_change().dropna()
    sharpe = returns.mean() / returns.std() * np.sqrt(252 / 5) if returns.std() > 0 else 0

    return total_return, sharpe, final_value, len(trade_log)


def main():
    print("=" * 60)
    print("复现原报告 v39i 结果")
    print("=" * 60)

    params = {
        'MAX_DAILY_BUY': 3, 'MAX_POSITION': 0.20,
        'W_MOM': 0.15, 'W_PV_CORR': 0.05, 'W_TURNOVER': 0.05,
        'W_SIZE': 0.30, 'W_FUND_FLOW': 0.05, 'W_GAP': 0.05, 'W_ILLIQ': 0.20,
        'MOM_THRESHOLD': 0.05, 'MOM_THRESHOLD_BEAR': 0.08,
        'PV_CORR_10_MIN': -0.5, 'PV_CORR_20_MIN': 0.0, 'BOLL_W_MIN': 0.0,
        'STOP_LOSS': -0.05, 'TAKE_PROFIT': 0.10,
        'HOLD_DAYS_MAX': 5, 'HOLD_DAYS_EXTEND': 5, 'HOLD_DAYS_EXTEND_PNL': 0.03,
        'MAX_HOLDINGS': 8, 'COOLDOWN_DAYS': 0,
    }

    tpl, codes = load_panel_from_db('2023-01-01', '2025-12-31', need_open=True, need_hl=True, pool='zz800')
    close_panel, volume_panel, amount_panel, open_panel, high_panel, low_panel = tpl

    # 1. amount=0（原报告条件）
    print("\n[1] amount=0 (原报告条件, 2023-2025, full)")
    ret, sharpe, final, trades = run_simulation(
        close_panel, volume_panel, amount_panel * 0,
        high_panel, low_panel, open_panel,
        '2023-01-01', '2025-12-31', params)
    print(f"  收益: {ret:.2f}%, 夏普: {sharpe:.3f}, 净值: {final:.0f}, 交易: {trades}")

    # 2. amount 修复后
    print("\n[2] amount修复后 (同样条件)")
    ret2, sharpe2, final2, trades2 = run_simulation(
        close_panel, volume_panel, amount_panel,
        high_panel, low_panel, open_panel,
        '2023-01-01', '2025-12-31', params)
    print(f"  收益: {ret2:.2f}%, 夏普: {sharpe2:.3f}, 净值: {final2:.0f}, 交易: {trades2}")

    print("\n" + "=" * 60)


if __name__ == '__main__':
    main()
