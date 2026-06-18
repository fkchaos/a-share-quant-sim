import pandas as pd
from scripts.strategies.v20_tail_pick import calc_tail_pick_factors, V20Config
from core.db import load_panel_from_db

tpl, codes = load_panel_from_db(start_date='2026-05-15', need_hl=True)
close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
high_panel, low_panel = tpl[3], tpl[4]
print(f'面板: {close_panel.shape}')

factors = calc_tail_pick_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
latest = close_panel.index[-1]
print(f'最新日期: {latest.date()}')

vol_ratio = factors['vol_ratio'].loc[latest].dropna()
range_ratio = factors['range_ratio'].loc[latest].dropna()
amount_ratio = factors['amount_ratio'].loc[latest].dropna()
price_vs_ma5 = factors['price_vs_ma5'].loc[latest].dropna()
recent_limit_up = factors['recent_limit_up'].loc[latest].dropna()

avg_amount = amount_panel.rolling(20).mean() / 1e4
day_amount = avg_amount.loc[latest]
liquid_mask = (day_amount > V20Config.min_liquidity) & (day_amount < V20Config.max_liquidity)
liquid_stocks = set(day_amount[liquid_mask].dropna().index)
print(f'流动性池: {len(liquid_stocks)}只')

scored = []
for code in liquid_stocks:
    if code not in vol_ratio.index:
        continue
    vr = vol_ratio.get(code, 999)
    rr = range_ratio.get(code, 999)
    ar = amount_ratio.get(code, 0)
    pm = price_vs_ma5.get(code, 0)
    lu = recent_limit_up.get(code, 0)
    if vr > V20Config.vol_vs_avg_max * 1.5:
        continue
    if ar < V20Config.amount_vs_avg_min * 0.3:
        continue
    s1 = max(0, 3.0 - vr * 2.0) if vr < 1.5 else 0
    s2 = (1.0 - rr) * 2.0 if rr < 1.0 else (max(0, (1.2 - rr) / 0.2 * 0.5) if rr < 1.2 else 0)
    s3 = max(0, 1.0 - abs(ar - 1.0) * 0.8) if 0.15 < ar < 3.0 else 0
    s4 = min((pm - 1.0) * 5.0, 1.0) if pm > 1.0 else (0.2 if pm > 0.98 else 0)
    s5 = 0.8 if lu > 0 else 0
    score = s1 + s2 + s3 + s4 + s5
    if score > 0:
        scored.append((code, vr, rr, ar, pm, lu, score))

scored.sort(key=lambda x: x[6], reverse=True)
print(f'\n{"代码":8s} {"vol_r":>6s} {"rng_r":>6s} {"amt_r":>6s} {"pv_ma":>6s} {"lu":>3s} {"score":>6s}')
print('-' * 50)
for code, vr, rr, ar, pm, lu, score in scored[:20]:
    print(f'{code:8s} {vr:6.2f} {rr:6.2f} {ar:6.2f} {pm:6.3f} {lu:3.0f} {score:6.2f}')
print(f'\n候选: {len(scored)}只 | 实际选: {min(len(scored), 8)}只')
