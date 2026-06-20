#!/usr/bin/env python3
"""
scripts/backtest/v35_walk_forward.py — v35 行业轮动 Walk-Forward 回测（支持参数扫描）

用法:
    # 默认参数
    python scripts/backtest/v35_walk_forward.py
    
    # 指定权重扫描
    SECTOR_MOM_WEIGHT=0.4 python scripts/backtest/v35_walk_forward.py
"""
import sys
import os
import time
import argparse

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 在导入 wf_runner 之前，通过环境变量覆盖 strategy_map 中的参数
from core.strategy_map import STRATEGY_MAP

# 读取环境变量覆盖
if 'SECTOR_MOM_WEIGHT' in os.environ:
    STRATEGY_MAP['v35']['params']['SECTOR_MOM_WEIGHT'] = float(os.environ['SECTOR_MOM_WEIGHT'])
if 'SECTOR_W_SHORT' in os.environ:
    STRATEGY_MAP['v35']['params']['SECTOR_W_SHORT'] = float(os.environ['SECTOR_W_SHORT'])
if 'SECTOR_W_MID' in os.environ:
    STRATEGY_MAP['v35']['params']['SECTOR_W_MID'] = float(os.environ['SECTOR_W_MID'])
if 'SECTOR_W_LONG' in os.environ:
    STRATEGY_MAP['v35']['params']['SECTOR_W_LONG'] = float(os.environ['SECTOR_W_LONG'])

from scripts.backtest.wf_runner import run_wf


def main():
    parser = argparse.ArgumentParser(description="v35 行业轮动 Walk-Forward 回测")
    parser.add_argument("--train", type=int, default=252, help="训练期天数")
    parser.add_argument("--test", type=int, default=63, help="测试期天数")
    parser.add_argument("--step", type=int, default=63, help="步进天数")
    parser.add_argument("--start", type=str, default="2022-01-01", help="起始日期")
    parser.add_argument("--end", type=str, default="2026-06-18", help="结束日期")
    args = parser.parse_args()

    t0 = time.time()

    # 打印当前使用的参数
    p = STRATEGY_MAP['v35']['params']
    print("=" * 60)
    print("v35 行业轮动 Walk-Forward 回测")
    print(f"  SECTOR_MOM_WEIGHT = {p['SECTOR_MOM_WEIGHT']}")
    print(f"  SECTOR_W_SHORT/MID/LONG = {p['SECTOR_W_SHORT']}/{p['SECTOR_W_MID']}/{p['SECTOR_W_LONG']}")
    print(f"  WF: train={args.train}, test={args.test}, step={args.step}")
    print(f"  区间: {args.start} ~ {args.end}")
    print("=" * 60)

    results = run_wf(
        strategy_name="v35",
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
    print("v35 Walk-Forward 结果")
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

    elapsed = time.time() - t0
    print(f"\n耗时: {elapsed:.1f}s")


if __name__ == "__main__":
    main()
