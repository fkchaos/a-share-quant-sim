#!/usr/bin/env python3
"""
v61b 止盈参数扫描测试 (简化版)
直接调用v61b_risk_scan的run_fold函数
"""
import sys
import os
import pandas as pd
sys.path.insert(0, '/root/a-share-quant-sim')

from scripts.backtest.v61b_risk_scan import load_data, run_fold

# 测试的止盈值
TAKE_PROFIT_VALUES = [0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25]

print("加载数据...")
data = load_data()
print(f"数据加载完成: {data['close'].shape[0]}天, {data['close'].shape[1]}只股票")

# 使用标准WF的test区间
# 2024-01-01 ~ 2026-05-31 (最近的test期)
test_start = pd.Timestamp('2024-01-01')
test_end = pd.Timestamp('2026-05-31')

results = []

for tp in TAKE_PROFIT_VALUES:
    print(f"\n{'='*60}")
    print(f"测试 TAKE_PROFIT = {tp*100:.0f}%")
    print(f"{'='*60}")
    
    try:
        result = run_fold(
            data, 
            test_start, 
            test_end, 
            rebal=5, 
            top_n=5, 
            sl=-0.08, 
            tp=tp, 
            hold_max=5
        )
        
        if result:
            results.append({
                'take_profit': tp,
                'total_return': result.get('total', 0),
                'sharpe': result.get('sharpe', 0),
                'max_drawdown': result.get('dd', 0),
            })
            print(f"  收益={result.get('total', 0):.2f}%, 夏普={result.get('sharpe', 0):.3f}, 回撤={result.get('dd', 0):.1f}%")
    except Exception as e:
        print(f"  错误: {e}")
        import traceback
        traceback.print_exc()

# 打印汇总
print(f"\n{'='*80}")
print("v61b 止盈参数扫描结果汇总 (2024-01-01 ~ 2026-05-31)")
print(f"{'='*80}")
print(f"{'止盈%':>8} {'总收益%':>10} {'夏普':>8} {'最大回撤%':>10}")
print(f"{'-'*80}")
for r in sorted(results, key=lambda x: x['sharpe'], reverse=True):
    print(f"{r['take_profit']*100:>7.0f}% {r['total_return']:>9.2f}% {r['sharpe']:>7.3f} {r['max_drawdown']:>9.1f}%")
