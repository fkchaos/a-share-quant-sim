#!/usr/bin/env python3
"""
ML Rolling Training v2 — Walk-Forward 回测
============================================

增强版功能：
  1. 多周期标签融合 (5d/20d)
  2. 因子分组 stacking（默认关闭）
  3. Regime switching（默认关闭）
  4. 特征增强（精简：cap_quantile + 2交互 + rsi背离）
  5. Hybrid: ML + v6b 混合 score

用法：
    python ml_rolling_train.py                                    # v2
    python ml_rolling_train.py --hybrid-alpha 0.7                # 70% ML + 30% v6b
    python ml_rolling_train.py --ablation baseline               # 消融基线
"""

import argparse, json, os, sys, time
from datetime import datetime
import numpy as np, pandas as pd

from core.config import STRATEGY_PROFILES, MarketFilter
from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.data import load_and_build_panel
from core.ml import run_ml_pipeline

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(_BASE_DIR, "data"))
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")

def _load_stock_names():
    p = os.path.join(_BASE_DIR, "hs300_constituents.csv")
    if not os.path.exists(p): p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data" + "/hs300_constituents.csv")
    try:
        df = pd.read_csv(p)
        return dict(zip(df['品种代码'].astype(str).str.zfill(6), df['品种名称']))
    except Exception: return {}

def main():
    parser = argparse.ArgumentParser()
    # ML 核心
    parser.add_argument("--forward-periods", nargs="+", type=int, default=[5, 20])
    parser.add_argument("--train-days", type=int, default=252)
    parser.add_argument("--test-days", type=int, default=63)
    parser.add_argument("--step-days", type=int, default=63)
    # 增强开关
    parser.add_argument("--no-multi-period", action="store_true")
    parser.add_argument("--no-group", action="store_true")
    parser.add_argument("--no-regime", action="store_true")
    parser.add_argument("--no-enhanced", action="store_true")
    # LGB
    parser.add_argument("--lgb-lr", type=float, default=0.05)
    parser.add_argument("--lgb-leaves", type=int, default=63)
    parser.add_argument("--lgb-min-data", type=int, default=20)
    # 风控
    parser.add_argument("--no-vol-scaling", action="store_true")
    parser.add_argument("--vol-target", type=float, default=0.20)
    parser.add_argument("--use-take-profit", action="store_true")
    parser.add_argument("--use-holding-decay", action="store_true")
    parser.add_argument("--use-atr-stop", action="store_true")
    parser.add_argument("--atr-k", type=float, default=2.0)
    # 策略
    parser.add_argument("--strategy", nargs="+", default=["v6b_8f_pos_ic"])
    parser.add_argument("--no-v6b", action="store_true")
    parser.add_argument("--ablation", choices=["baseline","multi_period","group","regime","enhanced","full"])
    parser.add_argument("--hybrid-alpha", type=float, default=None,
                        help="α×ML + (1-α)×v6b, e.g. 0.7")
    parser.add_argument("--ensemble", action="store_true",
                        help="启用 LGB+XGB+Ridge ensemble")
    parser.add_argument("--ensemble-stacking", choices=["ols","equal","ic_weighted"],
                        default="ols", help="ensemble stacking 方法")
    # 回测
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--exec-timing", choices=["close","open"], default="close")
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
    use_multi = not args.no_multi_period
    use_group = not args.no_group
    use_regime = not args.no_regime
    use_enhanced = not args.no_enhanced
    label_suffix = "_v2"
    if args.ablation:
        use_multi = args.ablation in ("multi_period","full")
        use_group = args.ablation in ("group","full")
        use_regime = args.ablation in ("regime","full")
        use_enhanced = args.ablation in ("enhanced","full")
        label_suffix = f"_{args.ablation}"

    print("=" * 60)
    print(f"ML Rolling Training v2{label_suffix}")
    print("=" * 60)
    t0 = time.time()

    lgb_params = {
        'objective': 'regression_l1', 'metric': 'mae',
        'learning_rate': args.lgb_lr, 'num_leaves': args.lgb_leaves,
        'min_data_in_leaf': args.lgb_min_data, 'feature_fraction': 0.8,
        'bagging_fraction': 0.8, 'bagging_freq': 5,
        'lambda_l1': 0.1, 'lambda_l2': 1.0, 'verbose': -1,
    }

    # 1. 数据
    need_open = (args.exec_timing == "open")
    loaded, codes = load_and_build_panel(args.start, args.end, need_open=need_open,
                                         need_hl=True, market_filter=MarketFilter())
    close_panel = loaded[0]
    open_panel = loaded[3] if len(loaded) > 3 and need_open else None
    high_panel = loaded[4] if len(loaded) > 4 else None
    low_panel = loaded[5] if len(loaded) > 5 else None
    factors = calc_factors_panel(close_panel, loaded[1], loaded[2],
                                  open_panel, high_panel, low_panel)
    print(f"  {close_panel.shape[0]}d × {close_panel.shape[1]}s | {len(factors)} factors")

    # 2. ML Pipeline
    label = f"ml{label_suffix}"
    if args.ensemble:
        label = f"ml_ens_{args.ensemble_stacking}"
        use_multi = True  # ensemble 默认开多周期
    score_ml, fold_info = run_ml_pipeline(
        factors=factors, close_panel=close_panel,
        train_days=args.train_days, test_days=args.test_days, step_days=args.step_days,
        forward_periods=args.forward_periods,
        use_multi_period=use_multi, use_group_stacking=use_group,
        use_regime=use_regime, use_enhanced_features=use_enhanced,
        lgb_params=lgb_params, stock_names=stock_names,
        use_ensemble=args.ensemble,
        ensemble_stacking=args.ensemble_stacking,
    )
    if score_ml.abs().sum().sum() == 0:
        print("⚠️ ML score 全零"); sys.exit(1)

    # 3. Hybrid: ML + v6b
    if args.hybrid_alpha is not None and 0 < args.hybrid_alpha < 1:
        strat = args.strategy[0] if args.strategy else "v6b_8f_pos_ic"
        if strat in STRATEGY_PROFILES:
            prof = STRATEGY_PROFILES[strat]
            sc_v6b = composite_score(
                {k: v for k, v in factors.items() if k in prof.factor_weights}, prof.factor_weights
            ) if prof.factor_weights else composite_score(factors)
            cd = score_ml.index.intersection(sc_v6b.index)
            cs = score_ml.columns.intersection(sc_v6b.columns)
            hybrid = pd.DataFrame(0.0, index=score_ml.index, columns=score_ml.columns)
            hybrid.loc[cd, cs] = (args.hybrid_alpha * score_ml.loc[cd, cs] +
                                   (1 - args.hybrid_alpha) * sc_v6b.loc[cd, cs])
            score_ml = hybrid
            label = f"ml_hybrid{int(args.hybrid_alpha*100)}"
            print(f"  Hybrid α={args.hybrid_alpha}: ML + {1-args.hybrid_alpha}×{strat}")

    # 4. 回测
    from scripts.run_backtest import run_backtest
    max_ind = 0 if args.no_industry else args.max_industry_weight
    bt_kwargs = dict(
        top_n=args.top_n, rebalance_freq=args.rebalance_freq,
        stop_loss=args.stop_loss, max_position=args.max_position,
        max_industry_weight=max_ind, max_daily_turnover=0, weight_method='equal',
        stock_names=None if args.no_industry else stock_names,
        exec_timing=args.exec_timing,
        use_vol_scaling=not args.no_vol_scaling, vol_target=args.vol_target,
        use_take_profit=args.use_take_profit,
        tp_tiers=[(0.10,0.30),(0.20,0.30),(0.30,1.00)] if args.use_take_profit else None,
        use_holding_decay=args.use_holding_decay,
        use_atr_stop=args.use_atr_stop, atr_k=args.atr_k,
    )
    if need_open: bt_kwargs['open_panel'] = open_panel

    ml_m, ml_nav, ml_tr = run_backtest(close_panel, score_ml, label=label, **bt_kwargs)
    metrics_list, nav_dict = [ml_m], {label: ml_nav}
    print(f"  {label}: {ml_m['annual_return']:.2%} / {ml_m['sharpe_ratio']:.2f} / {ml_m['max_drawdown']:.2%}")

    if fold_info:
        avg_t = np.mean([f['test_ic'] for f in fold_info])
        pos = sum(1 for f in fold_info if f['test_ic'] > 0)
        print(f"  Folds: avg test IC={avg_t:.4f}, positive={pos}/{len(fold_info)}")

    # 基准
    if not args.no_v6b:
        for sn in args.strategy:
            if sn not in STRATEGY_PROFILES: continue
            prof = STRATEGY_PROFILES[sn]
            sc = composite_score(
                {k: v for k, v in factors.items() if k in prof.factor_weights}, prof.factor_weights
            ) if prof.factor_weights else composite_score(factors)
            m, nav, tr = run_backtest(close_panel, sc, label=sn, **bt_kwargs)
            metrics_list.append(m); nav_dict[sn] = nav
            print(f"  {sn}: {m['annual_return']:.2%} / {m['sharpe_ratio']:.2f} / {m['max_drawdown']:.2%}")

    # 输出
    print(f"\n{'=' * 60}\n完成 ({time.time()-t0:.1f}s)")
    print(f"{'─' * 60}")
    for m in metrics_list:
        print(f"  {m['label']:<25} {m['annual_return']:>8.2%} {m['sharpe_ratio']:>7.2f} {m['max_drawdown']:>9.2%}")

    out_dir = args.output_dir or os.path.join(REPORT_DIR, datetime.now().strftime("%Y%m%d_%H%M%S_ml2"))
    os.makedirs(out_dir, exist_ok=True)
    for l, nav in nav_dict.items(): nav.to_csv(os.path.join(out_dir, f"nav_{l}.csv"))
    if fold_info: pd.DataFrame(fold_info).to_csv(os.path.join(out_dir, "ml_folds.csv"), index=False)
    print(f"\n结果已保存: {out_dir}/")

if __name__ == "__main__":
    main()
