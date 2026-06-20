#!/usr/bin/env python3
"""
scripts/backtest/sweep_v27_sltp_hold.py — v27 SL/TP/持仓天数 参数扫描
=========================================================================
分两轮：
  Round 1: 单参数扫描（固定其他参数为默认值），每组 ~4 folds × ~60s
  Round 2: 最优组合精细扫描（可选）

默认值（当前代码）:
  STOP_LOSS = -0.02
  TAKE_PROFIT = 0.05
  HOLD_DAYS_MAX = 5
  HOLD_DAYS_EXTEND = 7
  HOLD_DAYS_EXTEND_PNL = 0.03

用法:
    python scripts/backtest/sweep_v27_sltp_hold.py          # Round 1 单参数扫描
    python scripts/backtest/sweep_v27_sltp_hold.py --round2 # Round 2 最优组合
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

# ── 默认参数 ─────────────────────────────────────────────────────
DEFAULTS = {
    "STOP_LOSS": -0.02,
    "TAKE_PROFIT": 0.05,
    "HOLD_DAYS_MAX": 5,
    "HOLD_DAYS_EXTEND": 7,
    "HOLD_DAYS_EXTEND_PNL": 0.03,
}

# ── Round 1: 单参数扫描空间 ──────────────────────────────────────
ROUND1 = {
    "STOP_LOSS": [-0.015, -0.02, -0.025, -0.03],
    "TAKE_PROFIT": [0.03, 0.05, 0.07, 0.10],
    "HOLD_DAYS_MAX": [3, 5, 7],
    "HOLD_DAYS_EXTEND": [5, 7, 10],
}


def run_single(params_overrides, label):
    """跑单组参数 WF，返回结果 dict"""
    adapter = get_adapter()
    # 恢复默认
    for k, v in DEFAULTS.items():
        adapter._risk_params["v27"][k] = v
    # 覆盖
    for k, v in params_overrides.items():
        adapter._risk_params["v27"][k] = v

    t0 = time.time()
    df = run_wf("v27", TRAIN_DAYS, TEST_DAYS, STEP_DAYS, START_DATE, END_DATE)
    elapsed = time.time() - t0

    if df is not None and len(df) > 0:
        return {
            "label": label,
            "params": dict(params_overrides),
            "avg_ret": df["test_ret"].mean() * 100,
            "avg_sharpe": df["test_sharpe"].mean(),
            "avg_dd": df["test_dd"].mean() * 100,
            "pos_folds": f"{(df['test_ret'] > 0).sum()}/{len(df)}({(df['test_ret'] > 0).mean()*100:.0f}%)",
            "time_s": f"{elapsed:.0f}s",
        }
    return {"label": label, "params": dict(params_overrides), "avg_sharpe": "N/A", "note": "无结果"}


def print_table(results, title):
    print(f"\n{'='*70}")
    print(title)
    print(f"{'='*70}")
    print(f"{'参数':>20} | {'夏普':>8} | {'收益':>8} | {'回撤':>8} | {'正收益fold':>12} | {'耗时':>6}")
    print("-" * 80)
    sorted_r = sorted(results, key=lambda x: float(x.get("avg_sharpe", "-999")), reverse=True)
    for r in sorted_r:
        sharpe = r.get("avg_sharpe", "N/A")
        avg_ret = r.get("avg_ret", "N/A")
        avg_dd = r.get("avg_dd", "N/A")
        print(f"{r['label']:>20} | {sharpe:>8} | {avg_ret:>7.2f}% | {avg_dd:>7.1f}% | "
              f"{r.get('pos_folds','N/A'):>12} | {r.get('time_s','N/A'):>6}")
    return sorted_r


# ── 主流程 ──────────────────────────────────────────────────────
if __name__ == "__main__":
    do_round2 = "--round2" in sys.argv

    if not do_round2:
        # ── Round 1: 单参数扫描 ──────────────────────────────────
        print("=" * 70)
        print("v27 SL/TP/持仓天数 参数扫描 Round 1（单参数）")
        print(f"  WF: train={TRAIN_DAYS}, test={TEST_DAYS}, step={STEP_DAYS}")
        print(f"  区间: {START_DATE} ~ {END_DATE}")
        print(f"  默认值: {DEFAULTS}")
        print("=" * 70)

        all_results = {}
        total_t0 = time.time()

        for param_name, values in ROUND1.items():
            print(f"\n{'─'*50}")
            print(f"扫描 {param_name}: {values}")
            print(f"{'─'*50}")
            param_results = []
            for v in values:
                label = f"{param_name}={v}"
                overrides = {param_name: v}
                r = run_single(overrides, label)
                param_results.append(r)
                sharpe = r.get("avg_sharpe", "N/A")
                print(f"  {label:>25} → 夏普={sharpe}, 正收益={r.get('pos_folds','N/A')}, 耗时={r.get('time_s','N/A')}")

            sorted_r = print_table(param_results, f"{param_name} 扫描结果（按夏普降序）")
            all_results[param_name] = sorted_r

        # ── 汇总最优 ──────────────────────────────────────────────
        print(f"\n{'='*70}")
        print("Round 1 汇总：各参数最优值")
        print(f"{'='*70}")
        best_combo = {}
        for param_name, sorted_r in all_results.items():
            if sorted_r and sorted_r[0].get("avg_sharpe") not in ("N/A", None):
                best = sorted_r[0]
                best_val = list(best["params"].values())[0]
                best_combo[param_name] = best_val
                print(f"  {param_name:>20} = {best_val:<8} (夏普={best['avg_sharpe']:.3f})")

        total_elapsed = time.time() - total_t0
        print(f"\n  Round 1 总耗时: {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")
        print(f"\n  建议 Round 2 扫描组合:")
        print(f"    {best_combo}")
        print(f"  运行: python scripts/backtest/sweep_v27_sltp_hold.py --round2")

    else:
        # ── Round 2: 最优组合精细扫描 ─────────────────────────────
        # 用 Round 1 的最优值作为中心，做 ±1 邻域扫描
        # 这里硬编码 Round 1 结果（运行时手动填入）
        BEST = {
            "STOP_LOSS": -0.02,      # ← 填入 Round 1 最优
            "TAKE_PROFIT": 0.05,     # ← 填入 Round 1 最优
            "HOLD_DAYS_MAX": 5,      # ← 填入 Round 1 最优
            "HOLD_DAYS_EXTEND": 7,   # ← 填入 Round 1 最优
        }

        # 生成邻域组合（每个参数 ±1 步长）
        NEIGHBOR = {
            "STOP_LOSS": [BEST["STOP_LOSS"] - 0.005, BEST["STOP_LOSS"], BEST["STOP_LOSS"] + 0.005],
            "TAKE_PROFIT": [BEST["TAKE_PROFIT"] - 0.02, BEST["TAKE_PROFIT"], BEST["TAKE_PROFIT"] + 0.02],
            "HOLD_DAYS_MAX": [max(2, BEST["HOLD_DAYS_MAX"] - 2), BEST["HOLD_DAYS_MAX"], BEST["HOLD_DAYS_MAX"] + 2],
            "HOLD_DAYS_EXTEND": [max(3, BEST["HOLD_DAYS_EXTEND"] - 2), BEST["HOLD_DAYS_EXTEND"], BEST["HOLD_DAYS_EXTEND"] + 3],
        }

        print("=" * 70)
        print("v27 SL/TP/持仓天数 参数扫描 Round 2（最优邻域）")
        print(f"  中心值: {BEST}")
        print(f"  邻域: {NEIGHBOR}")
        print("=" * 70)

        # 全组合（3^4 = 81 组）
        keys = list(NEIGHBOR.keys())
        combos = list(itertools.product(*[NEIGHBOR[k] for k in keys]))

        round2_results = []
        total_t0 = time.time()

        for i, combo in enumerate(combos):
            overrides = dict(zip(keys, combo))
            label = f"SL={overrides['STOP_LOSS']:.3f} TP={overrides['TAKE_PROFIT']:.2f} H={overrides['HOLD_DAYS_MAX']} E={overrides['HOLD_DAYS_EXTEND']}"
            print(f"\n[{i+1}/{len(combos)}] {label}")
            r = run_single(overrides, label)
            round2_results.append(r)
            sharpe = r.get("avg_sharpe", "N/A")
            print(f"  → 夏普={sharpe}, 正收益={r.get('pos_folds','N/A')}, 耗时={r.get('time_s','N/A')}")

        sorted_r2 = print_table(round2_results, "Round 2 全组合结果（按夏普降序）")

        if sorted_r2:
            best2 = sorted_r2[0]
            print(f"\n★ 全局最优: {best2['label']}")
            print(f"  夏普={best2.get('avg_sharpe','N/A')}, 收益={best2.get('avg_ret','N/A'):.2f}%, 回撤={best2.get('avg_dd','N/A'):.1f}%")

        total_elapsed = time.time() - total_t0
        print(f"\n  Round 2 总耗时: {total_elapsed:.0f}s ({total_elapsed/60:.1f}min)")
