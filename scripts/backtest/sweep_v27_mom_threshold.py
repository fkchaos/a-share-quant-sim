#!/usr/bin/env python3
"""
scripts/backtest/sweep_v27_mom_threshold.py — v27 MOM_THRESHOLD 参数扫描
=========================================================================
通过修改 strategy_adapter 的 risk_params 注入 MOM_THRESHOLD
"""
import sys
import os
import time
import copy

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.backtest.wf_runner import run_wf
from scripts.backtest.strategy_adapter import get_adapter

# ── 扫描参数 ──────────────────────────────────────────────────────
MOM_VALUES = [0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.05]

# ── WF 配置 ──────────────────────────────────────────────────────
TRAIN_DAYS = 252
TEST_DAYS  = 252
STEP_DAYS  = 252
START_DATE = "2021-01-01"
END_DATE   = "2026-05-31"

print("=" * 60)
print("v27 MOM_THRESHOLD 参数扫描")
print(f"  扫描值: {MOM_VALUES}")
print(f"  WF: train={TRAIN_DAYS}, test={TEST_DAYS}, step={STEP_DAYS}")
print(f"  区间: {START_DATE} ~ {END_DATE}")
print("=" * 60)

# 先跑基线（MOM_THRESHOLD=0.02）
print(f"\n{'='*60}")
print(f"基线: MOM_THRESHOLD = 0.02")
print(f"{'='*60}")
t0 = time.time()
df_base = run_wf("v27", TRAIN_DAYS, TEST_DAYS, STEP_DAYS, START_DATE, END_DATE)
base_time = time.time() - t0

results = []
if df_base is not None and len(df_base) > 0:
    results.append({
        "MOM_THRESHOLD": 0.02,
        "avg_ret": df_base["test_ret"].mean() * 100,
        "avg_sharpe": df_base["test_sharpe"].mean(),
        "avg_dd": df_base["test_dd"].mean() * 100,
        "pos_folds": f"{(df_base['test_ret'] > 0).sum()}/{len(df_base)}({(df_base['test_ret'] > 0).mean()*100:.0f}%)",
        "time_s": f"{base_time:.0f}s",
        "note": "基线",
    })

# 扫描其他值
for mv in MOM_VALUES:
    if mv == 0.02:
        continue  # 基线已跑

    print(f"\n{'='*60}")
    print(f"MOM_THRESHOLD = {mv:.3f}")
    print(f"{'='*60}")

    # 注入 MOM_THRESHOLD 到 adapter
    adapter = get_adapter()
    adapter._risk_params["v27"]["MOM_THRESHOLD"] = mv

    t0 = time.time()
    # 直接调用 run_wf，但 adapter 已修改
    df = run_wf("v27", TRAIN_DAYS, TEST_DAYS, STEP_DAYS, START_DATE, END_DATE)
    elapsed = time.time() - t0

    if df is not None and len(df) > 0:
        results.append({
            "MOM_THRESHOLD": mv,
            "avg_ret": df["test_ret"].mean() * 100,
            "avg_sharpe": df["test_sharpe"].mean(),
            "avg_dd": df["test_dd"].mean() * 100,
            "pos_folds": f"{(df['test_ret'] > 0).sum()}/{len(df)}({(df['test_ret'] > 0).mean()*100:.0f}%)",
            "time_s": f"{elapsed:.0f}s",
        })
        print(f"  → 夏普={df['test_sharpe'].mean():.3f}, 正收益={(df['test_ret'] > 0).sum()}/{len(df)}, 耗时={elapsed:.0f}s")
    else:
        results.append({"MOM_THRESHOLD": mv, "avg_sharpe": "N/A", "note": "无结果"})

    # 恢复默认值
    adapter._risk_params["v27"]["MOM_THRESHOLD"] = 0.02

# ── 汇总 ──────────────────────────────────────────────────────────
print(f"\n{'='*60}")
print("扫描汇总（按夏普降序）")
print(f"{'='*60}")
print(f"{'MOM_THRESHOLD':>15} | {'平均夏普':>8} | {'平均收益':>8} | {'平均回撤':>8} | {'正收益fold':>12} | {'耗时':>6} | 备注")
print("-" * 90)

results_sorted = sorted(results, key=lambda x: float(x.get("avg_sharpe", "-999")), reverse=True)
for r in results_sorted:
    sharpe = r.get("avg_sharpe", "N/A")
    avg_ret = r.get("avg_ret", "N/A")
    avg_dd = r.get("avg_dd", "N/A")
    print(f"{r['MOM_THRESHOLD']:>15.3f} | {sharpe:>8} | "
          f"{avg_ret:>7.2f}% | {avg_dd:>7.1f}% | "
          f"{r.get('pos_folds','N/A'):>12} | {r.get('time_s','N/A'):>6} | {r.get('note','')}")

if results_sorted:
    best = results_sorted[0]
    print(f"\n★ 最佳参数: MOM_THRESHOLD = {best['MOM_THRESHOLD']} (夏普={best.get('avg_sharpe','N/A')})")
print("=" * 60)
