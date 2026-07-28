#!/usr/bin/env python3
"""
v69 择时因子扫描：测试不同择时组合的WF表现
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.backtest.wf_runner import run_wf
from scripts.backtest.strategy_adapter import get_adapter

adapter = get_adapter()

# 定义择时组合
REGIMES = {
    "no_timing": {
        "SENTIMENT_ENABLED": False,
    },
    "ma_only": {
        "SENTIMENT_ENABLED": False,
        "REGIME_ENABLED": True,
    },
    "breadth_only": {
        "SENTIMENT_ENABLED": False,
        "REGIME_ENABLED": False,
        "USE_AD_RATIO": True,
    },
    "vol_only": {
        "SENTIMENT_ENABLED": False,
        "REGIME_ENABLED": False,
        "USE_VOL_REGIME": True,
    },
    "volume_only": {
        "SENTIMENT_ENABLED": False,
        "REGIME_ENABLED": False,
        "USE_VOLUME_TREND": True,
    },
    "ma_breadth": {
        "SENTIMENT_ENABLED": False,
        "REGIME_ENABLED": True,
        "USE_AD_RATIO": True,
    },
    "ma_vol": {
        "SENTIMENT_ENABLED": False,
        "REGIME_ENABLED": True,
        "USE_VOL_REGIME": True,
    },
    "ma_volume": {
        "SENTIMENT_ENABLED": False,
        "REGIME_ENABLED": True,
        "USE_VOLUME_TREND": True,
    },
    "ma_breadth_vol": {
        "SENTIMENT_ENABLED": False,
        "REGIME_ENABLED": True,
        "USE_AD_RATIO": True,
        "USE_VOL_REGIME": True,
    },
    "all_timing": {
        "SENTIMENT_ENABLED": False,
        "REGIME_ENABLED": True,
        "USE_AD_RATIO": True,
        "USE_VOL_REGIME": True,
        "USE_VOLUME_TREND": True,
    },
}

print(f"{'组合':<20} {'夏普':>8} {'收益':>8} {'回撤':>8} {'正fold':>8} {'耗时':>6}")
print("-" * 70)

for name, overrides in REGIMES.items():
    # 注入参数
    for k, v in overrides.items():
        adapter._risk_params['v69'][k] = v

    t0 = time.time()
    try:
        result = run_wf('v69', train_days=252, test_days=126, step_days=63,
                       start_date='2021-01-01', end_date='2026-06-24',
                       pool_override='zz1800')
        avg_sharpe = result['test_sharpe'].mean()
        avg_ret = result['test_ret'].mean()
        avg_dd = result['test_dd'].mean()
        pos_folds = (result['test_sharpe'] > 0).sum()
        total_folds = len(result)
        elapsed = time.time() - t0
        print(f"{name:<20} {avg_sharpe:>8.3f} {avg_ret:>7.1f}% {avg_dd:>7.1f}% {pos_folds}/{total_folds:<5} {elapsed:>5.0f}s")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"{name:<20} ERROR: {e} ({elapsed:.0f}s)")

# 恢复默认
for k in list(adapter._risk_params['v69'].keys()):
    if k.startswith('USE_'):
        adapter._risk_params['v69'].pop(k, None)
