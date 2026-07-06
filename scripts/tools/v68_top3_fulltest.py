#!/usr/bin/env python3
"""v68 Top3参数 全量回测 + 分年统计"""
import sys, os, json
sys.path.insert(0, '/root/a-share-quant-sim')

from scripts.backtest.wf_runner import run_wf
from scripts.backtest.strategy_adapter import get_adapter

CONFIGS = [
    {"name": "#1 MOM=0.35 ILLIQ=0.15 SIZE=0.35", "W_MOM": 0.35, "W_ILLIQ": 0.15, "W_SIZE": 0.35},
    {"name": "#2 MOM=0.25 ILLIQ=0.0 SIZE=0.35",  "W_MOM": 0.25, "W_ILLIQ": 0.0,  "W_SIZE": 0.35},
    {"name": "#3 MOM=0.25 ILLIQ=0.05 SIZE=0.30", "W_MOM": 0.25, "W_ILLIQ": 0.05, "W_SIZE": 0.30},
]

def run_yearly(strategy_name, start, end):
    """逐年跑全量回测，返回每年收益"""
    yearly = {}
    for year in range(2021, 2027):
        y_start = f"{year}-01-01"
        y_end = f"{year}-12-31" if year < 2026 else "2026-06-24"
        if y_start > end:
            break
        try:
            df = run_wf(strategy_name, 252, 252, 252, y_start, y_end, full=True)
            if df is not None and len(df) > 0:
                ret = df['test_ret'].mean() * 100
                sharpe = df['test_sharpe'].mean()
                dd = df['test_dd'].mean() * 100
                yearly[year] = {"ret": round(ret, 2), "sharpe": round(sharpe, 3), "dd": round(dd, 2)}
        except Exception as e:
            yearly[year] = {"error": str(e)}
    return yearly

def main():
    adapter = get_adapter()
    
    for cfg in CONFIGS:
        print(f"\n{'='*60}")
        print(f"  {cfg['name']}")
        print(f"{'='*60}")
        
        # 设置参数
        rp = adapter._risk_params['v68']
        rp['W_MOM'] = cfg['W_MOM']
        rp['W_ILLIQ'] = cfg['W_ILLIQ']
        rp['W_SIZE'] = cfg['W_SIZE']
        
        # 全量回测
        df = run_wf('v68', 252, 126, 63, '2021-01-01', '2026-06-24', full=True)
        total_ret = df['test_ret'].mean() * 100
        sharpe = df['test_sharpe'].mean()
        dd = df['test_dd'].mean() * 100
        pos_rate = (df['test_ret'] > 0).sum() / len(df) * 100
        
        print(f"  全量: 收益={total_ret:.1f}% 夏普={sharpe:.3f} 回撤={dd:.1f}% 正={pos_rate:.0f}%")
        
        # 分年统计
        print(f"  分年:")
        yearly = run_yearly('v68', '2021-01-01', '2026-06-24')
        for year, stats in sorted(yearly.items()):
            if 'error' in stats:
                print(f"    {year}: ERROR {stats['error'][:40]}")
            else:
                print(f"    {year}: 收益={stats['ret']:+.1f}% 夏普={stats['sharpe']:.3f} 回撤={stats['dd']:.1f}%")

if __name__ == '__main__':
    main()
