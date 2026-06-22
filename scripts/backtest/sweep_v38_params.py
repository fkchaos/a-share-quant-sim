#!/usr/bin/env python3
"""
scripts/backtest/sweep_v38_params.py — v38 参数扫描
====================================================
基于 v38 v2 的基准参数，做精细参数扫描。

基准参数（已验证优于 v27）：
  MOM_THRESHOLD=0.06, PV_CORR_20_MIN=0.10, BOLL_W_MIN=0.8, MIN_AMOUNT=3000万
  COOLDOWN_DAYS=3, MAX_SAME_PREFIX=3

扫描空间（每个参数 ±1~2 步长）:
  MOM_THRESHOLD:    0.05, 0.06, 0.07
  PV_CORR_20_MIN:   0.05, 0.10, 0.15
  HOLD_DAYS_MAX:    3, 5, 7
  TAKE_PROFIT:      0.03, 0.05
  COOLDOWN_DAYS:    0, 3, 5

共 3×3×3×2×3 = 162 组
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
START_DATE = "2021-01-01"
END_DATE   = "2026-05-31"

# ── 扫描空间（精简版，每组约 4s，总计 ~5min）────────────────────
PARAM_SPACE = {
    "MOM_THRESHOLD":    [0.05, 0.06, 0.07],
    "PV_CORR_20_MIN":   [0.05, 0.10, 0.15],
    "HOLD_DAYS_MAX":    [5, 7],
    "TAKE_PROFIT":      [0.03, 0.05],
    "COOLDOWN_DAYS":    [0, 3],
}

# 固定参数
FIXED = {
    "STOP_LOSS": -0.015,
    "PV_CORR_10_MIN": -0.2,
    "BOLL_W_MIN": 0.8,
    "MIN_AMOUNT_DAYS": 30000000,
    "HOLD_DAYS_EXTEND": 5,
    "HOLD_DAYS_EXTEND_PNL": 0.03,
    "MAX_DAILY_BUY": 4,
    "MAX_POSITION": 0.20,
    "MAX_SAME_PREFIX": 3,
}

keys = list(PARAM_SPACE.keys())
combos = list(itertools.product(*[PARAM_SPACE[k] for k in keys]))
total = len(combos)

print("=" * 80)
print(f"v38 参数扫描: {total} 组")
print(f"  区间: {START_DATE} ~ {END_DATE}")
print(f"  扫描空间: {PARAM_SPACE}")
print(f"  固定参数: {FIXED}")
print("=" * 80)

adapter = get_adapter()
results = []
total_t0 = time.time()

for i, combo in enumerate(combos):
    overrides = dict(zip(keys, combo))
    overrides.update(FIXED)

    label = (f"MOM={overrides['MOM_THRESHOLD']:.2f} PV20={overrides['PV_CORR_20_MIN']:.2f} "
             f"H_MAX={overrides['HOLD_DAYS_MAX']} TP={overrides['TAKE_PROFIT']:.2f} CD={overrides['COOLDOWN_DAYS']}")

    print(f"\n[{i+1}/{total}] {label}")

    # 注入参数
    for k, v in overrides.items():
        adapter._risk_params["v38"][k] = v

    t0 = time.time()
    nav = run_wf("v38", 252, 252, 252, START_DATE, END_DATE, full=True)
    elapsed = time.time() - t0

    if nav is not None and len(nav) > 0:
        total_ret = nav.iloc[-1] / nav.iloc[0] - 1
        max_dd = ((nav.cummax() - nav) / nav.cummax()).max()
        daily_ret = nav.pct_change().dropna()
        sharpe = daily_ret.mean() / daily_ret.std() * (252 ** 0.5) if daily_ret.std() > 0 else 0

        # 统计交易次数
        # （估：从 NAV 变化推算不够精确，这里用 ret 的标准差作为代理指标）

        results.append({
            "label": label,
            "params": dict(overrides),
            "total_ret": total_ret * 100,
            "max_dd": max_dd * 100,
            "sharpe": sharpe,
            "time_s": f"{elapsed:.0f}s",
        })
        print(f"  → 收益={total_ret*100:.1f}%, 回撤={max_dd*100:.1f}%, 夏普={sharpe:.3f}, 耗时={elapsed:.0f}s")
    else:
        results.append({"label": label, "params": dict(overrides), "total_ret": -999, "max_dd": 999, "sharpe": -999, "time_s": f"{elapsed:.0f}s"})
        print(f"  → 无结果")

# ── 汇总 ──────────────────────────────────────────────────────────
total_elapsed = time.time() - total_t0

print(f"\n{'='*80}")
print(f"v38 参数扫描汇总（按夏普降序）  总耗时: {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")
print(f"{'='*80}")
print(f"{'参数组合':>55} | {'收益':>8} | {'回撤':>8} | {'夏普':>8} | {'耗时':>6}")
print("-" * 100)

results_sorted = sorted(results, key=lambda x: x["sharpe"], reverse=True)
for r in results_sorted[:20]:  # 只显示 top 20
    ret = r["total_ret"]
    dd = r["max_dd"]
    sharpe = r["sharpe"]
    if ret < -900:
        continue
    print(f"{r['label']:>55} | {ret:>7.1f}% | {dd:>7.1f}% | {sharpe:>8.3f} | {r['time_s']:>6}")

# ── 最优 ──────────────────────────────────────────────────────────
valid = [r for r in results_sorted if r["total_ret"] > -900]
if valid:
    best = valid[0]
    print(f"\n{'='*80}")
    print(f"★ 最优组合（按夏普）: {best['label']}")
    print(f"  收益={best['total_ret']:.1f}%, 回撤={best['max_dd']:.1f}%, 夏普={best['sharpe']:.3f}")
    print(f"  参数: {best['params']}")
    print(f"{'='*80}")

# ── 按收益排序 top 10 ─────────────────────────────────────────────
print(f"\n按收益排序 Top 10:")
by_ret = sorted(valid, key=lambda x: x["total_ret"], reverse=True)
for r in by_ret[:10]:
    print(f"  {r['label']:>55} | {r['total_ret']:>7.1f}% | {r['max_dd']:>7.1f}% | {r['sharpe']:>8.3f}")

print(f"\nEXIT:0")
