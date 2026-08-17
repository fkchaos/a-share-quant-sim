#!/usr/bin/env python3
"""v75j广度卖出策略WF对比 — 通过标准wf_runner运行"""
import sys, os, time, json
sys.path.insert(0, '/root/a-share-quant-sim')

from scripts.backtest.wf_runner import run_wf
from scripts.backtest.strategy_adapter import get_adapter

MODES = {
    'A': {'label': '现状(不卖)', 'mode': ''},
    'B': {'label': '广度<30%全清', 'mode': 'full'},
    'C': {'label': '广度<30%减半', 'mode': 'half'},
    'D': {'label': '广度<30%收紧止损-5%', 'mode': 'tight_stop'},
}

results = {}
for key, cfg in MODES.items():
    print(f"\n{'='*60}")
    print(f"方案{key}: {cfg['label']}")
    print(f"{'='*60}")

    # 注入BREADTH_SELL_MODE参数
    adapter = get_adapter()
    if cfg['mode']:
        adapter._risk_params['v75j']['BREADTH_SELL_MODE'] = cfg['mode']
    else:
        adapter._risk_params['v75j'].pop('BREADTH_SELL_MODE', None)

    t0 = time.time()
    r = run_wf('v75j', train_days=252, test_days=126, step_days=63,
               start_date='2021-01-01', end_date='2026-05-31')
    elapsed = time.time() - t0

    if r:
        results[key] = {
            'label': cfg['label'],
            'sharpe': r.get('sharpe', 0),
            'total': r.get('total', 0),
            'dd': r.get('dd', 0),
            'pos_rate': r.get('pos_rate', 0),
            'n_folds': r.get('n_folds', 0),
        }
        print(f"\n  结果: Sharpe={r.get('sharpe',0):.3f} Return={r.get('total',0):+.1f}% DD={r.get('dd',0):.1f}% Folds={r.get('n_folds',0)} PosRate={r.get('pos_rate',0):.1f}%")
    print(f"  耗时 {elapsed:.1f}s")

# 汇总对比
print(f"\n\n{'='*70}")
print("v75j 广度卖出策略 WF 对比汇总")
print(f"{'='*70}")
print(f"{'方案':<25} {'Sharpe':>8} {'收益':>8} {'回撤':>8} {'正fold率':>8}")
print("-" * 70)
for key in ['A', 'B', 'C', 'D']:
    if key in results:
        r = results[key]
        print(f"{r['label']:<25} {r['sharpe']:>8.3f} {r['total']:>+7.1f}% {r['dd']:>7.1f}% {r['pos_rate']:>7.1f}%")
print("-" * 70)
