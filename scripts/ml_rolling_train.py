#!/usr/bin/env python3
"""
ML Rolling Training v2 — Walk-Forward 回测
============================================

增强版功能：
  1. 多周期标签融合 (5d/20d/60d)
  2. 因子分组 stacking
  3. Regime switching (牛/熊/震荡)
  4. 特征增强 (行业one-hot/市值分位数/交互项)

用法：
    python ml_rolling_train.py                                    # v2 全部增强
    python ml_rolling_train.py --no-group --no-regime            # 关掉一些增强
    python ml_rolling_train.py --forward-periods 5 20            # 只用5d+20d
    python ml_rolling_train.py --ablation baseline               # 消融实验基线
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd

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
    parser = argparse.ArgumentParser(description="ML Rolling Training v2 — Walk-Forward 回测")
    # ML 核心参数
    parser.add_argument("--forward-periods", nargs="+", type=int, default=[5, 20],
                        help="多周期标签 (default: 5 20)")
    parser.add_argument("--train-days", type=int, default=252)
    parser.add_argument("--test-days", type=int, default=63)
    parser.add_argument("--step-days", type=int, default=63)

    # 增强开关
    parser.add_argument("--no-multi-period", action="store_true", help="关闭多周期融合")
    parser.add_argument("--no-group", action="store_true", help="关闭因子分组 stacking")
    parser.add_argument("--no-regime", action="store_true", help="关闭 regime switching")
    parser.add_argument("--no-enhanced", action="store_true", help="关闭特征增强")

    # LGB 参数
    parser.add_argument("--lgb-lr", type=float, default=0.05)
    parser.add_argument("--lgb-leaves", type=int, default=63)
    parser.add_argument("--lgb-min-data", type=int, default=20)

    # 风控参数
    parser.add_argument("--no-vol-scaling", action="store_true", help="关闭波动率缩放")
    parser.add_argument("--vol-target", type=float, default=0.20, help="波动率目标 (default: 0.20)")
    parser.add_argument("--use-take-profit", action="store_true", help="启用分级止盈")
    parser.add_argument("--use-holding-decay", action="store_true", help="启用持有期decay")
    parser.add_argument("--use-atr-stop", action="store_true", help="启用ATR自适应止损")
    parser.add_argument("--atr-k", type=float, default=2.0, help="ATR止损倍数 (default: 2.0)")

    # 策略对比
    parser.add_argument("--strategy", nargs="+", default=["v6b_8f_pos_ic"])
    parser.add_argument("--no-v6b", action="store_true")
    parser.add_argument("--ablation", choices=["baseline", "multi_period", "group", "regime", "enhanced", "full"],
                        help="消融实验模式：只开启特定模块")

    # 回测参数
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--exec-timing", choices=["close", "open"], default="close")
    parser.add_argument("--top-n", type=int, default=12)
    parser.add_argument("--rebalance-freq", type=int, default=20)
    parser.add_argument("--stop-loss", type=float, default=0.20)
    parser.add_argument("--max-position", type=float, default=0.10)
    parser.add_argument("--max-industry-weight", type=float, default=0.25)
    parser.add_argument("--no-industry", action="store_true")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--report-markdown", action="store_true")
    args = parser.parse_args()

    stock_names = _load_stock_names()

    # 消融实验：根据 --ablation 设置开关
    use_multi = not args.no_multi_period
    use_group = not args.no_group
    use_regime = not args.no_regime
    use_enhanced = not args.no_enhanced

    if args.ablation:
        use_multi = args.ablation in ("multi_period", "full")
        use_group = args.ablation in ("group", "full")
        use_regime = args.ablation in ("regime", "full")
        use_enhanced = args.ablation in ("enhanced", "full")
        label_suffix = f"_{args.ablation}"
    else:
        label_suffix = "_v2"

    print("=" * 60)
    print(f"ML Rolling Training v2{label_suffix}")
    print("=" * 60)
    t0 = time.time()

    lgb_params = {
        'objective': 'regression_l1',
        'metric': 'mae',
        'learning_rate': args.lgb_lr,
        'num_leaves': args.lgb_leaves,
        'min_data_in_leaf': args.lgb_min_data,
        'feature_fraction': 0.8,
        'bagging_fraction': 0.8,
        'bagging_freq': 5,
        'lambda_l1': 0.1,
        'lambda_l2': 1.0,
        'verbose': -1,
    }

    print(f"\nMulti-period: {use_multi} | Group stacking: {use_group} | "
          f"Regime: {use_regime} | Enhanced: {use_enhanced}")
    print(f"Forward periods: {args.forward_periods}")

    # ── 1. 加载数据 ──
    print(f"\n[1/4] 加载数据...")
    need_open = (args.exec_timing == "open")
    loaded, codes = load_and_build_panel(
        args.start, args.end, need_open=need_open, need_hl=True,
        market_filter=core_config.market,
    )
    close_panel = loaded[0]
    volume_panel = loaded[1]
    amount_panel = loaded[2]
    open_panel = loaded[3] if len(loaded) > 3 and need_open else None
    high_panel = loaded[4] if len(loaded) > 4 else None
    low_panel = loaded[5] if len(loaded) > 5 else None
    print(f"  {close_panel.shape[0]}d × {close_panel.shape[1]}s | "
          f"{close_panel.index[0].date()} ~ {close_panel.index[-1].date()}")

    # ── 2. 因子面板 ──
    factors = calc_factors_panel(
        close_panel, volume_panel, amount_panel,
        open_panel=open_panel, high_panel=high_panel, low_panel=low_panel,
    )
    print(f"  {len(factors)} factors")

    # ── 3. ML Pipeline ──
    label = f"ml{label_suffix}"
    score_ml, fold_info = run_ml_pipeline(
        factors=factors,
        close_panel=close_panel,
        train_days=args.train_days,
        test_days=args.test_days,
        step_days=args.step_days,
        forward_periods=args.forward_periods,
        use_multi_period=use_multi,
        use_group_stacking=use_group,
        use_regime=use_regime,
        use_enhanced_features=use_enhanced,
        lgb_params=lgb_params,
        stock_names=stock_names,
    )

    if score_ml.abs().sum().sum() == 0:
        print("\n⚠️  ML score panel 全零，退出")
        sys.exit(1)

    # ── 4. 回测 ──
    print(f"\n[4/4] 回测...")
    from scripts.run_backtest import run_backtest
    max_ind = 0 if args.no_industry else args.max_industry_weight
    sn = None if args.no_industry else stock_names
    bt_kwargs = dict(
        top_n=args.top_n, rebalance_freq=args.rebalance_freq,
        stop_loss=args.stop_loss, max_position=args.max_position,
        max_industry_weight=max_ind, max_daily_turnover=0,
        weight_method='equal', stock_names=sn, exec_timing=args.exec_timing,
        use_vol_scaling=not args.no_vol_scaling,
        vol_target=args.vol_target,
        use_take_profit=args.use_take_profit,
        tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)] if args.use_take_profit else None,
        use_holding_decay=args.use_holding_decay,
        use_atr_stop=args.use_atr_stop,
        atr_k=args.atr_k,
    )
    if args.exec_timing == 'open':
        bt_kwargs['open_panel'] = open_panel

    ml_metrics, ml_nav, ml_trades = run_backtest(
        close_panel, score_ml, label=label, **bt_kwargs
    )
    print(f"  {label}: {ml_metrics['annual_return']:.2%} / "
          f"{ml_metrics['sharpe_ratio']:.2f} / {ml_metrics['max_drawdown']:.2%}")

    metrics_list = [ml_metrics]
    nav_dict = {label: ml_nav}
    trades_dict = {label: ml_trades}
    all_labels = [label]

    # 基准对比
    if not args.no_v6b:
        for strat_name in args.strategy:
            if strat_name not in STRATEGY_PROFILES:
                continue
            profile = STRATEGY_PROFILES[strat_name]
            if profile.factor_weights:
                score_base = composite_score(
                    {k: v for k, v in factors.items() if k in profile.factor_weights},
                    profile.factor_weights,
                )
            else:
                score_base = composite_score(factors)
            m, nav, tr = run_backtest(close_panel, score_base, label=strat_name, **bt_kwargs)
            metrics_list.append(m)
            nav_dict[strat_name] = nav
            trades_dict[strat_name] = tr
            all_labels.append(strat_name)

    # ── 5. 输出 ──
    total_time = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"完成 ({total_time:.1f}s)")
    print(f"{'─' * 60}")
    print(f"{'策略':<25} {'年化':>9} {'夏普':>7} {'Sortino':>8} {'最大回撤':>10}")
    print(f"{'─' * 60}")
    for m in metrics_list:
        print(f"{m['label']:<25} {m['annual_return']:>8.2%} "
              f"{m['sharpe_ratio']:>7.2f} {m['sortino_ratio']:>7.2f} "
              f"{m['max_drawdown']:>9.2%}")

    # ML folds 汇总
    if fold_info:
        avg_train = np.mean([f['train_ic'] for f in fold_info])
        avg_test = np.mean([f['test_ic'] for f in fold_info])
        pos_folds = sum(1 for f in fold_info if f['test_ic'] > 0)
        print(f"\n  ML Folds: avg IC train={avg_train:.4f} test={avg_test:.4f} "
              f"| positive: {pos_folds}/{len(fold_info)}")

    # 保存
    if args.output_dir:
        out_dir = args.output_dir
    else:
        out_dir = os.path.join(REPORT_DIR, datetime.now().strftime("%Y%m%d_%H%M%S_ml2"))
    os.makedirs(out_dir, exist_ok=True)

    with open(os.path.join(out_dir, "summary.json"), "w") as f:
        json.dump({m['label']: {k: v for k, v in m.items()} for m in metrics_list},
                  f, indent=2, ensure_ascii=False, default=str)
    pd.DataFrame([{
        'strategy': m['label'],
        'annual_return': m['annual_return'],
        'sharpe': m['sharpe_ratio'],
        'max_dd': m['max_drawdown'],
        'sortino': m['sortino_ratio'],
        'total_trades': m['total_trades'],
        'final_value': m['final_value'],
    } for m in metrics_list]).to_csv(os.path.join(out_dir, "comparison.csv"), index=False)
    for l, nav in nav_dict.items():
        nav.to_csv(os.path.join(out_dir, f"nav_{l}.csv"))
    if fold_info:
        pd.DataFrame(fold_info).to_csv(os.path.join(out_dir, "ml_folds.csv"), index=False)

    print(f"\n结果已保存: {out_dir}/")

    if args.report_markdown:
        lines = [f"# ML v2 回测报告\n",
                 f"> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n",
                 f"## 参数\n- Multi-period: {use_multi} {args.forward_periods}\n"
                 f"- Group stacking: {use_group}\n- Regime: {use_regime}\n"
                 f"- Enhanced: {use_enhanced}\n",
                 "## 对比\n",
                 "| 策略 | 年化 | 夏普 | Sortino | 最大回撤 |",
                 "|------|------|------|---------|---------|"]
        for m in metrics_list:
            lines.append(f"| {m['label']} | {m['annual_return']:.2%} | "
                         f"{m['sharpe_ratio']:.2f} | {m['sortino_ratio']:.2f} | "
                         f"{m['max_drawdown']:.2%} |")
        print("\n\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
