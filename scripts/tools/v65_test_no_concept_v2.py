#!/usr/bin/env python3
"""v65 方案C：不用概念热度 + 排除一字涨停板"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pandas as pd
import numpy as np
from core.db import load_panel_from_db

print("=" * 60)
print("v65 方案C：不用概念热度 + 排除一字涨停板")
print("=" * 60)

result = load_panel_from_db("2021-01-01", "2026-06-24", pool="zz1800", need_open=True, need_hl=True)
(close, vol, amt, opn, high, low), codes = result
print(f"\nPanel: {close.shape[0]} 天 × {close.shape[1]} 只")

# 计算涨停因子
returns = close.pct_change()
limit_threshold = 0.095
limit_up = (returns >= limit_threshold) & (returns <= 0.105)
yesterday_limit = limit_up.shift(1).fillna(False)
two_day_limit = limit_up & yesterday_limit

# 回测
cash = 200000
holdings = {}
dates = close.index
nav_list = []
trades = wins = losses = 0
skipped_one_word = 0

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
    
    # 选股：连续两天涨停
    prev_date = dates[i-2]
    if prev_date in two_day_limit.index:
        stocks = two_day_limit.loc[prev_date]
        candidates = stocks[stocks].index.tolist()
        
        # 按市值排序
        if candidates:
            market_cap = amt.loc[prev_date, candidates] if prev_date in amt.index else pd.Series()
            if not market_cap.empty:
                sorted_candidates = market_cap.sort_values(ascending=True).index.tolist()
            else:
                sorted_candidates = candidates
        else:
            sorted_candidates = []
    else:
        sorted_candidates = []
    
    # 买入
    for code in sorted_candidates[:3]:
        if code in holdings or code not in opn.columns or date not in opn.index:
            continue
        
        buy = opn.loc[date, code]
        prev_c = close.loc[prev_date, code] if prev_date in close.index and code in close.columns else None
        
        if pd.isna(buy) or prev_c is None or pd.isna(prev_c) or buy <= 0 or prev_c <= 0:
            continue
        
        # 排除一字涨停板
        buy_high = high.loc[date, code] if code in high.columns and date in high.index else None
        buy_low = low.loc[date, code] if code in low.columns and date in low.index else None
        if buy_high is not None and buy_low is not None:
            if not pd.isna(buy_high) and not pd.isna(buy_low):
                if buy == buy_high == buy_low:
                    skipped_one_word += 1
                    continue
        
        # 高开判断
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
print(f"  跳过一字板: {skipped_one_word}")
print(f"  年化收益: {((nav[-1]) ** (252/len(nav)) - 1) * 100:.2f}%")
print(f"{'=' * 60}")

print(f"\n对比标杆 v39g (夏普1.297):")
if sharpe > 1.297:
    print(f"  ✅ 夏普 {sharpe:.3f} > 1.297，超越标杆")
else:
    print(f"  ❌ 夏普 {sharpe:.3f} < 1.297，不如标杆")
