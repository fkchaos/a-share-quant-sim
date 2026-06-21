#!/usr/bin/env python3
"""
scripts/backtest/sweep_v27_regime_bcde.py — v27 Regime 方案 B/C/D/E 综合扫描
========================================================================
在 calc_regime 中注入 REGIME_MODE 参数，测试四种方案：

方案B: linear — slope 线性映射到仓位乘数
方案C: 3class + REGIME_INDEX 切换指数（需要中证500数据）
方案D: vol — 波动率过滤
方案E: 3class + 熊市减仓（通过 MAX_HOLDINGS 和 MAX_DAILY_BUY 调节）

对比基准: 3class + ST=0（当前默认）

每组只跑1个WF fold（step=252），加快速度。
"""
import sys
import os
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.backtest.wf_runner import run_wf
from scripts.backtest.strategy_adapter import get_adapter

# ── WF 配置（只跑1个fold加速） ────────────────────────────────────
TRAIN_DAYS = 252
TEST_DAYS  = 252
STEP_DAYS  = 504  # 大步长，只产生1个fold
START_DATE = "2021-01-01"
END_DATE   = "2026-05-31"

# 固定风控参数
FIXED_RISK = {
    "STOP_LOSS":         -0.015,
    "TAKE_PROFIT":       0.03,
    "HOLD_DAYS_MAX":     5,
    "HOLD_DAYS_EXTEND":  5,
    "HOLD_DAYS_MIN":     1,
    "HOLD_DAYS_EXTEND_PNL": 0.03,
    "MAX_DAILY_BUY":     4,
    "MAX_POSITION":      0.20,
    "MAX_HOLDINGS":      8,
    "MOM_THRESHOLD":     0.05,
}

# ── 实验组定义 ────────────────────────────────────────────────────
EXPERIMENTS = [
    # 基准
    {
        "name": "基准(3class)",
        "regime": {
            "REGIME_ENABLED": True,
            "REGIME_MODE": "3class",
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "SLOPE_THRESHOLD": 0.0,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_SIDEWAYS_ALLOC": 0.7,
            "REGIME_BEAR_ALLOC": 0.3,
        },
        "risk": {},
    },
    # 方案B: linear 连续映射
    {
        "name": "B-linear",
        "regime": {
            "REGIME_ENABLED": True,
            "REGIME_MODE": "linear",
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "REGIME_SLOPE_CAP": 0.01,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_BEAR_ALLOC": 0.3,
        },
        "risk": {},
    },
    # 方案B: linear + 更激进的bear
    {
        "name": "B-linear-aggressive",
        "regime": {
            "REGIME_ENABLED": True,
            "REGIME_MODE": "linear",
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "REGIME_SLOPE_CAP": 0.008,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_BEAR_ALLOC": 0.1,
        },
        "risk": {},
    },
    # 方案D: vol 波动率过滤
    {
        "name": "D-vol-filter",
        "regime": {
            "REGIME_ENABLED": True,
            "REGIME_MODE": "vol",
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "SLOPE_THRESHOLD": 0.0,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_SIDEWAYS_ALLOC": 0.7,
            "REGIME_BEAR_ALLOC": 0.3,
            "REGIME_VOL_FILTER": True,
            "REGIME_VOL_WINDOW": 20,
            "REGIME_VOL_THRESHOLD": 1.5,
        },
        "risk": {},
    },
    # 方案D: vol + 更敏感
    {
        "name": "D-vol-sensitive",
        "regime": {
            "REGIME_ENABLED": True,
            "REGIME_MODE": "vol",
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "SLOPE_THRESHOLD": 0.0,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_SIDEWAYS_ALLOC": 0.7,
            "REGIME_BEAR_ALLOC": 0.3,
            "REGIME_VOL_FILTER": True,
            "REGIME_VOL_WINDOW": 10,
            "REGIME_VOL_THRESHOLD": 1.3,
        },
        "risk": {},
    },
    # 方案E: 熊市减仓（通过风控参数）
    {
        "name": "E-bear-reduce",
        "regime": {
            "REGIME_ENABLED": True,
            "REGIME_MODE": "3class",
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "SLOPE_THRESHOLD": 0.0,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_SIDEWAYS_ALLOC": 0.7,
            "REGIME_BEAR_ALLOC": 0.3,
        },
        "risk": {
            "MAX_HOLDINGS": 4,      # 熊市最多4只
            "MAX_DAILY_BUY": 2,     # 熊市每天最多买2只
        },
    },
    # 方案B+D: linear + vol
    {
        "name": "BD-linear-vol",
        "regime": {
            "REGIME_ENABLED": True,
            "REGIME_MODE": "linear",
            "REGIME_MA_PERIOD": 20,
            "REGIME_SLOPE_DAYS": 5,
            "REGIME_SLOPE_CAP": 0.01,
            "REGIME_BULL_ALLOC": 1.0,
            "REGIME_BEAR_ALLOC": 0.3,
            "REGIME_VOL_FILTER": True,
            "REGIME_VOL_WINDOW": 20,
            "REGIME_VOL_THRESHOLD": 1.5,
        },
        "risk": {},
    },
]

print("=" * 70)
print(f"v27 Regime B/C/D/E 综合扫描: {len(EXPERIMENTS)} 组")
print(f"  WF: train={TRAIN_DAYS}, test={TEST_DAYS}, step={STEP_DAYS}")
print(f"  区间: {START_DATE} ~ {END_DATE}")
print("=" * 70)

adapter = get_adapter()
results = []
total_t0 = time.time()

for i, exp in enumerate(EXPERIMENTS):
    name = exp["name"]
    print(f"\n[{i+1}/{len(EXPERIMENTS)}] {name}")

    # 注入 regime 参数
    for k, v in exp["regime"].items():
        adapter._regime_params["v27"][k] = v

    # 注入风控参数（方案E覆盖）
    risk_params = dict(FIXED_RISK)
    for k, v in exp["risk"].items():
        risk_params[k] = v
    for k, v in risk_params.items():
        adapter._risk_params["v27"][k] = v

    t0 = time.time()
    df = run_wf("v27", TRAIN_DAYS, TEST_DAYS, STEP_DAYS, START_DATE, END_DATE)
    elapsed = time.time() - t0

    if df is not None and len(df) > 0:
        avg_ret = df["test_ret"].mean() * 100
        avg_sharpe = df["test_sharpe"].mean()
        avg_dd = df["test_dd"].mean() * 100
        pos_folds = (df["test_ret"] > 0).sum()
        total_folds = len(df)
        pos_pct = pos_folds / total_folds * 100

        results.append({
            "name": name,
            "avg_ret": avg_ret,
            "avg_sharpe": avg_sharpe,
            "avg_dd": avg_dd,
            "pos_folds": f"{pos_folds}/{total_folds}({pos_pct:.0f}%)",
            "time_s": f"{elapsed:.0f}s",
        })
        print(f"  → 夏普={avg_sharpe:.3f}, 收益={avg_ret:.1f}%, 回撤={avg_dd:.1f}%, 正收益={pos_folds}/{total_folds}, 耗时={elapsed:.0f}s")
    else:
        results.append({"name": name, "avg_sharpe": "N/A"})
        print(f"  → 无结果")

# ── 汇总 ──────────────────────────────────────────────────────────
total_elapsed = time.time() - total_t0

print(f"\n{'='*70}")
print(f"B/C/D/E 扫描汇总（按夏普降序）  总耗时: {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")
print(f"{'='*70}")
print(f"{'方案':>25} | {'夏普':>8} | {'收益':>8} | {'回撤':>8} | {'正收益':>10} | {'耗时':>6}")
print("-" * 85)

results_sorted = sorted(results, key=lambda x: float(x.get("avg_sharpe", "-999")), reverse=True)
for r in results_sorted:
    sharpe = r.get("avg_sharpe", "N/A")
    avg_ret = r.get("avg_ret", "N/A")
    avg_dd = r.get("avg_dd", "N/A")
    print(f"{r['name']:>25} | {sharpe:>8} | {avg_ret:>7.1f}% | {avg_dd:>7.1f}% | "
          f"{r.get('pos_folds','N/A'):>10} | {r.get('time_s','N/A'):>6}")

if results_sorted:
    best = results_sorted[0]
    print(f"\n{'='*70}")
    print(f"★ 最优方案: {best['name']}")
    print(f"  夏普={best.get('avg_sharpe','N/A')}, 收益={best.get('avg_ret','N/A'):.1f}%, 回撤={best.get('avg_dd','N/A'):.1f}%")
    print(f"{'='*70}")

# 恢复默认参数
adapter._regime_params["v27"] = {
    "REGIME_ENABLED": True,
    "REGIME_MA_PERIOD": 20,
    "REGIME_SLOPE_DAYS": 5,
    "SLOPE_THRESHOLD": 0.0,
    "REGIME_BULL_ALLOC": 1.0,
    "REGIME_SIDEWAYS_ALLOC": 0.7,
    "REGIME_BEAR_ALLOC": 0.3,
}

print(f"\nEXIT:0")
