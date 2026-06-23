#!/usr/bin/env python3
"""
scripts/experiments/test_v40_sell_modes.py — 对比 v40 不同卖出模式的 WF 结果
通过 monkey-patch DEFAULT_PARAMS 实现参数覆盖
"""
import subprocess
import re
import sys
import os

os.chdir('/root/a-share-quant-sim')

def run_wf_with_patch(strategy, params_patch=None):
    """跑 WF，可选地 patch 策略参数"""
    cmd = [sys.executable, '-c']
    
    if params_patch:
        patch_lines = '\n'.join([
            f'from scripts.strategies import v40_factor_exit as v40_mod',
            f'orig = v40_mod.DEFAULT_PARAMS.copy()',
        ])
        for k, v in params_patch.items():
            if isinstance(v, str):
                patch_lines += f'\nv40_mod.DEFAULT_PARAMS["{k}"] = "{v}"'
            else:
                patch_lines += f'\nv40_mod.DEFAULT_PARAMS["{k}"] = {v}'
        patch_lines += '\n'
        
        full_cmd = f'''
import sys
sys.path.insert(0, ".")
{patch_lines}
from scripts.backtest.wf_runner import run_wf
import argparse
sys.argv = ["wf_runner.py", "--strategy", "{strategy}", "--start", "2023-01-01", "--end", "2025-12-31"]
run_wf("{strategy}", 252, 252, 252, "2023-01-01", "2025-12-31", full=False)
'''
    else:
        full_cmd = f'''
import sys
sys.path.insert(0, ".")
from scripts.backtest.wf_runner import run_wf
run_wf("{strategy}", 252, 252, 252, "2023-01-01", "2025-12-31", full=False)
'''
    
    cmd.append(full_cmd)
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stdout + result.stderr
    
    ret_match = re.search(r'测试:\s+([\d.]+)%', output)
    dd_match = re.search(r'DD=([\d.]+)%', output)
    sharpe_match = re.search(r'Sharpe=([\d.]+)', output)
    folds_match = re.search(r'正收益 fold:\s+(\d+)/(\d+)', output)
    factor_decay_count = output.count('factor_decay')
    
    return {
        'return': float(ret_match.group(1)) if ret_match else None,
        'drawdown': float(dd_match.group(1)) if dd_match else None,
        'sharpe': float(sharpe_match.group(1)) if sharpe_match else None,
        'positive_folds': int(folds_match.group(1)) if folds_match else None,
        'total_folds': int(folds_match.group(2)) if folds_match else None,
        'factor_decay_count': factor_decay_count,
    }


if __name__ == '__main__':
    experiments = [
        ("v39c (baseline)", "v39c", None),
        ("v40 threshold=0.35 (default)", "v40", None),
        ("v40 threshold=0.25", "v40", {"SELL_THRESHOLD": 0.25}),
        ("v40 threshold=0.20", "v40", {"SELL_THRESHOLD": 0.20}),
        ("v40 threshold=0.15", "v40", {"SELL_THRESHOLD": 0.15}),
        ("v40 momentum drop=30%", "v40", {"SELL_MODE": "momentum", "MOMENTUM_DROP_PCT": 0.30}),
    ]
    
    print("=" * 72)
    print(f"{'实验':<32} {'收益':>8} {'回撤':>8} {'夏普':>8} {'Fold':>8} {'因子卖出':>8}")
    print("=" * 72)
    
    for name, strategy, patch in experiments:
        print(f"  运行: {name}...", end='', flush=True)
        result = run_wf_with_patch(strategy, patch)
        print(f"\r{name:<32} {result['return']:>7.2f}% {result['drawdown']:>7.2f}% {result['sharpe']:>8.3f} {result['positive_folds']}/{result['total_folds']:>5} {result['factor_decay_count']:>8}")
    
    print("=" * 72)
