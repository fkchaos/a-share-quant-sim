#!/usr/bin/env python3
"""
分析 2023H2~2024H2 熊市期：大盘走势 + 策略亏损原因
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

import pandas as pd
import numpy as np

# 加载数据
files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
all_data = {}
for f in files:
    code = f.replace(".csv","")
    df = pd.read_csv(os.path.join(DAILY_DIR,f), index_col='date', parse_dates=True)
    if len(df)>0: all_data[code]=df

# 构建全量 close panel
close = pd.DataFrame({c:d['close'] for c,d in all_data.items()})
dates = close.dropna(how='all').index.sort_values()
dates = dates[(dates>='2023-01-01')&(dates<='2025-12-31')]
close = close.loc[dates]

# 全量等权指数（近似大盘）
# 用所有股票等权平均收益率
valid_cols = [c for c in close.columns if close[c].notna().sum() > 100]
close_valid = close[valid_cols].dropna(how='all')
daily_ret = close_valid.pct_change().mean(axis=1)  # 等权日收益
cum_ret = (1 + daily_ret).cumprod()

print("=== 大盘（等权指数）累计收益 ===")
periods = [
    ('2023-01-01','2023-06-30','2023H1'),
    ('2023-07-01','2023-12-31','2023H2'),
    ('2024-01-01','2024-06-30','2024H1'),
    ('2024-07-01','2024-12-31','2024H2'),
    ('2025-01-01','2025-06-30','2025H1'),
    ('2025-07-01','2025-12-31','2025H2'),
]
for s,e,label in periods:
    sl = cum_ret[(cum_ret.index>=s)&(cum_ret.index<=e)]
    if len(sl)>0:
        ret = sl.iloc[-1]/sl.iloc[0]-1
        print(f"  {label}: {ret*100:+.2f}%")

# 分析选股数量
print("\n=== 截面股票数量 ===")
for s,e,label in periods:
    sl = close_valid[(close_valid.index>=s)&(close_valid.index<=e)]
    avg_stocks = sl.notna().sum(axis=1).mean()
    print(f"  {label}: 平均 {avg_stocks:.0f} 只")

# 分析因子 IC 在熊市是否失效
print("\n=== 熊市期因子 IC ===")
from core.factors import calc_factors_panel
from core.config import STRATEGY_PROFILES

# 加载 2023H2~2024H2 数据
close_slice = close[(close.index>='2023-07-01')&(close.index<='2024-12-31')]
vol_slice = pd.DataFrame({c:all_data[c]['volume'] for c in all_data if c in all_data})
amt_slice = pd.DataFrame({c:all_data[c].get('amount', all_data[c]['close']*all_data[c]['volume']) for c in all_data if c in all_data})

# 只保留有效股票
vol_slice = vol_slice[close_slice.columns]
amt_slice = amt_slice[close_slice.columns]

# 因子计算
factors = calc_factors_panel(close_slice, vol_slice, amt_slice)

# 计算 IC
fwd_ret = close_slice.pct_change(5).shift(-5)
weights = STRATEGY_PROFILES['v6b_8f_pos_ic'].factor_weights

print(f"  {'因子':>15} | {'IC均值':>8} | {'IC_IR':>8} | {'IC>0占比':>8}")
print(f"  {'-'*50}")
for fac in weights.keys():
    if fac not in factors: continue
    ic_vals = []
    for date in factors[fac].index:
        if date not in fwd_ret.index: continue
        f = factors[fac].loc[date].dropna()
        r = fwd_ret.loc[date].dropna()
        common = f.index.intersection(r.index)
        if len(common) < 10: continue
        corr = np.corrcoef(f[common], r[common])[0,1]
        if not np.isnan(corr): ic_vals.append(corr)
    if ic_vals:
        ic_mean = np.mean(ic_vals)
        ic_ir = ic_mean/np.std(ic_vals) if np.std(ic_vals)>0 else 0
        ic_pos = sum(1 for x in ic_vals if x>0)/len(ic_vals)
        print(f"  {fac:>15} | {ic_mean:>+8.4f} | {ic_ir:>+8.4f} | {ic_pos:>8.1%}")

print("\n分析完成")
