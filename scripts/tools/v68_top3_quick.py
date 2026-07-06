#!/usr/bin/env python3
"""v68 Top3参数 全量回测 + 分年统计"""
import sys, os
sys.path.insert(0, '/root/a-share-quant-sim')
import io, pandas as pd
from contextlib import redirect_stdout

from scripts.backtest.wf_runner import run_wf
from scripts.backtest.strategy_adapter import get_adapter

CONFIGS = [
    {"name": "#1 MOM=0.35 ILLIQ=0.15 SIZE=0.35", "W_MOM": 0.35, "W_ILLIQ": 0.15, "W_SIZE": 0.35},
    {"name": "#2 MOM=0.25 ILLIQ=0.0 SIZE=0.35",  "W_MOM": 0.25, "W_ILLIQ": 0.0,  "W_SIZE": 0.35},
    {"name": "#3 MOM=0.25 ILLIQ=0.05 SIZE=0.30", "W_MOM": 0.25, "W_ILLIQ": 0.05, "W_SIZE": 0.30},
]

def run_and_print(label, start, end):
    """跑一次回测并打印结果"""
    buf = io.StringIO()
    with redirect_stdout(buf):
        df = run_wf('v68', 252, 252, 252, start, end, full=True)
    ret = df['test_ret'].iloc[0] * 100
    sharpe = df['test_sharpe'].iloc[0]
    dd = df['test_dd'].iloc[0] * 100
    print(f"    {label}: 收益={ret:+.1f}% 夏普={sharpe:.3f} 回撤={dd:.1f}%")

def main():
    adapter = get_adapter()
    
    for cfg in CONFIGS:
        print(f"\n{'='*50}")
        print(f"  {cfg['name']}")
        print(f"{'='*50}")
        
        rp = adapter._risk_params['v68']
        rp['W_MOM'] = cfg['W_MOM']
        rp['W_ILLIQ'] = cfg['W_ILLIQ']
        rp['W_SIZE'] = cfg['W_SIZE']
        
        # 全量回测
        run_and_print("全量", '2021-01-01', '2026-06-24')
        
        # 分年
        print(f"  分年:")
        for year in range(2021, 2027):
            y_start = f"{year}-01-01"
            y_end = f"{year}-12-31" if year < 2026 else "2026-06-24"
            if y_start > '2026-06-24':
                break
            try:
                run_and_print(str(year), y_start, y_end)
            except Exception as e:
                print(f"    {year}: ERROR {str(e)[:50]}")

if __name__ == '__main__':
    main()
