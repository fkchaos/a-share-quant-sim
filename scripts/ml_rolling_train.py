#!/usr/bin/env python3
"""
ML Rolling Training — Walk-Forward 回测
=========================================

使用 LightGBM 滚动训练替代人工因子权重，进行 Walk-Forward 回测。

用法：
    python ml_rolling_train.py                          # 默认：ML vs v6b_8f_pos_ic 对比
    python ml_rolling_train.py --strategy all           # ML vs 所有策略
    python ml_rolling_train.py --forward-period 5       # 预测未来5日收益（默认）
    python ml_rolling_train.py --forward-period 20      # 预测未来20日收益
    python ml_rolling_train.py --train-days 504         # 2年训练窗口
    python ml_rolling_train.py --exec-timing open       # 开盘执行（模拟盘中模式）
    python ml_rolling_train.py --no-v6b                # 只跑 ML，不跑基准

输出：
    data/backtest_results/YYYYMMDD_HHMMSS_ml/
        ├── summary.json
        ├── comparison.csv
        ├── nav_ml.csv / nav_v6b_8f_pos_ic.csv ...
        ├── ml_folds.csv         ← ML 每轮训练详细信息
        ├── ml_predictions.csv   ← ML 预测值 × 日期 × 股票
        └── report.md
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import config as core_config, STRATEGY_PROFILES
from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.data import load_and_build_panel
from core.ml import run_ml_pipeline

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(_BASE_DIR, "data"))
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")


def _load_stock_names() -> dict:
    """Load stock name mapping."""
    hs300_path = os.path.join(_BASE_DIR, "hs300_constituents.csv")
    if not os.path.exists(hs300_path):
        hs300_path = "/root/hs300_constituents.csv"
    try:
        hs300 = pd.read_csv(hs300_path)
        return dict(zip(
            hs300['品种代码'].astype(str).str.zfill(6),
            hs300['品种名称']
        ))
    except Exception:
        return {}


def main():
    parser = argparse.ArgumentParser(
        description="ML Rolling Training Walk-Forward 回测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    # ML 参数
    parser.add_argument("--forward-period", type=int, default=5,
                        help="预测未来 N 日收益 (default: 5)")
    parser.add_argument("--train-days", type=int, default=252,
                        help="训练窗口长度 (default: 252 ≈ 1年)")
    parser.add_argument("--test-days", type=int, default=63,
                        help="测试窗口长度 (default: 63 ≈ 1季度)")
    parser.add_argument("--step-days", type=int, default=63,
                        help="滚动步长 (default: 63 = test_days)")
    parser.add_argument("--lgb-learning-rate", type=float, default=0.05)
    parser.add_argument("--lgb-num-leaves", type=int, default=63)
    parser.add_argument("--lgb-min-data", type=int, default=20)

    # 策略对比
    parser.add_argument("--strategy", nargs="+", default=["v6b_8f_pos_ic"],
                        help="对比的策略 (default: v6b_8f_pos_ic)")
    parser.add_argument("--no-v6b", action="store_true",
                        help="只跑 ML 策略，不跑基准")

    # 回测参数
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--exec-timing", choices=["close", "open"], default="close")
    parser.add_argument("--top-n", type=int, default=12)
    parser.add_argument("--rebalance-freq", type=int, default=20)
    parser.add_argument("--stop-loss", type=float, default=0.20)
    parser.add_argument("--max-position", type=float, default=0.10)
    parser.add_argument("--max-industry-weight", type=float, default=0.25)
    parser.add_argument("--no-industry", action="store_true",
                        help="不限制行业仓位")

    # 输出
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--report-markdown", action="store_true")
    args = parser.parse_args()

    stock_names = _load_stock_names()

    print("=" * 60)
    print("ML Rolling Training — Walk-Forward 回测")
    print("=" * 60)
    t0 = time.time()

    lgb_params = {
        'objective': 'regression_l1',
        'metric': 'mae',
        'learning_rate': args.lgb_learning_rate,
        'num_leaves': args.lgb_num_leaves,
        'min_data_in_leaf': args.lgb_min_data,
        'feature_fraction': 0.8,
        'bagging_fraction': 0.8,
        'bagging_freq': 5,
        'lambda_l1': 0.1,
        'lambda_l2': 1.0,
        'verbose': -1,
    }

    print(f"\nML 参数: forward={args.forward_period}d | "
          f"train={args.train_days}d | test={args.test_days}d | step={args.step_days}d")
    print(f"LGB: lr={args.lgb_learning_rate} | "
          f"leaves={args.lgb_num_leaves} | min_data={args.lgb_min_data}")

    # ── 1. 加载数据 ──
    print(f"\n[1/4] 加载数据... (exec_timing={args.exec_timing})")
    need_open = (args.exec_timing == "open")

    loaded, codes = load_and_build_panel(
        args.start, args.end,
        need_open=need_open, need_hl=True,
        market_filter=core_config.market,
    )
    close_panel = loaded[0]
    volume_panel = loaded[1]
    amount_panel = loaded[2]
    open_panel = loaded[3] if len(loaded) > 3 and need_open else None
    high_panel = loaded[4] if len(loaded) > 4 else None
    low_panel = loaded[5] if len(loaded) > 5 else None

    print(f"  Panel: {close_panel.shape[0]} days × {close_panel.shape[1]} stocks")
    print(f"  Range: {close_panel.index[0].date()} ~ {close_panel.index[-1].date()}")

    # ── 2. 计算因子面板 ──
    print(f"\n[2/4] 计算因子面板...")
    factors = calc_factors_panel(
        close_panel, volume_panel, amount_panel,
        open_panel=open_panel, high_panel=high_panel, low_panel=low_panel,
    )
    print(f"  共 {len(factors)} 个因子")

    # ── 3. ML Pipeline ──
    print(f"\n[3/4] ML 滚动训练...")
    score_ml, fold_info = run_ml_pipeline(
        factors=factors,
        close_panel=close_panel,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
        forward_period=args.forward_period,
        lgb_params=lgb_params,
        stock_names=stock_names,
    )

    if score_ml.abs().sum().sum() == 0:
        print("\n⚠️  ML score panel 为全零，无法回测。检查日期对齐和标签计算。")
        sys.exit(1)

    # ── 4. 回测 ──
    print(f"\n[4/4] 回测执行 (exec_timing={args.exec_timing})...")

    from scripts.run_backtest import run_backtest

    max_ind = 0 if args.no_industry else args.max_industry_weight
    stock_names_for_bt = None if args.no_industry else stock_names

    # ML 策略回测
    print(f"\n  ▶ ml_top{args.top_n}: top_n={args.top_n}, "
          f"freq={args.rebalance_freq}, sl={args.stop_loss}")
    ml_kwargs = dict(
        top_n=args.top_n,
        rebalance_freq=args.rebalance_freq,
        stop_loss=args.stop_loss,
        max_position=args.max_position,
        max_industry_weight=max_ind,
        max_daily_turnover=0,
        weight_method='equal',
        stock_names=stock_names_for_bt,
        exec_timing=args.exec_timing,
    )
    if args.exec_timing == 'open':
        ml_kwargs['open_panel'] = open_panel

    ml_metrics, ml_nav, ml_trades = run_backtest(
        close_panel, score_ml, label='ml_rolling', **ml_kwargs,
    )
    ml_time = time.time() - t0
    print(f"    ML 完成 ({ml_time:.1f}s): "
          f"Return={ml_metrics['annual_return']:.2%}, "
          f"Sharpe={ml_metrics['sharpe_ratio']:.2f}, "
          f"MaxDD={ml_metrics['max_drawdown']:.2%}")

    # ── 5. 基准对比 ──
    metrics_list = [ml_metrics]
    nav_dict = {'ml_rolling': ml_nav}
    trades_dict = {'ml_rolling': ml_trades}

    base_strategies = [] if args.no_v6b else args.strategy

    for strat_name in base_strategies:
        if strat_name not in STRATEGY_PROFILES:
            print(f"  ⚠️  Unknown strategy '{strat_name}', skip.")
            continue

        profile = STRATEGY_PROFILES[strat_name]
        if profile.factor_weights:
            score_base = composite_score(
                {k: v for k, v in factors.items() if k in profile.factor_weights},
                profile.factor_weights,
            )
        else:
            score_base = composite_score(factors)

        base_kwargs = dict(
            top_n=args.top_n,
            rebalance_freq=args.rebalance_freq,
            stop_loss=args.stop_loss,
            max_position=args.max_position,
            max_industry_weight=max_ind,
            max_daily_turnover=0,
            weight_method='equal',
            stock_names=stock_names_for_bt,
            exec_timing=args.exec_timing,
        )
        if args.exec_timing == 'open':
            base_kwargs['open_panel'] = open_panel

        m, nav, trades = run_backtest(
            close_panel, score_base, label=strat_name, **base_kwargs,
        )
        metrics_list.append(m)
        nav_dict[strat_name] = nav
        trades_dict[strat_name] = trades
        print(f"  ▶ {strat_name}: "
              f"Return={m['annual_return']:.2%}, "
              f"Sharpe={m['sharpe_ratio']:.2f}, "
              f"MaxDD={m['max_drawdown']:.2%}")

    # ── 6. 输出 ──
    total_time = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"完成 ({total_time:.1f}s)")
    print(f"{'=' * 60}")

    # 打印对比表
    print(f"\n{'策略对比汇总':^60}")
    print(f"{'─' * 60}")
    print(f"{'策略':<25} {'年化收益':>10} {'夏普':>7} {'Sortino':>8} {'最大回撤':>10}")
    print(f"{'─' * 60}")
    for m in metrics_list:
        print(f"{m['label']:<25} {m['annual_return']:>9.2%} "
              f"{m['sharpe_ratio']:>7.2f} {m['sortino_ratio']:>7.2f} "
              f"{m['max_drawdown']:>9.2%}")

    # 保存结果
    if args.output_dir:
        out_dir = args.output_dir
    else:
        out_dir = os.path.join(
            REPORT_DIR,
            datetime.now().strftime("%Y%m%d_%H%M%S_ml")
        )

    os.makedirs(out_dir, exist_ok=True)

    # summary.json
    summary = {m['label']: {k: v for k, v in m.items()} for m in metrics_list}
    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False, default=str)

    # comparison.csv
    rows = []
    for m in metrics_list:
        rows.append({
            'strategy': m['label'],
            'annual_return': m['annual_return'],
            'sharpe': m['sharpe_ratio'],
            'max_dd': m['max_drawdown'],
            'sortino': m['sortino_ratio'],
            'calmar': m['calmar_ratio'],
            'total_trades': m['total_trades'],
            'final_value': m['final_value'],
        })
    pd.DataFrame(rows).to_csv(os.path.join(out_dir, "comparison.csv"), index=False)

    # nav curves
    for label, nav in nav_dict.items():
        nav.to_csv(os.path.join(out_dir, f"nav_{label}.csv"))

    # trades
    for label, trades in trades_dict.items():
        if len(trades) > 0:
            trades.to_csv(os.path.join(out_dir, f"trades_{label}.csv"), index=False)

    # ML folds
    if fold_info:
        fold_df = pd.DataFrame(fold_info)
        fold_df.to_csv(os.path.join(out_dir, "ml_folds.csv"), index=False)
        with open(os.path.join(out_dir, "ml_folds.json"), "w") as f:
            json.dump(fold_info, f, indent=2, default=str)

        print(f"\n  ML Folds 汇总:")
        avg_train_ic = np.mean([f['train_ic'] for f in fold_info])
        avg_test_ic = np.mean([f['test_ic'] for f in fold_info])
        print(f"    平均 train IC: {avg_train_ic:.4f}")
        print(f"    平均 test IC:  {avg_test_ic:.4f}")
        print(f"    IC 衰减:       {avg_train_ic - avg_test_ic:.4f}")

    print(f"\n结果已保存: {out_dir}/")

    if args.report_markdown:
        # 简单 Markdown 输出
        lines = ["# ML Rolling Training 回测报告\n"]
        lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        lines.append(f"## ML 参数\n")
        lines.append(f"- forward_period: {args.forward_period}天")
        lines.append(f"- train: {args.train_days}天 | test: {args.test_days}天 | step: {args.step_days}天")
        lines.append(f"- LGB: lr={args.lgb_learning_rate}, leaves={args.lgb_num_leaves}\n")
        lines.append("## 策略对比\n")
        lines.append("| 策略 | 年化收益 | 夏普 | Sortino | 最大回撤 |")
        lines.append("|------|---------|------|---------|---------|")
        for m in metrics_list:
            lines.append(f"| {m['label']} | {m['annual_return']:.2%} | "
                         f"{m['sharpe_ratio']:.2f} | {m['sortino_ratio']:.2f} | "
                         f"{m['max_drawdown']:.2%} |")
        print("\n\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
