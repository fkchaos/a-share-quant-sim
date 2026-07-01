#!/usr/bin/env python3
"""v70 参数扫描脚本"""

import subprocess
import sys
import json
from datetime import datetime

# 测试不同的因子权重组合
configs = [
    # (name, W_MOM, W_QUALITY, W_LOW_VOL, W_REVERSAL)
    ('v70_c1', 0.20, 0.20, 0.20, 0.40),  # 反转为主
    ('v70_c2', 0.10, 0.10, 0.10, 0.70),  # 极端反转
    ('v70_c3', 0.40, 0.10, 0.10, 0.40),  # 动量+反转
    ('v70_c4', 0.00, 0.20, 0.20, 0.60),  # 纯反转+质量+低波
    ('v70_c5', 0.30, 0.30, 0.30, 0.10),  # 传统因子为主
]

results = []

for name, w_mom, w_qual, w_vol, w_rev in configs:
    print(f'\n{"="*60}')
    print(f'测试配置: {name}')
    print(f'权重: 动量{w_mom:.0%} + 质量{w_qual:.0%} + 低波{w_vol:.0%} + 反转{w_rev:.0%}')
    print(f'{"="*60}')
    
    # 构建参数
    params = json.dumps({
        'W_MOM': w_mom,
        'W_QUALITY': w_qual,
        'W_LOW_VOL': w_vol,
        'W_REVERSAL': w_rev,
    })
    
    # 运行WF
    cmd = [
        'python3', 'scripts/backtest/wf_runner.py',
        '--strategy', 'v70',
        '--pool', 'zz1800',
        '--start', '2021-01-01',
        '--end', '2026-06-24',
        '--train', '252',
        '--test', '126',
        '--step', '63',
        '--params', params,
    ]
    
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        output = result.stdout + result.stderr
        
        # 提取关键指标
        ret = 'N/A'
        sharpe = 'N/A'
        win_fold = 'N/A'
        
        for line in output.split('\n'):
            if '测试期平均收益率' in line:
                ret = line.split(':')[1].strip()
            elif '测试期平均夏普' in line:
                sharpe = line.split(':')[1].strip()
            elif '正收益 fold' in line:
                win_fold = line.split(':')[1].strip()
        
        results.append({
            'name': name,
            'config': f'动量{w_mom:.0%}+质量{w_qual:.0%}+低波{w_vol:.0%}+反转{w_rev:.0%}',
            'return': ret,
            'sharpe': sharpe,
            'win_fold': win_fold,
        })
        
        print(f'结果: 收益{ret}, 夏普{sharpe}, 正fold{win_fold}')
        
    except subprocess.TimeoutExpired:
        print(f'超时，跳过')
        results.append({
            'name': name,
            'config': f'动量{w_mom:.0%}+质量{w_qual:.0%}+低波{w_vol:.0%}+反转{w_rev:.0%}',
            'return': 'TIMEOUT',
            'sharpe': 'TIMEOUT',
            'win_fold': 'TIMEOUT',
        })
    except Exception as e:
        print(f'错误: {e}')
        results.append({
            'name': name,
            'config': f'动量{w_mom:.0%}+质量{w_qual:.0%}+低波{w_vol:.0%}+反转{w_rev:.0%}',
            'return': 'ERROR',
            'sharpe': 'ERROR',
            'win_fold': 'ERROR',
        })

# 汇总结果
print('\n' + '='*60)
print('v70 参数扫描汇总')
print('='*60)
print(f'{"配置":<30} {"收益率":<15} {"夏普":<15} {"正fold":<15}')
print('-'*60)
for r in results:
    print(f'{r["config"]:<30} {r["return"]:<15} {r["sharpe"]:<15} {r["win_fold"]:<15}')
print('='*60)
