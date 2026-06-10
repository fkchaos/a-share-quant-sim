import sys, os
sys.path.insert(0, 'scripts')
sys.path.insert(0, '.')
from v13_small_mid_short import V13Config, calc_small_cap_factors, select_stocks
from core.db import load_panel_as_dicts
import pandas as pd, numpy as np

# 用 v13_small_mid_short 自带的 load 函数
close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = load_small_cap_panel(start_date='2025-05-01', end_date='2026-06-10')
factors = calc_small_cap_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)

dates = close_panel.index[-5:]
for date in dates:
    print(f'\n=== {date.date()} ===')
    if date not in factors['rev_5'].index:
        print('  date not in factors index')
        continue

    # 流动性筛选
    avg_amount = amount_panel.rolling(20).mean() / 1e4
    day_amount = avg_amount.loc[date]
    liquid_mask = (day_amount > 300) & (day_amount < 10000)  # 万元
    liquid_stocks = set(day_amount[liquid_mask].dropna().index)
    print(f'流动性池: {len(liquid_stocks)} 只')

    r5 = factors['rev_5'].loc[date].dropna()
    vr = factors['vol_ratio'].loc[date].dropna()

    # 跌幅 > 2% 的
    down_2pct = r5[r5 < -0.02]
    print(f'跌幅>2%: {len(down_2pct)} 只')
    if len(down_2pct) > 0:
        # 同时在流动性池里
        in_pool = down_2pct[down_2pct.index.isin(liquid_stocks)]
        print(f'且在流动性池: {len(in_pool)} 只')
        if len(in_pool) > 0:
            top5 = in_pool.nsmallest(5)
            for code, val in top5.items():
                ratio = vr.get(code, 0)
                print(f'  {code} rev_5={val:.2%} vol_ratio={ratio:.2f}')

    actual = select_stocks(factors, date, close_panel, volume_panel, amount_panel)
    print(f'select_stocks 返回: {actual}')
