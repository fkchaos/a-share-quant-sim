#!/usr/bin/env python3
"""v68 近3天涨停因子(W_RECENT_LIMIT_3D)权重扫描"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import numpy as np
from scripts.backtest.strategy_adapter import get_adapter
from scripts.backtest.wf_runner import run_wf

adapter = get_adapter()
original_v68_params = dict(adapter._risk_params.get('v68', {}))

# 权重扫描范围
weights = [0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3]

results = []
for w in weights:
    print(f"\n=== W_RECENT_LIMIT_3D = {w} ===")
    
    # 构建参数
    params = dict(original_v68_params)
    params['W_RECENT_LIMIT_3D'] = w
    
    # 调整其他权重使总和为1（保持原有比例缩放）
    base_keys = ['W_MOM', 'W_SIZE', 'W_ILLIQ', 'W_TURNOVER', 'W_PV_CORR']
    base_sum = sum(original_v68_params.get(k, 0) for k in base_keys)
    if base_sum > 0:
        scale = (1.0 - w) / base_sum
        for k in base_keys:
            params[k] = round(original_v68_params.get(k, 0) * scale, 4)
    
    weight_sum = sum(params.get(k, 0) for k in base_keys) + w
    print(f"权重总和: {weight_sum:.4f}")
    print(f"  W_MOM={params['W_MOM']}, W_SIZE={params['W_SIZE']}, W_ILLIQ={params['W_ILLIQ']}, W_RECENT_LIMIT_3D={w}")
    
    # 临时注入参数
    adapter._risk_params['v68'] = params
    
    # 运行WF
    try:
        result = run_wf(
            strategy_name='v68',
            train_days=252,
            test_days=126,
            step_days=63,
            start_date='2021-01-01',
            end_date='2026-06-24',
            full=False,
            pool_override='zz1800',
        )
        
        if result is not None and not result.empty:
            avg_ret = result['test_ret'].mean() * 100
            avg_sharpe = result['test_sharpe'].mean()
            avg_dd = result['test_dd'].mean() * 100
            pos_rate = (result['test_ret'] > 0).sum() / len(result) * 100
            
            results.append({
                'weight': w,
                'sharpe': avg_sharpe,
                'total_return': avg_ret,
                'win_rate': pos_rate,
                'max_drawdown': avg_dd,
            })
            print(f"  Sharpe={avg_sharpe:.3f}, Return={avg_ret:.1f}%, PosRate={pos_rate:.0f}%, MaxDD={avg_dd:.1f}%")
        else:
            print(f"  无结果")
            results.append({'weight': w, 'sharpe': 0, 'total_return': 0, 'win_rate': 0, 'max_drawdown': 0})
    except Exception as e:
        print(f"  错误: {e}")
        results.append({'weight': w, 'sharpe': 0, 'total_return': 0, 'win_rate': 0, 'max_drawdown': 0})

# 恢复原始参数
adapter._risk_params['v68'] = original_v68_params

# 汇总
print("\n" + "="*70)
print("v68 W_RECENT_LIMIT_3D 权重扫描结果 (16 folds, 2021-01-01 ~ 2026-06-24)")
print("="*70)
print(f"{'权重':>6} {'Sharpe':>8} {'收益':>8} {'正Fold':>6} {'最大回撤':>8}")
print("-"*70)
for r in sorted(results, key=lambda x: x['sharpe'], reverse=True):
    print(f"{r['weight']:>6.2f} {r['sharpe']:>8.3f} {r['total_return']:>7.1f}% {r['win_rate']:>5.0f}% {r['max_drawdown']:>7.1f}%")
