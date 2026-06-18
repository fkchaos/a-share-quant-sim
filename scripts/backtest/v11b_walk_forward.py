#!/usr/bin/env python3
"""
v11b Walk-Forward 验证（完整引擎版）
=====================================

用预计算 panel 因子 + 直接向量化 ensemble 评分，但每个 fold 用完整 run_backtest 引擎
（含涨跌停检查、行业分散、分级止盈、持有期 decay），与历史 WF 配置对齐。

WF 参数：train=252, test=126, step=63
评分：ensemble_union_score（momentum/volatility/reversal 3组，每组 top_n=5）
交易：top_n=12, rebalance_freq=20, stop_loss=20%

用法：
    python scripts/backtest/v11b_walk_forward.py
    python scripts/backtest/v11b_walk_forward.py --start 2021-01-01 --end 2026-05-31
"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime

from core.db import load_panel_from_db
from core.factors import calc_factors_panel_v11b
from core.scoring import ensemble_union_score
from core.config import STRATEGY_PROFILES
from scripts.backtest.run_backtest import run_backtest

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")

# ── v11b 配置 ──────────────────────────────────────────────────────
PROFILE = STRATEGY_PROFILES["v11b_zz800_union"]
ENSEMBLE_GROUPS = PROFILE.ensemble_groups
GROUP_TOP_N = PROFILE.ensemble_group_top_n
TOP_N = PROFILE.top_n
REBAL_FREQ = PROFILE.rebalance_freq
STOP_LOSS = PROFILE.stop_loss
INITIAL_CAPITAL = 200_000

def main():
    import argparse
    parser = argparse.ArgumentParser(description="v11b Walk-Forward 验证（完整引擎）")
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2026-05-31")
    parser.add_argument("--train", type=int, default=252)
    parser.add_argument("--test", type=int, default=126)
    parser.add_argument("--step", type=int, default=63)
    args = parser.parse_args()

    print(f"{'='*60}")
    print(f"v11b Walk-Forward 验证（完整引擎版）")
    print(f"  数据区间: {args.start} ~ {args.end}")
    print(f"  WF 参数: train={args.train}, test={args.test}, step={args.step}")
    print(f"  Ensemble: {list(ENSEMBLE_GROUPS.keys())}, group_top_n={GROUP_TOP_N}")
    print(f"  交易参数: top_n={TOP_N}, rebal={REBAL_FREQ}, sl={STOP_LOSS:.0%}")
    print(f"{'='*60}")

    # ── 加载数据 ──────────────────────────────────────────────────
    print("\n📥 加载数据...")
    t0 = time.time()
    panels, codes = load_panel_from_db(args.start, args.end, need_hl=True)
    close_panel = panels[0]
    volume_panel = panels[1]
    high_panel = panels[3]
    low_panel = panels[4]
    print(f"  {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只 ({time.time()-t0:.1f}s)")

    # ── 预计算因子 ────────────────────────────────────────────────
    print("\n🔢 预计算因子 (v11b 专用 13 个)...")
    t1 = time.time()
    factors = calc_factors_panel_v11b(close_panel, volume_panel, high_panel, low_panel)
    print(f"  {len(factors)} 个因子 ({time.time()-t1:.1f}s)")

    # ── 预计算评分 ────────────────────────────────────────────────
    print("\n📊 预计算 ensemble 评分...")
    t2 = time.time()
    score_panel = ensemble_union_score(
        factors,
        ensemble_groups=ENSEMBLE_GROUPS,
        group_top_n=GROUP_TOP_N,
        min_groups=1,
    )
    print(f"  评分面板: {score_panel.shape} ({time.time()-t2:.1f}s)")

    # ── Walk-Forward ──────────────────────────────────────────────
    print(f"\n🚀 Walk-Forward 开始（完整 run_backtest 引擎）...")
    dates = close_panel.index
    n = len(dates)
    fold_results = []
    fold_navs = []
    fold = 0
    train_end = args.train

    t3 = time.time()
    while train_end + args.test <= n:
        fold += 1
        train_start = max(0, train_end - args.train)
        test_start = train_end
        test_end = min(n, train_end + args.test)

        window_dates = dates[train_start:test_end]
        test_dates = dates[test_start:test_end]

        sub_close = close_panel.loc[window_dates]
        sub_score = score_panel.loc[window_dates]
        warmup = train_end - train_start

        # 用完整 run_backtest 引擎
        m, nav, _ = run_backtest(
            sub_close, sub_score,
            top_n=TOP_N,
            rebalance_freq=REBAL_FREQ,
            stop_loss=STOP_LOSS,
            label=f'v11b_fold{fold}',
            warmup_days=warmup,
            initial_capital=INITIAL_CAPITAL,
        )

        # 只取 test 期 nav
        test_nav = nav.loc[test_dates] if nav is not None else None

        fold_results.append({
            "fold": fold,
            "train": f"{dates[train_start].date()}~{dates[test_start-1].date()}",
            "test": f"{dates[test_start].date()}~{dates[test_end-1].date()}",
            "ann_return": m["annual_return"],
            "sharpe": m["sharpe_ratio"],
            "max_dd": m["max_drawdown"],
            "sortino": m["sortino_ratio"],
            "trades": m["total_trades"],
        })
        if test_nav is not None:
            fold_navs.append(test_nav)

        print(f"  Fold {fold:2d} | {fold_results[-1]['test']} | "
              f"Ret={m['annual_return']:7.1%} Sharpe={m['sharpe_ratio']:5.2f} "
              f"DD={m['max_drawdown']:6.1%} Trades={m['total_trades']}")

        train_end += args.step

    wf_time = time.time() - t3

    # ── 汇总 ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"WF 完成 ({wf_time:.1f}s) — {len(fold_results)} folds")
    print(f"{'='*60}")

    rets = [r["ann_return"] for r in fold_results]
    sharpes = [r["sharpe"] for r in fold_results]
    dds = [r["max_dd"] for r in fold_results]
    pos = sum(1 for r in rets if r > 0)

    print(f"\n📊 汇总:")
    print(f"  平均年化:   {np.mean(rets):.1%}")
    print(f"  年化中位数: {np.median(rets):.1%}")
    print(f"  平均夏普:   {np.mean(sharpes):.2f}")
    print(f"  平均回撤:   {np.mean(dds):.1%}")
    print(f"  正收益fold: {pos}/{len(fold_results)} ({pos/len(fold_results):.0%})")

    print(f"\n📋 各 fold 详情:")
    for r in fold_results:
        print(f"  Fold {r['fold']:2d} | {r['test']} | "
              f"Ret={r['ann_return']:7.1%} Sharpe={r['sharpe']:5.2f} "
              f"DD={r['max_dd']:6.1%} Trades={r['trades']}")

    # ── 保存结果 ──────────────────────────────────────────────────
    os.makedirs(REPORT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(REPORT_DIR, f"v11b_wf_{ts}")
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump({
            "strategy": "v11b_zz800_union",
            "engine": "full_run_backtest",
            "data_source": "db",
            "wf_params": {"train": args.train, "test": args.test, "step": args.step},
            "summary": {
                "avg_return": float(np.mean(rets)),
                "median_return": float(np.median(rets)),
                "avg_sharpe": float(np.mean(sharpes)),
                "avg_max_dd": float(np.mean(dds)),
                "pos_folds": pos,
                "total_folds": len(fold_results),
            },
            "folds": fold_results,
        }, f, indent=2, ensure_ascii=False)

    if fold_navs:
        combined_nav = None
        for tnav in fold_navs:
            if combined_nav is None:
                combined_nav = tnav / tnav.iloc[0]
            else:
                combined_nav = pd.concat([combined_nav, tnav * (combined_nav.iloc[-1] / tnav.iloc[0])])
        combined_nav.to_csv(os.path.join(out_dir, "nav.csv"))

    print(f"\n✅ 结果已保存 → {out_dir}")

    if pos / len(fold_results) >= 0.6 and np.mean(sharpes) > 0.5:
        print(f"✅ 通过: 正收益fold {pos/len(fold_results):.0%} >= 60%, 夏普 {np.mean(sharpes):.2f} > 0.5")
    else:
        print(f"❌ 未通过: 正收益fold {pos/len(fold_results):.0%}, 夏普 {np.mean(sharpes):.2f}")

if __name__ == "__main__":
    main()
