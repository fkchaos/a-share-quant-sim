import sys, os
sys.path.insert(0, 'scripts')
sys.path.insert(0, '.')
from v20_tail_pick import V20Config, select_stocks_tail_pick, calc_tail_pick_factors, load_panel
import pandas as pd, numpy as np

close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = load_panel(start_date='2025-05-01', end_date='2026-06-10')
factors = calc_tail_pick_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)

dates = close_panel.index[-5:]
for date in dates:
    print(f'\n=== {date.date()} ===')
    avg_amount = amount_panel.rolling(20).mean() / 1e4
    if date not in avg_amount.index:
        continue
    day_amount = avg_amount.loc[date]
    liquid_mask = (day_amount > 300) & (day_amount < 10000)
    liquid_stocks = set(day_amount[liquid_mask].dropna().index)
    print(f'流动性通过: {len(liquid_stocks)} 只')
    vol_ratio = factors['vol_ratio'].loc[date].dropna()
    range_ratio = factors['range_ratio'].loc[date].dropna()
    amount_ratio = factors['amount_ratio'].loc[date].dropna()
    price_vs_ma5 = factors['price_vs_ma5'].loc[date].dropna()
    recent_limit_up = factors['recent_limit_up'].loc[date].dropna()
    pass_vr = pass_rr = pass_ar = pass_pm = pass_lu = 0
    fail_reasons = {'vr': 0, 'rr': 0, 'ar': 0, 'pm': 0, 'lu': 0}
    last_fail = None
    for code in liquid_stocks:
        if code not in vol_ratio.index:
            continue
        vr = vol_ratio.get(code, 999)
        if vr > 0.8:
            fail_reasons['vr'] += 1
            continue
        pass_vr += 1
        rr = range_ratio.get(code, 999)
        if rr > 0.8:
            fail_reasons['rr'] += 1
            continue
        pass_rr += 1
        ar = amount_ratio.get(code, 0)
        if ar < 0.5 or ar > 3.0:
            fail_reasons['ar'] += 1
            continue
        pass_ar += 1
        pm = price_vs_ma5.get(code, 0)
        if pm < 1.0:
            fail_reasons['pm'] += 1
            continue
        pass_pm += 1
        lu = recent_limit_up.get(code, 0)
        if lu < 1.0:
            fail_reasons['lu'] += 1
            continue
        pass_lu += 1
    print(f'  缩量(vr<=0.8): {pass_vr}  过滤掉: {fail_reasons["vr"]}')
    print(f'  振幅收窄(rr<=0.8): {pass_rr}  过滤掉: {fail_reasons["rr"]}')
    print(f'  成交额(0.5<=ar<=3): {pass_ar}  过滤掉: {fail_reasons["ar"]}')
    print(f'  价格>MA5: {pass_pm}  过滤掉: {fail_reasons["pm"]}')
    print(f'  20日有涨停: {pass_lu}  过滤掉: {fail_reasons["lu"]}')
    selected = select_stocks_tail_pick(factors, date, close_panel, volume_panel, amount_panel, high_panel, low_panel)
    print(f'  最终选中: {selected}')
