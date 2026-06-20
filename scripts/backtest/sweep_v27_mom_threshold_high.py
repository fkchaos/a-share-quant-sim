#!/usr/bin/env python3
"""
scripts/backtest/sweep_v27_mom_threshold_high.py — v27 MOM_THRESHOLD 高值扫描
==============================================================================
补充扫描 MOM_THRESHOLD = 0.05~0.10，确认趋势是否收敛
"""
import sys
import os
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.backtest.wf_runner import run_wf
from scripts.backtest.strategy_adapter import get_adapter

# ── 扫描参数 ──────────────────────────────────────────────────────
MOM_VALUES = [0.05, 0.06, 0.07, 0.08, 0.10]

# ── WF 配置 ──────────────────────────────────────────────────────
TRAIN_DAYS = 252
TEST_DAYS  = 252
STEP_DAYS  = 252
START_DATE = "2021-01-01"
END_DATE   = "2026-05-31"

print("=" * 60)
print("v27 MOM_THRESHOLD 高值扫描（0.05~0.10）")
print(f"  扫描值: {MOM_VALUES}")
print(f"  WF: train={TRAIN_DAYS}, test={TEST_DAYS}, step={STEP_DAYS}")
print(f"  区间: {START_DATE} ~ {END_DATE}")
print("=" * 60)

results = []
adapter = get_adapter()

for mv in MOM_VALUES:
    print(f"\n{'='*60}")
    print(f"MOM_THRESHOLD = {mv:.3f}")
    print(f"{'='*60}")

    adapter._risk_params["v27"]["MOM_THRESHOLD"] = mv

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
            "MOM_THRESHOLD": mv,
            "avg_ret": avg_ret,
            "avg_sharpe": avg_sharpe,
            "avg_dd": avg_dd,
            "pos_folds": f"{pos_folds}/{total_folds}({pos_pct:.0f}%)",
            "time_s": f"{elapsed:.0f}s",
        })
        print(f"  → 夏普={avg_sharpe:.3f}, 正收益={pos_folds}/{total_folds}({pos_pct:.0f}%), 回撤={avg_dd:.1f}%, 耗时={elapsed:.0f}s")
    else:
        results.append({"MOM_THRESHOLD": mv, "avg_sharpe": "N/A", "note": "无结果"})

# 恢复默认
adapter._risk_params["v27"]["MOM_THRESHOLD"] = 0.05

# ── 汇总 ──────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("高值扫描汇总（按夏普降序）")
print(f"{'='*60}")
print(f"{'MOM_THRESHOLD':>15} | {'平均夏普':>8} | {'平均收益':>8} | {'平均回撤':>8} | {'正收益fold':>12} | {'耗时':>6}")
print("-" * 80)

results_sorted = sorted(results, key=lambda x: float(x.get("avg_sharpe", "-999")), reverse=True)
for r in results_sorted:
    sharpe = r.get("avg_sharpe", "N/A")
    avg_ret = r.get("avg_ret", "N/A")
    avg_dd = r.get("avg_dd", "N/A")
    print(f"{r['MOM_THRESHOLD']:>15.3f} | {sharpe:>8} | {avg_ret:>7.2f}% | {avg_dd:>7.1f}% | "
          f"{r.get('pos_folds','N/A'):>12} | {r.get('time_s','N/A'):>6}")

if results_sorted:
    best = results_sorted[0]
    print(f"\n★ 最优: MOM_THRESHOLD = {best['MOM_THRESHOLD']} (夏普={best.get('avg_sharpe','N/A')})")

# 与低值合并对比
print(f"\n{'='*60}")
print("全范围对比（低值 0.01~0.05 + 高值 0.05~0.10）")
print(f"{'='*60}")
print("低值区结果（来自前次扫描）：")
low_results = [
    (0.010, 5.718), (0.015, 5.769), (0.020, 5.851), (0.025, 5.888),
    (0.030, 5.850), (0.040, 6.019), (0.050, 6.028),
]
for mv, sh in low_results:
    marker = " ← 前次最优" if mv == 0.05 else ""
    print(f"  {mv:.3f}: 夏普={sh:.3f}{marker}")

print("高值区结果（本次扫描）：")
for r in results_sorted:
    mv = r["MOM_THRESHOLD"]
    sh = r.get("avg_sharpe", "N/A")
    marker = " ← 本次最优" if r is results_sorted[0] else ""
    print(f"  {mv:.3f}: 夏普={sh}{marker}")

print("=" * 60)
