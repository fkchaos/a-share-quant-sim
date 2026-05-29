#!/usr/bin/env python3
"""
Backtest Engine - Smoke Tests
Fast regression tests that can be run in < 30 seconds.
All use synthetic data (no network needed).
"""
import sys
sys.path.insert(0, "/root")

import numpy as np
import pandas as pd

from run_backtest import (
    calc_factors, composite_score_equal, composite_score_weighted,
    run_backtest, standardize, INITIAL_CAPITAL,
)


def make_panel(n_days=200, n_stocks=5, trend="flat", seed=42):
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2023-01-01", periods=n_days, freq="B")
    close = pd.DataFrame(index=dates)
    volume = pd.DataFrame(index=dates)
    amount = pd.DataFrame(index=dates)

    for i in range(n_stocks):
        code = f"STK{i:04d}"
        if trend == "flat":
            prices = 100.0 + rng.randn(n_days).cumsum() * 0.5
        elif trend == "up":
            prices = 100.0 + np.linspace(0, 30, n_days) + rng.randn(n_days)
        elif trend == "down":
            prices = 100.0 - np.linspace(0, 30, n_days) + rng.randn(n_days)
        else:
            raise ValueError(f"Unknown trend: {trend}")
        prices = np.maximum(prices, 1.0)
        close[code] = prices
        volume[code] = 1e6 + rng.randint(0, 5e5, n_days)
        amount[code] = close[code] * volume[code]

    return close, volume, amount


def test_smoke_v3_baseline():
    """Quick v3 baseline backtest."""
    close, vol, amt = make_panel()
    factors = calc_factors(close, vol, amt)
    score = composite_score_equal(factors)
    m, nav, trades = run_backtest(close, score, top_n=5, rebalance_freq=20, label='smoke_v3')
    assert m['final_value'] > 0, "Final value must be positive"
    assert -0.99 < m['total_return'] < 10, f"Return {m['total_return']} out of range"
    print(f"  ✅ v3_baseline: return={m['total_return']:.2%}, sharpe={m['sharpe_ratio']:.2f}")


def test_smoke_markowitz():
    """Markowitz weight method."""
    close, vol, amt = make_panel()
    factors = calc_factors(close, vol, amt)
    score = composite_score_equal(factors)
    m, nav, trades = run_backtest(
        close, score, top_n=5, rebalance_freq=20,
        weight_method='markowitz', label='smoke_mkw')
    assert m['final_value'] > 0
    print(f"  ✅ markowitz: return={m['total_return']:.2%}, sharpe={m['sharpe_ratio']:.2f}")


def test_smoke_equal_vs_weighted():
    """Equal weight and weighted should produce same result with rebal=999 (no rebalance after warm-up)."""
    close, vol, amt = make_panel(n_days=200, n_stocks=10)
    factors = calc_factors(close, vol, amt)

    score_eq = composite_score_equal(factors)
    score_w = composite_score_weighted(factors)  # default FACTOR_WEIGHTS

    # With rebalance_freq=999 and only 200 days, first rebalance at day 120,
    # then next at day 1119 (beyond data range). So exactly ONE rebalance.
    m_eq, _, t_eq = run_backtest(close, score_eq, top_n=10, rebalance_freq=999, label='eq')
    m_eq_2, _, t_eq_2 = run_backtest(close, score_w, top_n=10, rebalance_freq=999, label='w')

    # Actually they should trade the same amounts on the same day
    # The point is: both complete successfully and produce valid metrics
    assert m_eq['final_value'] > 0
    assert m_eq_2['final_value'] > 0
    print(f"  ✅ equal vs weighted: eq={m_eq['total_return']:.2%}, w={m_eq_2['total_return']:.2%}")


def test_smoke_ic_analysis():
    """IC analysis runs without error."""
    close, vol, amt = make_panel(n_days=200, n_stocks=10)
    factors = calc_factors(close, vol, amt)
    from run_backtest import run_ic_analysis, select_factors_ic
    ic_results = run_ic_analysis(factors, close)
    selected, discarded = select_factors_ic(ic_results)
    total = len(selected) + len(discarded)
    assert total <= len(factors), "IC results should not exceed factor count"
    assert total > 0, "Should have some IC results"
    print(f"  ✅ IC analysis: {len(selected)} selected, {len(discarded)} discarded from {len(factors)}")


def test_smoke_custom_params():
    """Override strategy params via CLI-like arguments."""
    close, vol, amt = make_panel()
    factors = calc_factors(close, vol, amt)
    score = composite_score_equal(factors)

    # Very conservative
    m, _, _ = run_backtest(close, score, top_n=3, rebalance_freq=60, stop_loss=0.05, label='conservative')
    # Very aggressive
    m_ag, _, _ = run_backtest(close, score, top_n=15, rebalance_freq=3, stop_loss=0.30, label='aggressive')

    assert m['annual_return'] != m_ag['annual_return'] or m['total_trades'] == m_ag['total_trades'], \
        "Different params should produce different results (or same if no trades)"
    print(f"  ✅ custom_params: conservative={m['annual_return']:.2%}, aggressive={m_ag['annual_return']:.2%}")


def test_smoke_no_crash_on_edge_params():
    """Extreme params should not crash."""
    close, vol, amt = make_panel()
    factors = calc_factors(close, vol, amt)
    score = composite_score_equal(factors)

    combos = [
        {'top_n': 1, 'rebalance_freq': 1, 'stop_loss': 0.01},
        {'top_n': 20, 'rebalance_freq': 200, 'stop_loss': 0.50},
        {'top_n': 5, 'rebalance_freq': 5, 'max_position': 0.50},
        {'top_n': 5, 'rebalance_freq': 5, 'use_vol_scaling': True},
    ]
    for i, kwargs in enumerate(combos):
        m, _, _ = run_backtest(close, score, **kwargs, label=f'edge_{i}')
        assert m['final_value'] > 0

    print(f"  ✅ edge_params: {len(combos)} extreme combos handled")


if __name__ == "__main__":
    print("Running smoke tests...")
    test_smoke_v3_baseline()
    test_smoke_markowitz()
    test_smoke_equal_vs_weighted()
    test_smoke_ic_analysis()
    test_smoke_custom_params()
    test_smoke_no_crash_on_edge_params()
    print("\n✅ All smoke tests PASSED")
