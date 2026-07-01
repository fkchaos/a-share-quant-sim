#!/usr/bin/env python3
"""v65 全量回测（放宽参数）"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pandas as pd
import numpy as np
from core.db import load_panel_from_db
from scripts.strategies.v65_yesterday_limit import calc_factors_v65_yesterday_limit, select_stocks_v65_yesterday_limit

print("=" * 60)
print("v65 昨日涨停打板策略（放宽参数）")
print("  概念热度排名≥95%（原始98%太严格）")
print("  高开>=1%时买入（原始2%太严格）")
print("=" * 60)

result = load_panel_from_db("2021-01-01", "2026-06-24", pool="zz1800", need_open=True, need_hl=True)
(close, vol, amt, opn, high, low), codes = result
print(f"\nPanel: {close.shape[0]} 天 × {close.shape[1]} 只")

factors = calc_factors_v65_yesterday_limit(close, vol, amt, high, low, opn)

# 最优参数
cash = 200000
holdings = {}
dates = close.index
nav_list = []
trades = wins = losses = 0

PARAMS = {
    'MIN_AMOUNT': 5000000,
    'MIN_MARKET_CAP': 2000000000,
    'CONCEPT_HEAT_TOP': 0.95,
    'HIGH_OPEN_THRESHOLD': 0.01,
}

print("\n运行回测...")

for i in range(2, len(dates)):
    date = dates[i]
    
    # 卖出
    for code in list(holdings.keys()):
        if code in opn.columns and date in opn.index:
            sell = opn.loc[date, code]
            if not pd.isna(sell) and sell > 0:
                pnl = (sell / holdings[code]['cost'] - 1)
                cash += holdings[code]['shares'] * sell * 0.998
                del holdings[code]
                if pnl > 0: wins += 1
                else: losses += 1
                trades += 1
    
    # 选股+买入
    prev_date = dates[i-2]
    prev_str = str(prev_date.date()) if hasattr(prev_date, 'date') else str(prev_date)[:10]
    stocks = select_stocks_v65_yesterday_limit(factors, prev_str, holdings, PARAMS, None)
    
    for code, _ in stocks[:3]:
        if code in holdings or code not in opn.columns or date not in opn.index:
            continue
        buy = opn.loc[date, code]
        prev_c = close.loc[prev_date, code]
        if pd.isna(buy) or pd.isna(prev_c) or buy <= 0 or prev_c <= 0:
            continue
        if buy / prev_c - 1 < 0.01:
            continue
        amount = cash * 0.20
        if amount > 10000:
            shares = int(amount / buy / 100) * 100
            if shares > 0:
                cash -= shares * buy * 1.0003
                holdings[code] = {'shares': shares, 'cost': buy}
                trades += 1
    
    total = cash + sum(
        close.loc[date, c] * p['shares'] 
        for c, p in holdings.items() 
        if c in close.columns and date in close.index and not pd.isna(close.loc[date, c])
    )
    nav_list.append(total / 200000)

# 计算指标
nav = np.array(nav_list)
ret = (nav[-1] - 1) * 100
wr = wins/(wins+losses)*100 if (wins+losses) > 0 else 0
returns = np.diff(nav) / nav[:-1]
sharpe = np.mean(returns) / np.std(returns) * np.sqrt(252) if np.std(returns) > 0 else 0
max_dd = ((nav / np.maximum.accumulate(nav)) - 1).min() * 100

print(f"\n{'=' * 60}")
print(f"回测结果:")
print(f"  总收益率: {ret:.2f}%")
print(f"  夏普比率: {sharpe:.3f}")
print(f"  最大回撤: {max_dd:.2f}%")
print(f"  胜率: {wr:.1f}% ({wins}胜/{losses}负)")
print(f"  交易次数: {trades}")
print(f"  年化收益: {((nav[-1]) ** (252/len(nav)) - 1) * 100:.2f}%")
print(f"{'=' * 60}")

print(f"\n对比标杆 v39g (夏普1.297):")
if sharpe > 1.297:
    print(f"  ✅ 夏普 {sharpe:.3f} > 1.297，超越标杆")
else:
    print(f"  ❌ 夏普 {sharpe:.3f} < 1.297，不如标杆")
