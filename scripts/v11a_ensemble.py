#!/usr/bin/env python3
"""
v11a: Multi-Group Ensemble Strategy
====================================

Fundamentally different from v10c:
- v10c: single composite score → select top N
- v11a: 3 independent factor groups → each selects top 4 → union = final portfolio

Factor groups:
  Group A (Momentum):  mom_20, mom_10, rsi_14, high_low_range
  Group B (Volatility): vol_60, vol_20, vol_10, boll_width_20
  Group C (Reversal):  rev_10, rev_5, rsi_6, boll_pos_10

Rationale:
  - Different market regimes favor different factor types
  - Instead of timing factors (unreliable), let all groups contribute
  - If momentum fails in bear market, vol/reversal groups may still work
  - Natural diversification without explicit market state detection
"""

import os
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import config as core_config, STRATEGY_PROFILES
from core.factors import calc_factors_panel
from core.scoring import composite_score, standardize
from core.data import load_and_build_panel
from scripts.run_backtest import run_backtest, walk_forward, save_results, generate_report


# ── Factor group definitions ──────────────────────────────────────
FACTOR_GROUPS = {
    'momentum': {
        'mom_20': 0.30,
        'mom_10': 0.25,
        'rsi_14': 0.25,
        'high_low_range': 0.20,
    },
    'volatility': {
        'vol_60': 0.30,
        'vol_20': 0.25,
        'vol_10': 0.25,
        'boll_width_20': 0.20,
    },
    'reversal': {
        'rev_10': 0.30,
        'rev_5': 0.25,
        'rsi_6': 0.25,
        'boll_pos_10': 0.20,
    },
}

GROUP_TOP_N = 4  # each group selects top 4


def build_ensemble_score(factors: dict, close_panel: pd.DataFrame) -> pd.DataFrame:
    """Build ensemble score by combining group selections.

    For each date:
    1. Each factor group independently scores all stocks
    2. Each group selects top GROUP_TOP_N stocks
    3. Final score = sum of group scores for stocks selected by any group
       (stocks selected by multiple groups get higher score)

    Returns: DataFrame (dates × stocks), composite ensemble score
    """
    dates = close_panel.index
    stocks = close_panel.columns
    ensemble_score = pd.DataFrame(0.0, index=dates, columns=stocks)

    for group_name, weights in FACTOR_GROUPS.items():
        # Build group score
        group_factors = {k: v for k, v in factors.items() if k in weights}
        if not group_factors:
            print(f"  ⚠️ Group '{group_name}' has no valid factors, skipping")
            continue

        group_score = composite_score(group_factors, weights)

        # For each date, find top GROUP_TOP_N stocks
        for date in dates:
            if date not in group_score.index:
                continue
            day_scores = group_score.loc[date].dropna()
            if len(day_scores) < GROUP_TOP_N:
                continue
            top_stocks = day_scores.nlargest(GROUP_TOP_N).index
            # Add group score to ensemble (stocks in multiple groups get boost)
            for s in top_stocks:
                if s in ensemble_score.columns:
                    ensemble_score.loc[date, s] += group_score.loc[date, s]

    return ensemble_score


def main():
    print("=" * 60)
    print("v11a: Multi-Group Ensemble Strategy")
    print("=" * 60)
    t0 = time.time()

    # ── 1. Load data ──
    print("\n[1/4] Loading data...")
    loaded, codes = load_and_build_panel(
        "2021-01-01", None,
        need_open=False, need_hl=True,
        market_filter=core_config.market,
    )
    close_panel = loaded[0]
    volume_panel = loaded[1]
    amount_panel = loaded[2]
    high_panel = loaded[4] if len(loaded) > 4 else None
    low_panel = loaded[5] if len(loaded) > 5 else None
    print(f"  Panel: {close_panel.shape[0]} days × {close_panel.shape[1]} stocks")

    # ── 2. Factor calculation ──
    print("\n[2/4] Calculating factors...")
    factors = calc_factors_panel(
        close_panel, volume_panel, amount_panel,
        high_panel=high_panel, low_panel=low_panel,
    )
    print(f"  {len(factors)} factors calculated")

    # ── 3. Build ensemble score ──
    print("\n[3/4] Building ensemble score (3 groups × top4)...")
    ensemble_score = build_ensemble_score(factors, close_panel)
    print(f"  Ensemble score shape: {ensemble_score.shape}")

    # ── 4. Backtest ──
    print("\n[4/4] Running backtest...")

    # v11a ensemble
    print("\n  ▶ v11a_ensemble:")
    m_v11a, nav_v11a, trades_v11a = run_backtest(
        close_panel, ensemble_score,
        top_n=12, rebalance_freq=20, stop_loss=0.20,
        max_position=0.10, label='v11a_ensemble',
    )
    print(f"    Return={m_v11a['annual_return']:.2%}, "
          f"Sharpe={m_v11a['sharpe_ratio']:.2f}, "
          f"MaxDD={m_v11a['max_drawdown']:.2f}")

    # v10c baseline for comparison
    print("\n  ▶ v10c_zz800_balanced (baseline):")
    v10c_profile = STRATEGY_PROFILES.get('v10c_zz800_balanced')
    if v10c_profile and v10c_profile.factor_weights:
        v10c_score = composite_score(
            {k: v for k, v in factors.items() if k in v10c_profile.factor_weights},
            v10c_profile.factor_weights,
        )
    else:
        from core.scoring import composite_score as cs
        v10c_score = cs(factors)
    m_v10c, nav_v10c, trades_v10c = run_backtest(
        close_panel, v10c_score,
        top_n=12, rebalance_freq=20, stop_loss=0.20,
        max_position=0.10, label='v10c_zz800_balanced',
    )
    print(f"    Return={m_v10c['annual_return']:.2%}, "
          f"Sharpe={m_v10c['sharpe_ratio']:.2f}, "
          f"MaxDD={m_v10c['max_drawdown']:.2f}")

    # ── 5. Walk-Forward ──
    print("\n[5/4] Walk-Forward analysis...")

    # Build WF-compatible score function for v11a
    def v11a_score_fn(factors_dict, w=None):
        return build_ensemble_score(factors_dict, close_panel)

    # v11a WF
    print("\n  --- WF: v11a_ensemble ---")
    wf_v11a, wf_nav_v11a = walk_forward(
        close_panel,
        train_days=252, test_days=63, step_days=63,
        top_n=12, rebalance_freq=20, stop_loss=0.20,
        label='v11a_ensemble',
        volume_panel=volume_panel, amount_panel=amount_panel,
        high_panel=high_panel, low_panel=low_panel,
    )
    if wf_v11a:
        avg_sharpe = np.mean([r['sharpe'] for r in wf_v11a])
        avg_ret = np.mean([r['ann_return'] for r in wf_v11a])
        pos = sum(1 for r in wf_v11a if r['ann_return'] > 0)
        print(f"\n  v11a WF Summary: {len(wf_v11a)} folds")
        print(f"    Avg Return: {avg_ret:.1%} | Avg Sharpe: {avg_sharpe:.2f}")
        print(f"    Positive folds: {pos}/{len(wf_v11a)}")

    # v10c WF
    print("\n  --- WF: v10c_zz800_balanced ---")
    wf_v10c, wf_nav_v10c = walk_forward(
        close_panel,
        train_days=252, test_days=63, step_days=63,
        top_n=12, rebalance_freq=20, stop_loss=0.20,
        factor_weights=v10c_profile.factor_weights if v10c_profile else None,
        label='v10c_zz800_balanced',
        volume_panel=volume_panel, amount_panel=amount_panel,
        high_panel=high_panel, low_panel=low_panel,
    )
    if wf_v10c:
        avg_sharpe = np.mean([r['sharpe'] for r in wf_v10c])
        avg_ret = np.mean([r['ann_return'] for r in wf_v10c])
        pos = sum(1 for r in wf_v10c if r['ann_return'] > 0)
        print(f"\n  v10c WF Summary: {len(wf_v10c)} folds")
        print(f"    Avg Return: {avg_ret:.1%} | Avg Sharpe: {avg_sharpe:.2f}")
        print(f"    Positive folds: {pos}/{len(wf_v10c)}")

    # ── Summary ──
    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"v11a Multi-Group Ensemble Results ({elapsed:.1f}s)")
    print(f"{'=' * 60}")
    print(f"\n{'Strategy':<25} {'Return':>8} {'Sharpe':>7} {'MaxDD':>8} {'Trades':>7}")
    print(f"{'─' * 60}")
    for m in [m_v11a, m_v10c]:
        print(f"{m['label']:<25} {m['annual_return']:>7.2%} "
              f"{m['sharpe_ratio']:>7.2f} {m['max_drawdown']:>7.2%} "
              f"{m['total_trades']:>7}")

    if wf_v11a and wf_v10c:
        print(f"\n{'WF Comparison':^60}")
        print(f"{'─' * 60}")
        v11a_pos = sum(1 for r in wf_v11a if r['ann_return'] > 0)
        v10c_pos = sum(1 for r in wf_v10c if r['ann_return'] > 0)
        v11a_avg = np.mean([r['ann_return'] for r in wf_v11a])
        v10c_avg = np.mean([r['ann_return'] for r in wf_v10c])
        v11a_sharpe = np.mean([r['sharpe'] for r in wf_v11a])
        v10c_sharpe = np.mean([r['sharpe'] for r in wf_v10c])
        print(f"{'Metric':<25} {'v11a':>12} {'v10c':>12}")
        print(f"{'─' * 60}")
        print(f"{'WF Avg Return':<25} {v11a_avg:>11.1%} {v10c_avg:>11.1%}")
        print(f"{'WF Avg Sharpe':<25} {v11a_sharpe:>12.2f} {v10c_sharpe:>12.2f}")
        print(f"{'WF Positive Folds':<25} {v11a_pos:>8}/{len(wf_v11a):<3} {v10c_pos:>8}/{len(wf_v10c):<3}")

    return m_v11a, m_v10c, wf_v11a, wf_v10c


if __name__ == "__main__":
    main()
