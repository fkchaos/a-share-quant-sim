#!/usr/bin/env python3
"""快速验证 risk_parity vs equal 权重分配效果"""
import sys, os, time
sys.path.insert(0, '/root/a-share-quant-sim')
os.environ['BACKTEST_DATA_DIR'] = '/root/data'

from scripts.run_backtest import run_backtest
from core.config import STRATEGY_PROFILES
from core.data import load_and_build_panel
from core.factors import calc_factors_panel
from core.strategy import StrategyEngine

# 加载数据
print("加载数据...")
loaded, codes = load_and_build_panel('2021-01-01', '2026-05-31', need_open=True, need_hl=True)
close_panel = loaded[0]
volume_panel = loaded[1]
amount_panel = loaded[2]
high_panel = loaded[3]
low_panel = loaded[4]
open_panel = loaded[5]
print(f"Panel: {close_panel.shape}")

# 计算因子
print("计算因子...")
factors = calc_factors_panel(close_panel, volume_panel, amount_panel, high_panel, low_panel)

# 构建评分
profile = STRATEGY_PROFILES['v11b_zz800_union']
engine = StrategyEngine(profile=profile.label, mode='ensemble')
score = engine.score_panel(factors)

results = {}

for method in ['equal', 'risk_parity']:
    print(f"\n{'='*60}")
    print(f"回测: weight_method={method}")
    print(f"{'='*60}")
    
    t0 = time.time()
    metrics, nav, trades = run_backtest(
        close_panel=close_panel,
        score=score,
        top_n=profile.top_n,
        rebalance_freq=profile.rebalance_freq,
        stop_loss=profile.stop_loss,
        max_position=profile.max_position,
        max_industry_weight=profile.max_industry_weight,
        weight_method=method,
        label=f'v11b_{method}',
        exec_timing='close',
    )
    elapsed = time.time() - t0
    
    results[method] = metrics
    print(f"耗时: {elapsed:.1f}s")
    print(f"年化: {metrics['annual_return']:.2%}")
    print(f"夏普: {metrics['sharpe_ratio']:.2f}")
    print(f"回撤: {metrics['max_drawdown']:.2%}")
    print(f"Sortino: {metrics['sortino_ratio']:.2f}")
    print(f"Calmar: {metrics['calmar_ratio']:.2f}")
    print(f"交易次数: {metrics['total_trades']}")

# 对比
print(f"\n{'='*60}")
print("对比总结")
print(f"{'='*60}")
eq = results['equal']
rp = results['risk_parity']
print(f"{'指标':<15} {'equal':>12} {'risk_parity':>12} {'差异':>12}")
print(f"{'-'*51}")
for key, fmt in [('annual_return', '.2%'), ('sharpe_ratio', '.2f'), ('max_drawdown', '.2%'), 
                 ('sortino_ratio', '.2f'), ('calmar_ratio', '.2f'), ('total_trades', 'd')]:
    ev = eq.get(key, 0)
    rv = rp.get(key, 0)
    diff = rv - ev
    if '%' in fmt:
        print(f"{key:<15} {ev:>11.2%} {rv:>11.2%} {diff:>+11.2%}")
    else:
        print(f"{key:<15} {ev:>12.2f} {rv:>12.2f} {diff:>+12.2f}")
