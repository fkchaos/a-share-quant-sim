#!/usr/bin/env python3
"""
Ensemble 参数扫描：group_top_n × 组权重 全组合。

输出：每组参数的回测指标 + 推荐最优。

用法：
    python scripts/sweep_v11b_params.py                   # 默认扫描
    python scripts/sweep_v11b_params.py --csv             # 额外输出 CSV
"""

import json
import time
import numpy as np
import pandas as pd
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BACKTEST_DATA_DIR", "/root/data")

from core.factors import calc_factors_panel
from core.data import load_and_build_panel
from core.scoring import ensemble_union_score
from core.config import STRATEGY_PROFILES, PROFILE_V11B_ZZ800_UNION
from scripts.run_backtest import run_backtest


def sweep(close_panel, factors, base_profile, csv_output=False):
    """扫描 group_top_n 和组权重组合。"""
    group_top_n_range = [3, 4, 5, 6]
    # 当前 v11b 的基准权重
    base_momentum = {'mom_20': 0.30, 'mom_10': 0.25, 'rsi_14': 0.25, 'high_low_range': 0.20}
    base_volatility = {'vol_60': 0.30, 'vol_20': 0.25, 'vol_10': 0.25, 'boll_width_20': 0.20}
    base_reversal = {'rev_10': 0.30, 'rev_5': 0.25, 'rsi_6': 0.25, 'boll_pos_10': 0.20}

    # 权重微调：每组内部的 mom/hl 权重交换
    weight_variants = [
        ("base", base_momentum, base_volatility, base_reversal),
        ("mom_heavy", {'mom_20': 0.35, 'mom_10': 0.30, 'rsi_14': 0.20, 'high_low_range': 0.15},
                      base_volatility, base_reversal),
        ("rev_heavy", base_momentum, base_volatility,
                     {'rev_10': 0.35, 'rev_5': 0.30, 'rsi_6': 0.20, 'boll_pos_10': 0.15}),
        ("vol_light", base_momentum,
                     {'vol_60': 0.20, 'vol_20': 0.25, 'vol_10': 0.30, 'boll_width_20': 0.25},
                     base_reversal),
    ]

    results = []
    t0 = time.time()

    for top_n in group_top_n_range:
        for wname, mom, vol, rev in weight_variants:
            label = f"v11b_ens_gtn{top_n}_{wname}"
            groups = {'momentum': mom, 'volatility': vol, 'reversal': rev}

            score = ensemble_union_score(factors, groups, group_top_n=top_n)

            try:
                metrics, nav, trades = run_backtest(
                    close_panel, score,
                    label=label,
                    top_n=base_profile.top_n,
                    rebalance_freq=base_profile.rebalance_freq,
                    stop_loss=base_profile.stop_loss,
                    max_position=base_profile.max_position,
                    max_industry_weight=base_profile.max_industry_weight,
                    max_daily_turnover=base_profile.max_daily_turnover,
                    weight_method=base_profile.weight_method,
                    use_take_profit=base_profile.use_take_profit,
                    tp_tiers=base_profile.tp_tiers,
                    use_holding_decay=base_profile.use_holding_decay,
                    stock_names=None,
                    exec_timing="close",
                )
                results.append({
                    'group_top_n': top_n,
                    'weight_variant': wname,
                    'annual_return': round(metrics['annual_return'], 4),
                    'sharpe': round(metrics['sharpe_ratio'], 3),
                    'max_drawdown': round(metrics['max_drawdown'], 4),
                    'win_rate': round(metrics.get('win_rate', 0), 3),
                    'n_trades': metrics.get('n_trades', 0),
                })
                print(f"  {label}: Return={metrics['annual_return']:.2%}, "
                      f"Sharpe={metrics['sharpe_ratio']:.2f}, "
                      f"MaxDD={metrics['max_drawdown']:.2%}")
            except Exception as e:
                print(f"  {label}: ERROR - {e}")

    elapsed = time.time() - t0
    df = pd.DataFrame(results)

    if len(df) > 0:
        print(f"\n{'='*60}")
        print(f"扫描完成：{len(df)} 组，耗时 {elapsed:.0f}s")
        print(f"{'='*60}")

        # 按 Sharpe 排序
        df_sorted = df.sort_values('sharpe', ascending=False)
        print("\nTop 5 (by Sharpe):")
        print(df_sorted.head(5).to_string(index=False))

        # 按 Return 排序
        df_ret = df.sort_values('annual_return', ascending=False)
        print("\nTop 5 (by Return):")
        print(df_ret.head(5).to_string(index=False))

        # 综合评分：Sharpe × 0.4 + Return × 0.4 + WinRate × 0.2
        if 'win_rate' in df.columns:
            df['score'] = (df['sharpe'] * 0.4 +
                           df['annual_return'] * 0.4 +
                           df['win_rate'] * 0.2)
            df_best = df.sort_values('score', ascending=False)
            print("\nTop 5 (综合评分):")
            print(df_best.head(5).to_string(index=False))

        if csv_output:
            out_path = "/root/data/backtest_results/v11b_sweep.csv"
            df_sorted.to_csv(out_path, index=False)
            print(f"\n结果已保存: {out_path}")

        return df

    return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="v11b Ensemble 参数扫描")
    parser.add_argument("--csv", action="store_true", help="输出 CSV 文件")
    args = parser.parse_args()

    print("[1/2] 加载数据...")
    loaded, codes = load_and_build_panel("2021-01-01", "today")
    close_panel, volume_panel, amount_panel = loaded[0], loaded[1], loaded[2]
    print(f"  {len(codes)} 只股票, {len(close_panel)} 个交易日")

    print("[2/2] 计算因子...")
    factors = calc_factors_panel(close_panel, volume_panel, amount_panel)

    print("\n开始参数扫描...")
    profile = STRATEGY_PROFILES["v11b_zz800_union"]
    sweep(close_panel, factors, profile, csv_output=args.csv)
