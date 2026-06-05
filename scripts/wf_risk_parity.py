#!/usr/bin/env python3
"""WF 验证 risk_parity vs equal"""
import sys, os, time, numpy as np
sys.path.insert(0, '/root/a-share-quant-sim')
os.environ['BACKTEST_DATA_DIR'] = '/root/data'

from scripts.run_backtest import walk_forward
from core.config import STRATEGY_PROFILES
from core.data import load_and_build_panel
from core.strategy import StrategyEngine

# 加载数据
print("加载数据...")
loaded, codes = load_and_build_panel('2021-01-01', '2026-05-31', need_open=True, need_hl=True)
close_panel = loaded[0]
volume_panel = loaded[1]
amount_panel = loaded[2]
high_panel = loaded[3]
low_panel = loaded[4]
print(f"Panel: {close_panel.shape}")

# 构建评分引擎
profile = STRATEGY_PROFILES['v11b_zz800_union']
engine = StrategyEngine(profile=profile.label, mode='ensemble')

results = {}

for method in ['equal', 'risk_parity']:
    print(f"\n{'='*60}")
    print(f"WF: weight_method={method}")
    print(f"{'='*60}")
    
    t0 = time.time()
    
    wf_results, combined_nav = walk_forward(
        close_panel=close_panel,
        score_fn=engine.score_panel,
        volume_panel=volume_panel,
        amount_panel=amount_panel,
        high_panel=high_panel,
        low_panel=low_panel,
        top_n=profile.top_n,
        rebalance_freq=profile.rebalance_freq,
        stop_loss=profile.stop_loss,
        max_position=profile.max_position,
        label=f'v11b_{method}',
        max_industry_weight=profile.max_industry_weight,
        run_kwargs={'weight_method': method},
    )
    elapsed = time.time() - t0
    
    rets = [r['ann_return'] for r in wf_results]
    sharpes = [r['sharpe'] for r in wf_results]
    dds = [r['max_dd'] for r in wf_results]
    positive = sum(1 for r in rets if r > 0)
    
    results[method] = {
        'avg_ret': np.mean(rets),
        'avg_sharpe': np.mean(sharpes),
        'avg_dd': np.mean(dds),
        'positive_folds': positive,
        'total_folds': len(wf_results),
        'wf_results': wf_results,
    }
    
    print(f"耗时: {elapsed:.1f}s")
    print(f"平均年化: {np.mean(rets):.1%}")
    print(f"平均夏普: {np.mean(sharpes):.2f}")
    print(f"平均回撤: {np.mean(dds):.1%}")
    print(f"正收益fold: {positive}/{len(wf_results)}")
    
    # 打印每 fold 详情
    for r in wf_results:
        print(f"  Fold {r['fold']}: {r['test']} | Ret={r['ann_return']:.1%} Sharpe={r['sharpe']:.2f} DD={r['max_dd']:.1%}")

# 对比
print(f"\n{'='*60}")
print("WF 对比总结")
print(f"{'='*60}")
eq = results['equal']
rp = results['risk_parity']
print(f"{'指标':<15} {'equal':>12} {'risk_parity':>12} {'差异':>12}")
print(f"{'-'*51}")
print(f"{'平均年化':<15} {eq['avg_ret']:>11.1%} {rp['avg_ret']:>11.1%} {rp['avg_ret']-eq['avg_ret']:>+11.1%}")
print(f"{'平均夏普':<15} {eq['avg_sharpe']:>12.2f} {rp['avg_sharpe']:>12.2f} {rp['avg_sharpe']-eq['avg_sharpe']:>+12.2f}")
print(f"{'平均回撤':<15} {eq['avg_dd']:>11.1%} {rp['avg_dd']:>11.1%} {rp['avg_dd']-eq['avg_dd']:>+11.1%}")
print(f"{'正收益fold':<15} {eq['positive_folds']:>5}/{eq['total_folds']:<6} {rp['positive_folds']:>5}/{rp['total_folds']:<6}")
