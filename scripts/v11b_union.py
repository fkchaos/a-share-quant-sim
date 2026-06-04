#!/usr/bin/env python3
"""
v11b: Multi-Group Union Strategy
================================

3 independent factor groups → each selects top 4 → union = final portfolio (up to 12 stocks)

Factor groups:
  Group A (Momentum):   mom_20, mom_10, rsi_14, high_low_range
  Group B (Volatility): vol_60, vol_20, vol_10, boll_width_20
  Group C (Reversal):   rev_10, rev_5, rsi_6, boll_pos_10

Key difference from v10c:
  - v10c: single composite score → top 12
  - v11b: 3 groups × top 4 → union (stocks selected by multiple groups get higher score)
  - No market state detection needed — natural diversification
"""

import os
import sys
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import config as core_config, STRATEGY_PROFILES
from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.data import load_and_build_panel
from scripts.run_backtest import run_backtest, save_results, generate_report


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

GROUP_TOP_N = 4


def build_union_score(factors: dict) -> pd.DataFrame:
    """Build union score: count how many groups select each stock.

    Returns: DataFrame (dates × stocks), score = 0 to 3
    """
    first_key = list(factors.keys())[0]
    template = factors[first_key]
    dates = template.index
    stocks = template.columns

    selection_count = pd.DataFrame(0.0, index=dates, columns=stocks)

    for group_name, weights in FACTOR_GROUPS.items():
        group_factors = {k: v for k, v in factors.items() if k in weights}
        if not group_factors:
            continue

        group_score = composite_score(group_factors, weights)

        for date in dates:
            if date not in group_score.index:
                continue
            day_scores = group_score.loc[date].dropna()
            if len(day_scores) < GROUP_TOP_N:
                continue
            top_stocks = day_scores.nlargest(GROUP_TOP_N).index
            for s in top_stocks:
                if s in selection_count.columns:
                    selection_count.loc[date, s] += 1.0

    return selection_count


def walk_forward_union(close_panel, volume_panel, amount_panel,
                       high_panel, low_panel,
                       train_days=252, test_days=63, step_days=63,
                       top_n=12, rebalance_freq=20, stop_loss=0.20,
                       label='v11b'):
    """Custom WF for union strategy — builds union score from windowed factors."""
    from core.factors import calc_factors_panel

    dates = close_panel.index
    n = len(dates)
    fold_results = []
    fold_navs = []
    fold = 0

    def _slice_panel(panel, idx):
        if panel is not None:
            return panel.loc[idx]
        return None

    train_end = train_days
    while train_end + test_days <= n:
        fold += 1
        train_start = max(0, train_end - train_days)
        test_start = train_end
        test_end = min(n, train_end + test_days)

        window_dates = dates[train_start:test_end]
        sub_close = close_panel.loc[window_dates]
        sub_volume = _slice_panel(volume_panel, window_dates)
        sub_amount = _slice_panel(amount_panel, window_dates)
        sub_high = _slice_panel(high_panel, window_dates)
        sub_low = _slice_panel(low_panel, window_dates)

        # Calculate factors from windowed data (no look-ahead)
        sub_factors = calc_factors_panel(
            sub_close, sub_volume, sub_amount,
            high_panel=sub_high, low_panel=sub_low,
        )

        # Build union score from windowed factors
        sub_score = build_union_score(sub_factors)

        # Slice test period
        test_dates = dates[test_start:test_end]
        sub_score_test = sub_score.loc[test_dates]
        sub_close_test = sub_close.loc[test_dates]

        # Run backtest
        _warmup = train_end - train_start
        m, nav, _ = run_backtest(
            sub_close, sub_score,
            top_n=top_n, rebalance_freq=rebalance_freq,
            stop_loss=stop_loss, label=f'{label}_fold{fold}',
            warmup_days=_warmup,
        )

        fold_results.append({
            'fold': fold,
            'train': f"{dates[train_start].date()}~{dates[test_start-1].date()}",
            'test': f"{dates[test_start].date()}~{dates[test_end-1].date()}",
            'ann_return': m['annual_return'],
            'sharpe': m['sharpe_ratio'],
            'max_dd': m['max_drawdown'],
            'sortino': m['sortino_ratio'],
            'trades': m['total_trades'],
        })
        fold_navs.append(nav)

        print(f"  WF Fold {fold}: {fold_results[-1]['test']} | "
              f"Ret={m['annual_return']:.1%} Sharpe={m['sharpe_ratio']:.2f} "
              f"DD={m['max_drawdown']:.1%}")

        train_end += step_days

    # Combine navs
    if fold_navs:
        combined_nav = fold_navs[0] / fold_navs[0].iloc[0]
        for fnav in fold_navs[1:]:
            combined_nav = combined_nav * (fnav / fnav.iloc[0])
    else:
        combined_nav = None

    return fold_results, combined_nav


def main():
    print("=" * 60)
    print("v11b: Multi-Group Union Strategy")
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

    # ── 3. Build union score ──
    print("\n[3/4] Building union score (3 groups × top4)...")
    union_score = build_union_score(factors)
    print(f"  Union score shape: {union_score.shape}")

    # ── 4. Backtest ──
    print("\n[4/4] Running backtest...")

    # v11b union
    print("\n  ▶ v11b_union:")
    m_v11b, nav_v11b, trades_v11b = run_backtest(
        close_panel, union_score,
        top_n=12, rebalance_freq=20, stop_loss=0.20,
        max_position=0.10, label='v11b_union',
    )
    print(f"    Return={m_v11b['annual_return']:.2%}, "
          f"Sharpe={m_v11b['sharpe_ratio']:.2f}, "
          f"MaxDD={m_v11b['max_drawdown']:.2f}")

    # v10c baseline
    print("\n  ▶ v10c_zz800_balanced (baseline):")
    v10c_profile = STRATEGY_PROFILES.get('v10c_zz800_balanced')
    if v10c_profile and v10c_profile.factor_weights:
        v10c_score = composite_score(
            {k: v for k, v in factors.items() if k in v10c_profile.factor_weights},
            v10c_profile.factor_weights,
        )
    else:
        v10c_score = composite_score(factors)
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

    # v11b WF (custom, no look-ahead)
    print("\n  --- WF: v11b_union ---")
    wf_v11b, wf_nav_v11b = walk_forward_union(
        close_panel, volume_panel, amount_panel,
        high_panel, low_panel,
        train_days=252, test_days=63, step_days=63,
        top_n=12, rebalance_freq=20, stop_loss=0.20,
        label='v11b_union',
    )
    if wf_v11b:
        avg_sharpe = np.mean([r['sharpe'] for r in wf_v11b])
        avg_ret = np.mean([r['ann_return'] for r in wf_v11b])
        pos = sum(1 for r in wf_v11b if r['ann_return'] > 0)
        print(f"\n  v11b WF Summary: {len(wf_v11b)} folds")
        print(f"    Avg Return: {avg_ret:.1%} | Avg Sharpe: {avg_sharpe:.2f}")
        print(f"    Positive folds: {pos}/{len(wf_v11b)}")

    # v10c WF
    print("\n  --- WF: v10c_zz800_balanced ---")
    from scripts.run_backtest import walk_forward
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
    print(f"v11b Multi-Group Union Results ({elapsed:.1f}s)")
    print(f"{'=' * 60}")
    print(f"\n{'Strategy':<25} {'Return':>8} {'Sharpe':>7} {'MaxDD':>8} {'Trades':>7}")
    print(f"{'─' * 60}")
    for m in [m_v11b, m_v10c]:
        print(f"{m['label']:<25} {m['annual_return']:>7.2%} "
              f"{m['sharpe_ratio']:>7.2f} {m['max_drawdown']:>7.2%} "
              f"{m['total_trades']:>7}")

    if wf_v11b and wf_v10c:
        print(f"\n{'WF Comparison':^60}")
        print(f"{'─' * 60}")
        v11b_pos = sum(1 for r in wf_v11b if r['ann_return'] > 0)
        v10c_pos = sum(1 for r in wf_v10c if r['ann_return'] > 0)
        v11b_avg = np.mean([r['ann_return'] for r in wf_v11b])
        v10c_avg = np.mean([r['ann_return'] for r in wf_v10c])
        v11b_sharpe = np.mean([r['sharpe'] for r in wf_v11b])
        v10c_sharpe = np.mean([r['sharpe'] for r in wf_v10c])
        print(f"{'Metric':<25} {'v11b':>12} {'v10c':>12}")
        print(f"{'─' * 60}")
        print(f"{'WF Avg Return':<25} {v11b_avg:>11.1%} {v10c_avg:>11.1%}")
        print(f"{'WF Avg Sharpe':<25} {v11b_sharpe:>12.2f} {v10c_sharpe:>12.2f}")
        print(f"{'WF Positive Folds':<25} {v11b_pos:>8}/{len(wf_v11b):<3} {v10c_pos:>8}/{len(wf_v10c):<3}")

        # Per-fold comparison
        print(f"\n{'Per-Fold Comparison':^60}")
        print(f"{'─' * 60}")
        print(f"{'Fold':<6} {'v11b Return':>12} {'v11b Sharpe':>12} {'v10c Return':>12} {'v10c Sharpe':>12}")
        print(f"{'─' * 60}")
        for i in range(min(len(wf_v11b), len(wf_v10c))):
            print(f"{i+1:<6} {wf_v11b[i]['ann_return']:>11.1%} {wf_v11b[i]['sharpe']:>12.2f} "
                  f"{wf_v10c[i]['ann_return']:>11.1%} {wf_v10c[i]['sharpe']:>12.2f}")

    return m_v11b, m_v10c, wf_v11b, wf_v10c


if __name__ == "__main__":
    main()
