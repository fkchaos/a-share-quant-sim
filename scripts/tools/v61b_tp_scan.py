#!/usr/bin/env python3
"""
v61b 止盈参数扫描测试
测试不同 TAKE_PROFIT 值对收益的影响
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.backtest.strategy_adapter import get_adapter
from scripts.backtest.wf_runner import run_wf

# 测试的止盈值
TAKE_PROFIT_VALUES = [0.05, 0.08, 0.10, 0.12, 0.15, 0.20, 0.25]

results = []

for tp in TAKE_PROFIT_VALUES:
    print(f"\n{'='*60}")
    print(f"测试 TAKE_PROFIT = {tp*100:.0f}%")
    print(f"{'='*60}")
    
    # 修改adapter的risk_params
    adapter = get_adapter()
    original_params = adapter.get_risk_params('v61b').copy()
    
    # 修改止盈值
    adapter._risk_params['v61b']['TAKE_PROFIT'] = tp
    
    try:
        # 运行WF
        result = run_wf('v61b', full=True)
        
        if result:
            results.append({
                'take_profit': tp,
                'total_return': result.get('total', 0),
                'sharpe': result.get('sharpe', 0),
                'max_drawdown': result.get('dd', 0),
                'win_rate': result.get('pos_rate', 0),
            })
            print(f"  结果: 收益={result.get('total', 0):.2f}%, 夏普={result.get('sharpe', 0):.3f}")
    except Exception as e:
        print(f"  错误: {e}")
    
    # 恢复原始参数
    adapter._risk_params['v61b'] = original_params

# 打印汇总
print(f"\n{'='*80}")
print("v61b 止盈参数扫描结果汇总")
print(f"{'='*80}")
print(f"{'止盈%':>8} {'总收益%':>10} {'夏普':>8} {'最大回撤%':>10} {'胜率%':>8}")
print(f"{'-'*80}")
for r in sorted(results, key=lambda x: x['sharpe'], reverse=True):
    print(f"{r['take_profit']*100:>7.0f}% {r['total_return']:>9.2f}% {r['sharpe']:>7.3f} {r['max_drawdown']:>9.1f}% {r['win_rate']:>7.1f}%")
