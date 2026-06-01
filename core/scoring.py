"""
Scoring engine — factor standardization + composite score.

Unified scoring for BOTH backtest (panel mode) and live simulation (single-stock mode).
All score computation goes through here — no duplication elsewhere.
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
    """Compute composite score from factor DataFrames (panel mode).

    factors: {factor_name: DataFrame (dates × stocks)}
    weights: {factor_name: float}  — missing = 0

    Returns: DataFrame (dates × stocks), composite score
    """
    if weights is None:
        weights = config.factor_weights

    first_key = list(factors.keys())[0]
    template = factors[first_key]
    score = pd.DataFrame(0.0, index=template.index, columns=template.columns)

    for name, w in weights.items():
        if name not in factors:
            continue
        factor_data = factors[name]
        # Skip non-DataFrame factors (e.g. degraded constant Series)
        if not isinstance(factor_data, pd.DataFrame):
            continue
        std_df = standardize(factor_data)
        score = score.add(w * std_df, fill_value=0)

    return score


def composite_score_equal(factors: dict) -> pd.DataFrame:
    """Equal-weight composite score (v3 baseline)."""
    n = len(factors)
    weights = {name: 1.0 / n for name in factors}
    return composite_score(factors, weights)


def score_all_stocks(all_factors: dict, weights: dict = None) -> dict:
    """Score all stocks for live simulation (single-stock mode).

    all_factors: {code: {factor_name: float}}
    weights:     {factor_name: float} — missing = config.factor_weights

    Returns: {code: score_float}

    This is the single source of truth for live scoring.
    It mirrors the cross-sectional standardization logic used by composite_score(panel).
    """
    if weights is None:
        weights = config.factor_weights

    # Collect all factor names that have weights
    factor_names = [n for n in weights if any(n in f for f in all_factors.values())]

    if not factor_names:
        return {code: 0.0 for code in all_factors}

    # Cross-sectional standardization for each factor
    std_values = {}
    for fname in factor_names:
        vals = {code: f.get(fname, np.nan) for code, f in all_factors.items()}
        arr = np.array(list(vals.values()))
        valid_mask = ~np.isnan(arr) & ~np.isinf(arr)
        if valid_mask.sum() < 10:
            std_values[fname] = {code: 0.0 for code in all_factors}
            continue
        valid_vals = arr[valid_mask]
        mean = np.mean(valid_vals)
        std = np.std(valid_vals)
        if std == 0:
            std_values[fname] = {code: 0.0 for code in all_factors}
            continue
        std_values[fname] = {code: (v - mean) / std if not np.isnan(v) else 0.0
                             for code, v in vals.items()}

    # Weighted sum
    scores = {}
    for code in all_factors:
        score = sum(std_values[fname].get(code, 0.0) * w
                    for fname, w in weights.items())
        scores[code] = score

    return scores


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


def factor_correlation(factors: dict, date=None):
    """计算因子面板的相关系数矩阵，用于因子去冗。

    factors: {factor_name: DataFrame (dates × stocks)}
    date:    指定日期（取该日截面）；None 则取最后一日

    返回: (corr_matrix, redundant_pairs)
      - corr_matrix: DataFrame (factor × factor)
      - redundant_pairs: [(factor_a, factor_b, corr), ...] 高相关对 (|ρ| > 0.8)
    """
    # 取截面数据
    snapshot = {}
    for fname, fdf in factors.items():
        if date is not None and date in fdf.index:
            snapshot[fname] = fdf.loc[date]
        elif len(fdf) > 0:
            snapshot[fname] = fdf.iloc[-1]

    if not snapshot:
        return pd.DataFrame(), []

    snap_df = pd.DataFrame(snapshot).dropna(axis=1, how='all')
    corr = snap_df.corr()

    # 找高相关对
    redundant = []
    names = corr.columns.tolist()
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            c = corr.iloc[i, j]
            if abs(c) > 0.8:
                redundant.append((names[i], names[j], round(float(c), 4)))

    redundant.sort(key=lambda x: abs(x[2]), reverse=True)
    return corr, redundant
