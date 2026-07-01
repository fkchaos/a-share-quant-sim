#!/usr/bin/env python3
"""
v65_yesterday_limit_test.py — 昨日涨停打板策略测试（BigQuant原始逻辑）
====================================================================
选股：连续两天涨停 + 热门概念（排名≥98%）+ 市值升序
买入：T+1日开盘价（假设高开>=2%时买入）
卖出：T+2日开盘价（持有1天）
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pandas as pd
import numpy as np
from core.db import load_panel_from_db
from scripts.strategies.v65_yesterday_limit import calc_factors_v65_yesterday_limit, select_stocks_v65_yesterday_limit

# 参数
INITIAL_CAPITAL = 200000
STOP_LOSS = -0.05
TAKE_PROFIT = 0.05
HOLD_DAYS_MAX = 1
MAX_DAILY_BUY = 3
MAX_HOLDINGS = 5
COMMISSION = 0.0003  # 佣金万3
STAMP_TAX = 0.001    # 印花税千1
SLIPPAGE = 0.001     # 滑点千1

PARAMS = {
    "MIN_AMOUNT": 5000000,
    "MIN_MARKET_CAP": 2000000000,
    "CONCEPT_HEAT_TOP": 0.98,
    "HIGH_OPEN_THRESHOLD": 0.02,
}

print("=" * 60)
print("v65 昨日涨停打板策略测试（BigQuant原始逻辑）")
print("  选股：连续两天涨停 + 热门概念（排名≥98%）+ 市值升序")
print("  买入：T+1日开盘价（假设高开>=2%时买入）")
print("  卖出：T+2日开盘价（持有1天）")
print("=" * 60)

# 加载数据
print("\n[1/3] 加载数据...")
result = load_panel_from_db("2021-01-01", "2026-06-24", pool="zz1800", need_open=True, need_hl=True)
(close, vol, amt, opn, high, low), codes = result
print(f"  Panel: {close.shape[0]} 天 × {close.shape[1]} 只")

# 计算因子
print("\n[2/3] 计算因子...")
factors = calc_factors_v65_yesterday_limit(close, vol, amt, high, low, opn)
print(f"  因子: {list(factors.keys())}")

# 全量回测
print("\n[3/3] 运行全量回测...")

cash = INITIAL_CAPITAL
holdings = {}  # code: {shares, cost, buy_date}
dates = close.index
nav_list = []
total_trades = 0
skipped_trades = 0
total_cost = 0
wins = 0
losses = 0

for i in range(2, len(dates)):  # 从第3天开始，因为需要T-2日数据
    date = dates[i]
    date_str = str(date.date()) if hasattr(date, 'date') else str(date)[:10]
    
    # === 步骤1：卖出昨天买入的持仓（T+2日开盘价卖出） ===
    for code in list(holdings.keys()):
        if code not in opn.columns or date not in opn.index:
            continue
        
        # 获取卖出价（T+2日开盘价）
        sell_price = opn.loc[date, code]
        if pd.isna(sell_price) or sell_price <= 0:
            continue
        
        # 计算收益
        cost = holdings[code]['cost']
        pnl = (sell_price / cost - 1) if cost > 0 else 0
        
        # 持有1天后卖出
        days_held = (date - holdings[code]['buy_date']).days if hasattr(date, 'date') else 1
        
        if days_held >= 1:
            # 计算卖出成本
            sell_price_after_slippage = sell_price * (1 - SLIPPAGE)
            sell_amount = holdings[code]['shares'] * sell_price_after_slippage
            sell_commission = sell_amount * COMMISSION
            sell_tax = sell_amount * STAMP_TAX
            cash += sell_amount - sell_commission - sell_tax
            total_cost += sell_commission + sell_tax
            
            if pnl > 0:
                wins += 1
            else:
                losses += 1
            
            del holdings[code]
            total_trades += 1
    
    # === 步骤2：选股（T-2日收盘后选股） ===
    if len(holdings) < MAX_HOLDINGS:
        # 选股用T-2日的数据
        prev_date = dates[i-2] if i >= 2 else None
        if prev_date is None:
            continue
        prev_date_str = str(prev_date.date()) if hasattr(prev_date, 'date') else str(prev_date)[:10]
        
        stocks = select_stocks_v65_yesterday_limit(factors, prev_date_str, holdings, PARAMS, None)
        
        # === 步骤3：买入（T日开盘价，假设高开>=2%时买入） ===
        for code, weight in stocks[:MAX_DAILY_BUY]:
            if code in holdings or code not in opn.columns or date not in opn.index:
                continue
            
            # 检查是否高开>=2%
            buy_price = opn.loc[date, code]
            prev_close = close.loc[prev_date, code] if prev_date in close.index and code in close.columns else None
            
            if pd.isna(buy_price) or buy_price <= 0:
                continue
            if prev_close is None or pd.isna(prev_close) or prev_close <= 0:
                continue
            
            # 高开判断：T日开盘价 / T-2日收盘价 >= 1.02
            high_open_ratio = buy_price / prev_close - 1
            if high_open_ratio < 0.02:  # 需要高开2%以上
                skipped_trades += 1
                continue
            
            amount = cash * 0.20
            if amount > 10000:
                # 计算买入成本（含滑点）
                buy_price_after_slippage = buy_price * (1 + SLIPPAGE)
                shares = int(amount / buy_price_after_slippage / 100) * 100
                if shares > 0:
                    buy_amount = shares * buy_price_after_slippage
                    buy_commission = buy_amount * COMMISSION
                    cash -= buy_amount + buy_commission
                    total_cost += buy_commission
                    holdings[code] = {
                        'shares': shares, 
                        'cost': buy_price_after_slippage,
                        'buy_date': date
                    }
                    total_trades += 1
    
    # === 步骤4：记录净值 ===
    total_value = cash
    for code, pos in holdings.items():
        if code in close.columns and date in close.index:
            price = close.loc[date, code]
            if not pd.isna(price):
                total_value += price * pos['shares']
    nav_list.append(total_value / INITIAL_CAPITAL)

# 计算指标
nav = pd.Series(nav_list, index=dates[2:])
returns = nav.pct_change().dropna()
total_return = (nav.iloc[-1] - 1) * 100
sharpe = returns.mean() / returns.std() * np.sqrt(252) if returns.std() > 0 else 0
max_dd = ((nav / nav.cummax()) - 1).min() * 100
win_rate = wins / (wins + losses) * 100 if (wins + losses) > 0 else 0

print(f"\n{'=' * 60}")
print(f"回测结果（BigQuant原始逻辑）:")
print(f"  总收益率: {total_return:.2f}%")
print(f"  夏普比率: {sharpe:.3f}")
print(f"  最大回撤: {max_dd:.2f}%")
print(f"  最终净值: {nav.iloc[-1]:.4f}")
print(f"  总交易次数: {total_trades}")
print(f"  跳过高开: {skipped_trades}")
print(f"  胜率: {win_rate:.1f}% ({wins}胜/{losses}负)")
print(f"  总交易成本: {total_cost:.2f}")
print(f"  年化收益: {((nav.iloc[-1]) ** (252/len(nav)) - 1) * 100:.2f}%")
print(f"{'=' * 60}")

# 与v39g对比
print(f"\n对比标杆 v39g (夏普1.297):")
if sharpe > 1.297:
    print(f"  ✅ 夏普 {sharpe:.3f} > 1.297，超越标杆")
else:
    print(f"  ❌ 夏普 {sharpe:.3f} < 1.297，不如标杆")
