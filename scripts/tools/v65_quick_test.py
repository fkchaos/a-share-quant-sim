#!/usr/bin/env python3
"""v65 快速测试（放宽参数）"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

import pandas as pd
import numpy as np
from core.db import load_panel_from_db
from scripts.strategies.v65_yesterday_limit import calc_factors_v65_yesterday_limit, select_stocks_v65_yesterday_limit

# 加载数据（1年）
result = load_panel_from_db("2021-01-01", "2022-01-01", pool="zz1800", need_open=True, need_hl=True)
(close, vol, amt, opn, high, low), codes = result
print(f"Panel: {close.shape[0]} 天 × {close.shape[1]} 只")

factors = calc_factors_v65_yesterday_limit(close, vol, amt, high, low, opn)

# 测试不同参数
for concept_top in [0.95, 0.98]:
    for high_open in [0.01, 0.02]:
        cash = 200000
        holdings = {}
        nav_list = []
        trades = wins = losses = 0
        
        PARAMS = {
            'MIN_AMOUNT': 5000000,
            'MIN_MARKET_CAP': 2000000000,
            'CONCEPT_HEAT_TOP': concept_top,
            'HIGH_OPEN_THRESHOLD': high_open,
        }
        
        for i in range(2, len(close.index)):
            date = close.index[i]
            
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
            prev_date = close.index[i-2]
            prev_str = str(prev_date.date()) if hasattr(prev_date, 'date') else str(prev_date)[:10]
            stocks = select_stocks_v65_yesterday_limit(factors, prev_str, holdings, PARAMS, None)
            
            for code, _ in stocks[:3]:
                if code in holdings or code not in opn.columns or date not in opn.index:
                    continue
                buy = opn.loc[date, code]
                prev_c = close.loc[prev_date, code]
                if pd.isna(buy) or pd.isna(prev_c) or buy <= 0 or prev_c <= 0:
                    continue
                if buy / prev_c - 1 < high_open:
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
        print(f'概念{concept_top}+高开{high_open}: 收益{ret:.1f}%, 胜率{wr:.0f}%, {trades}次')
