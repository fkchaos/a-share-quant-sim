#!/usr/bin/env python3
"""v75j + 融资融券择时 - 信号有效性测试"""
import sys
sys.path.insert(0, "/root/a-share-quant-sim")

import numpy as np
import pandas as pd
import akshare as ak
from core.db import load_panel_from_db

print("=" * 60)
print("v75j + 融资融券择时 - 信号有效性测试")
print("=" * 60)

# 1. Load stock data
print("\n[1] 加载股票数据...")
panels, codes = load_panel_from_db(
    start_date='2021-01-01', end_date='2026-06-30', pool='zz1800'
)
close, vol, amt = panels
print(f"  数据: {close.shape[0]}天 x {close.shape[1]}只股票")

# 2. Get margin signal
print("\n[2] 获取融资融券信号...")
df_margin = ak.macro_china_market_margin_sh()
df_margin['日期'] = pd.to_datetime(df_margin['日期'])
df_margin = df_margin.sort_values('日期').set_index('日期')
df_margin['margin_5d_chg'] = df_margin['融资余额'].pct_change(5)
df_margin['margin_20d_chg'] = df_margin['融资余额'].pct_change(20)

# 3. Calculate market return
print("\n[3] 计算市场收益...")
market_ret = close.mean(axis=1).pct_change(5).shift(-5)

# 4. Align dates properly
print("\n[4] 对齐日期...")
# Reindex margin signal to match stock dates
margin_5d = df_margin['margin_5d_chg'].reindex(close.index)
margin_20d = df_margin['margin_20d_chg'].reindex(close.index)

# Drop NaN dates
valid_mask = margin_5d.notna() & market_ret.notna()
valid_dates = close.index[valid_mask]
print(f"  有效交易日: {len(valid_dates)}天")

# 5. Test different thresholds
print("\n[5] 测试信号有效性...")
results = []

for window_name, window_data in [('margin_5d_chg', margin_5d), ('margin_20d_chg', margin_20d)]:
    for threshold in [-0.03, -0.01, 0.00, 0.01, 0.03]:
        for direction in ['above', 'below']:
            if direction == 'above':
                mask = window_data > threshold
            else:
                mask = window_data < threshold
            
            active_mask = mask & valid_mask
            active_dates = close.index[active_mask]
            
            if len(active_dates) > 30:
                avg_ret = market_ret[active_dates].mean()
                std_ret = market_ret[active_dates].std()
                ir = avg_ret / std_ret if std_ret > 0 else 0
                results.append({
                    'window': window_name,
                    'direction': direction,
                    'threshold': threshold,
                    'active_days': len(active_dates),
                    'avg_ret_5d': avg_ret,
                    'sharpe_like': ir
                })

# 6. Print results
print("\n[6] 结果汇总:")
print(f"{'Window':<15} {'Direction':<8} {'Threshold':<10} {'ActiveDays':<10} {'AvgRet5d':<10} {'IR':<8}")
print("-" * 65)
for r in sorted(results, key=lambda x: abs(x['sharpe_like']), reverse=True):
    print(f"{r['window']:<15} {r['direction']:<8} {r['threshold']:<10.2f} "
          f"{r['active_days']:<10} {r['avg_ret_5d']:<10.4f} {r['sharpe_like']:<8.3f}")

# 7. Save results
df = pd.DataFrame(results)
df.to_csv('/tmp/v75j_margin_signal_test.csv', index=False)

# 8. Find best
if results:
    best = max(results, key=lambda x: abs(x['sharpe_like']))
    print(f"\n[7] 最佳信号:")
    print(f"  {best['window']}, {best['direction']} {best['threshold']}")
    print(f"  活跃天数: {best['active_days']}")
    print(f"  5日平均收益: {best['avg_ret_5d']:.4f}")
    print(f"  IR: {best['sharpe_like']:.3f}")

print("\n完成!")
