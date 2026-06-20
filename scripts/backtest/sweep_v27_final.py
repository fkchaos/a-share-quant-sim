#!/usr/bin/env python3
"""
scripts/backtest/sweep_v27_final.py — v27 最终全组合扫描
=========================================================
基于 Round 1 最优值，做全组合网格扫描

Round 1 最优: SL=-0.015, TP=0.03, HOLD_MAX=7, HOLD_EXT=5, MOM=0.07

扫描空间（每个参数 ±1 步长）:
  STOP_LOSS:    -0.015, -0.02
  TAKE_PROFIT:  0.03, 0.05
  HOLD_DAYS_MAX: 5, 7
  HOLD_DAYS_EXTEND: 5, 7
  MOM_THRESHOLD: 0.05, 0.07, 0.08

共 2×2×2×2×3 = 48 组
"""
import sys
import os
import time
import itertools

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.backtest.wf_runner import run_wf
from scripts.backtest.strategy_adapter import get_adapter

# ── WF 配置 ──────────────────────────────────────────────────────
TRAIN_DAYS = 252
TEST_DAYS  = 252
STEP_DAYS  = 252
START_DATE = "2021-01-01"
END_DATE   = "2026-05-31"

# ── 扫描空间 ─────────────────────────────────────────────────────
PARAM_SPACE = {
    "STOP_LOSS":    [-0.015, -0.02],
    "TAKE_PROFIT":  [0.03, 0.05],
    "HOLD_DAYS_MAX": [5, 7],
    "HOLD_DAYS_EXTEND": [5, 7],
    "MOM_THRESHOLD": [0.05, 0.07, 0.08],
}

# 固定参数
FIXED = {
    "HOLD_DAYS_MIN": 1,
    "HOLD_DAYS_EXTEND_PNL": 0.03,
    "MAX_DAILY_BUY": 4,
    "MAX_POSITION": 0.20,
    "MAX_HOLDINGS": 8,
}

keys = list(PARAM_SPACE.keys())
combos = list(itertools.product(*[PARAM_SPACE[k] for k in keys]))
total = len(combos)

print("=" * 70)
print(f"v27 最终全组合扫描: {total} 组")
print(f"  WF: train={TRAIN_DAYS}, test={TEST_DAYS}, step={STEP_DAYS}")
print(f"  区间: {START_DATE} ~ {END_DATE}")
print(f"  参数空间: {PARAM_SPACE}")
print(f"  固定参数: {FIXED}")
print("=" * 70)

adapter = get_adapter()
results = []
total_t0 = time.time()

for i, combo in enumerate(combos):
    overrides = dict(zip(keys, combo))
    overrides.update(FIXED)

    # 格式化标签
    label = (f"SL={overrides['STOP_LOSS']:.3f} TP={overrides['TAKE_PROFIT']:.2f} "
             f"H={overrides['HOLD_DAYS_MAX']} E={overrides['HOLD_DAYS_EXTEND']} M={overrides['MOM_THRESHOLD']:.2f}")

    print(f"\n[{i+1}/{total}] {label}")

    # 注入参数
    for k, v in overrides.items():
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
            "label": label,
            "params": dict(overrides),
            "avg_ret": avg_ret,
            "avg_sharpe": avg_sharpe,
            "avg_dd": avg_dd,
            "pos_folds": f"{pos_folds}/{total_folds}({pos_pct:.0f}%)",
            "time_s": f"{elapsed:.0f}s",
        })
        print(f"  → 夏普={avg_sharpe:.3f}, 收益={avg_ret:.1f}%, 回撤={avg_dd:.1f}%, 正收益={pos_folds}/{total_folds}, 耗时={elapsed:.0f}s")
    else:
        results.append({"label": label, "params": dict(overrides), "avg_sharpe": "N/A"})
        print(f"  → 无结果")

# ── 汇总 ──────────────────────────────────────────────────────────
total_elapsed = time.time() - total_t0

print(f"\n{'='*70}")
print(f"全组合扫描汇总（按夏普降序）  总耗时: {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")
print(f"{'='*70}")
print(f"{'参数组合':>50} | {'夏普':>8} | {'收益':>8} | {'回撤':>8} | {'正收益':>10} | {'耗时':>6}")
print("-" * 100)

results_sorted = sorted(results, key=lambda x: float(x.get("avg_sharpe", "-999")), reverse=True)
for r in results_sorted:
    sharpe = r.get("avg_sharpe", "N/A")
    avg_ret = r.get("avg_ret", "N/A")
    avg_dd = r.get("avg_dd", "N/A")
    print(f"{r['label']:>50} | {sharpe:>8} | {avg_ret:>7.1f}% | {avg_dd:>7.1f}% | "
          f"{r.get('pos_folds','N/A'):>10} | {r.get('time_s','N/A'):>6}")

if results_sorted:
    best = results_sorted[0]
    print(f"\n{'='*70}")
    print(f"★ 全局最优组合: {best['label']}")
    print(f"  夏普={best.get('avg_sharpe','N/A')}, 收益={best.get('avg_ret','N/A'):.1f}%, 回撤={best.get('avg_dd','N/A'):.1f}%")
    print(f"  参数: {best['params']}")
    print(f"{'='*70}")

print(f"\nEXIT:0")
