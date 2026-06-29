#!/usr/bin/env python3
"""v39g 参数扫描 — 串行,一次一个配置"""
import sys, os
sys.path.insert(0, '.')
from scripts.backtest.wf_runner import run_wf
from scripts.backtest.strategy_adapter import get_adapter

ADAPTER = get_adapter()
CONFIGS = [(3,0.05),(3,0.08),(3,0.10),(2,0.05),(4,0.05),(5,0.05),(2,0.03),(4,0.08),(5,0.10)]

def run_one(hold, tp):
    ADAPTER._risk_params['v39g']['HOLD_DAYS_MAX'] = hold
    ADAPTER._risk_params['v39g']['TAKE_PROFIT'] = tp
    r = run_wf('v39g', 252, 126, 63, '2021-01-01', '2026-06-24', pool_override='zz1800')
    return r

if __name__ == '__main__':
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    hold, tp = CONFIGS[idx]
    print(f'[{idx+1}/{len(CONFIGS)}] HOLD={hold} TP={tp:.0f}% ...')
    r = run_one(hold, tp)
    sharpe = r.get('avg_sharpe', 0)
    ret = r.get('avg_return', 0)
    dd = r.get('avg_drawdown', 0)
    nf = len(r.get('fold_results', []))
    pos = sum(1 for f in r.get('fold_results', []) if f.get('sharpe', 0) > 0)
    print(f'  => ret={ret:+.2f}% sharpe={sharpe:+.3f} dd={dd:.1f}% pos={pos}/{nf}')
    print(f'CONFIG_{idx}_RESULT: hold={hold} tp={tp} ret={ret} sharpe={sharpe} dd={dd} pos={pos} nf={nf}')
