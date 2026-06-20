#!/usr/bin/env python3
"""
scripts/backtest/v33_walk_forward.py — v33 残差动量 Walk-Forward 回测
"""
import sys
import os
import time
import argparse

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from scripts.backtest.wf_runner import run_wf


def main():
    parser = argparse.ArgumentParser(description="v33 残差动量 Walk-Forward 回测")
    parser.add_argument("--train", type=int, default=252, help="训练期天数")
    parser.add_argument("--test", type=int, default=63, help="测试期天数")
    parser.add_argument("--step", type=int, default=63, help="步进天数")
    parser.add_argument("--start", type=str, default="2022-01-01", help="起始日期")
    parser.add_argument("--end", type=str, default="2026-06-18", help="结束日期")
    args = parser.parse_args()

    t0 = time.time()

    print("=" * 60)
    print("v33 残差动量 Walk-Forward 回测")
    print(f"  WF: train={args.train}, test={args.test}, step={args.step}")
    print(f"  区间: {args.start} ~ {args.end}")
    print("=" * 60)

    results = run_wf(
        strategy_name="v33",
        train_days=args.train,
        test_days=args.test,
        step_days=args.step,
        start_date=args.start,
        end_date=args.end,
    )

    if results is None:
        print("\n❌ WF 运行失败")
        return

    print("\n" + "=" * 60)
    print("v33 Walk-Forward 结果")
    print("=" * 60)

    folds = results.get("folds", [])
    if not folds:
        print("无 fold 结果")
        return

    positive_folds = sum(1 for f in folds if f.get("test_return", 0) > 0)
    total_folds = len(folds)
    avg_return = sum(f.get("test_return", 0) for f in folds) / total_folds
    avg_sharpe = sum(f.get("test_sharpe", 0) for f in folds) / total_folds
    avg_maxdd = sum(f.get("test_max_drawdown", 0) for f in folds) / total_folds

    print(f"\nFold 统计:")
    print(f"  总数: {total_folds}")
    print(f"  正收益: {positive_folds}/{total_folds} ({positive_folds/total_folds*100:.0f}%)")
    print(f"  平均收益: {avg_return*100:.2f}%")
    print(f"  平均夏普: {avg_sharpe:.3f}")
    print(f"  平均最大回撤: {avg_maxdd*100:.2f}%")

    print("\n各 Fold 详情:")
    for i, f in enumerate(folds):
        ret = f.get("test_return", 0) * 100
        sharpe = f.get("test_sharpe", 0)
        maxdd = f.get("test_max_drawdown", 0) * 100
        n_trades = f.get("n_trades", 0)
        status = "✅" if ret > 0 else "❌"
        print(f"  Fold {i+1}: {status} 收益={ret:+.2f}% 夏普={sharpe:.3f} 回撤={maxdd:.2f}% 交易={n_trades}次")

    elapsed = time.time() - t0
    print(f"\n耗时: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
