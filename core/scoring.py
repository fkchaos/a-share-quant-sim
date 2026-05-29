"""
Scoring engine — factor standardization + composite score.

Mirrors the logic that was previously duplicated between sim_account.py and run_backtest.py.
"""

import numpy as np
import pandas as pd

from core.config import config


def standardize(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional Z-score standardization (row-wise)."""
    mean = df.mean(axis=1)
    std = df.std(axis=1).replace(0, np.nan)
    return df.sub(mean, axis=0).div(std, axis=0)


def composite_score(
    factors: dict,
    weights: dict = None,
) -> pd.DataFrame:
    """Compute composite score from factor DataFrames.

    factors: {factor_name: DataFrame (dates × stocks)}
    weights: {factor_name: float}  — missing = 0

    Returns: DataFrame (dates × stocks), composite score
    """
    if weights is None:
        weights = config.factor_weights

    # Find common index/cols from first factor
    first_key = list(factors.keys())[0]
    template = factors[first_key]
    score = pd.DataFrame(0.0, index=template.index, columns=template.columns)

    for name, w in weights.items():
        if name in factors:
            std_df = standardize(factors[name])
            score = score.add(w * std_df, fill_value=0)

    return score


def composite_score_equal(factors: dict) -> pd.DataFrame:
    """Equal-weight composite score (v3 baseline)."""
    n = len(factors)
    weights = {name: 1.0 / n for name in factors}
    return composite_score(factors, weights)


def rel_strength_adjust(all_factors: dict, stocks: list) -> dict:
    """Fill in rel_strength factors using cross-sectional comparison (single-stock mode)."""
    for w, name in [(20, 'rel_strength_20'), (60, 'rel_strength_60')]:
        mom_key = f'mom_{w}'
        if mom_key in all_factors:
            vals = [all_factors[code].get(mom_key, np.nan) for code in stocks]
            vals = [v for v in vals if not np.isnan(v)]
            if vals:
                mean_val = np.mean(vals)
                for code in stocks:
                    if mom_key in all_factors.get(code, {}):
                        all_factors[code][name] = all_factors[code][mom_key] - mean_val
    return all_factors
