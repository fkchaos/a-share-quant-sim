"""
Scoring engine — factor standardization + composite score.

Unified scoring for BOTH backtest (panel mode) and live simulation (single-stock mode).
All score computation goes through here — no duplication elsewhere.
"""

import numpy as np
import pandas as pd

from core.config import DEFAULT_FACTOR_WEIGHTS


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

    factors: {factor_name: DataFrame (dates x stocks)}
    weights: {factor_name: float}  -- missing = 0

    Returns: DataFrame (dates x stocks), composite score
    """
    if weights is None:
        weights = DEFAULT_FACTOR_WEIGHTS

    first_key = list(factors.keys())[0]
    template = factors[first_key]
    score = pd.DataFrame(0.0, index=template.index, columns=template.columns)

    for name, w in weights.items():
        if name not in factors:
            continue
        factor_data = factors[name]
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


def score_all_stocks(all_factors: dict, weights: dict = None, dynamic_weights: dict = None) -> dict:
    """Score all stocks for live simulation (single-stock mode).

    all_factors: {code: {factor_name: float}}
    weights:     {factor_name: float} -- missing = DEFAULT_FACTOR_WEIGHTS
    dynamic_weights: dict of (factor_name: callable)

    Returns: {code: score_float}
    """
    if weights is None:
        weights = DEFAULT_FACTOR_WEIGHTS

    effective_weights = dict(weights)
    if dynamic_weights:
        for fname, fn in dynamic_weights.items():
            if fname in effective_weights:
                effective_weights[fname] = fn(effective_weights[fname], all_factors)

    factor_names = [n for n in effective_weights if any(n in f for f in all_factors.values())]
    if not factor_names:
        return {code: 0.0 for code in all_factors}

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

    scores = {}
    for code in all_factors:
        score = sum(std_values[fname].get(code, 0.0) * effective_weights.get(fname, 0.0)
                    for fname in factor_names)
        scores[code] = score

    return scores


def rel_strength_adjust(all_factors: dict, stocks: list) -> dict:
    """Fill in rel_strength factors using cross-sectional comparison."""
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
    """计算因子面板的相关系数矩阵，用于因子去冗。"""
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

    redundant = []
    names = corr.columns.tolist()
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            c = corr.iloc[i, j]
            if abs(c) > 0.8:
                redundant.append((names[i], names[j], round(float(c), 4)))
    redundant.sort(key=lambda x: abs(x[2]), reverse=True)
    return corr, redundant


# ── Ensemble 多组选股 ────────────────────────────────────────────

def ensemble_union_score(
    factors: dict,
    ensemble_groups: dict,
    group_top_n: int = 4,
    min_groups: int = 1,
    crowd_threshold: float = 0.0,
    date=None,
    group_weight_multiplier: dict = None,
    date_weight_series: dict = None,
) -> pd.DataFrame:
    """多组 Ensemble 评分（面板模式，回测用）。

    每组独立评分选 top_n，最终 score = 选中该股票的组数 (0 ~ len(groups))。
    min_groups: 最少需要被多少组选中才计入最终评分（1=union, 2=intersection）
    crowd_threshold: 拥挤度过滤阈值（0=不过滤，0.9=排除综合拥挤度>90%的股票）
    group_weight_multiplier: 各组权重乘数 {'momentum': 2.0, 'reversal': 0.5, ...}
        全局乘数，所有日期统一应用
    date_weight_series: 各组按日期的权重乘数 {'momentum': pd.Series(date_idx), ...}
        用于 HMM 因子择时：不同日期（市场状态）用不同权重
        优先级高于 group_weight_multiplier
    """
    if not ensemble_groups:
        first_key = list(factors.keys())[0]
        return pd.DataFrame(0.0, index=factors[first_key].index, columns=factors[first_key].columns)

    first_key = list(factors.keys())[0]
    template = factors[first_key]
    dates = template.index
    stocks = template.columns

    selection_count = pd.DataFrame(0.0, index=dates, columns=stocks)

    # 拥挤度过滤：获取 crowd_score 面板
    crowd_mask = None
    if crowd_threshold > 0 and 'crowd_score' in factors:
        crowd_panel = factors['crowd_score']
        # 拥挤度在阈值以下的股票通过
        crowd_mask = crowd_panel <= crowd_threshold

    for group_name, weights in ensemble_groups.items():
        group_factors = {k: v for k, v in factors.items() if k in weights}
        if not group_factors:
            continue
        group_score = composite_score(group_factors, weights)

        for date in dates:
            if date not in group_score.index:
                continue
            day_scores = group_score.loc[date].dropna()
            if len(day_scores) < group_top_n:
                continue

            # 拥挤度过滤：排除拥挤度过高的股票
            if crowd_mask is not None and date in crowd_mask.index:
                crowded = crowd_mask.loc[date]
                day_scores = day_scores[~crowded.reindex(day_scores.index).fillna(False)]

            if len(day_scores) < group_top_n:
                continue

            for s in day_scores.nlargest(group_top_n).index:
                if s in selection_count.columns:
                    _w = 1.0
                    # 优先使用 per-date 权重
                    if date_weight_series and group_name in date_weight_series:
                        _ds = date_weight_series[group_name]
                        if date in _ds.index:
                            _w = _ds.loc[date]
                        else:
                            _w = 1.0
                    elif group_weight_multiplier and group_name in group_weight_multiplier:
                        _w = group_weight_multiplier[group_name]
                    selection_count.loc[date, s] += _w

    # intersection: 只保留被 min_groups+ 组选中的
    if min_groups > 1:
        selection_count = selection_count.where(selection_count >= min_groups, 0.0)

    return selection_count


def ensemble_union_score_single(
    all_factors: dict,
    ensemble_groups: dict,
    group_top_n: int = 4,
    min_groups: int = 1,
) -> dict:
    """多组 Ensemble 评分（单股模式，模拟盘用）。

    all_factors: {code: {factor_name: float}}
    min_groups: 最少需要被多少组选中才计入
    """
    if not ensemble_groups:
        return {code: 0.0 for code in all_factors}

    selection_count = {code: 0.0 for code in all_factors}

    for group_name, weights in ensemble_groups.items():
        factor_names = [f for f in weights if any(f in fdict for fdict in all_factors.values())]
        if not factor_names:
            continue

        std_values = {}
        for fname in factor_names:
            vals = {code: fdict.get(fname, np.nan) for code, fdict in all_factors.items()}
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

        group_scores = {}
        for code in all_factors:
            score = sum(std_values[fname].get(code, 0.0) * weights.get(fname, 0.0)
                        for fname in factor_names)
            group_scores[code] = score

        sorted_stocks = sorted(group_scores.items(), key=lambda x: x[1], reverse=True)
        for code, _ in sorted_stocks[:group_top_n]:
            selection_count[code] += 1.0

    # intersection filter
    if min_groups > 1:
        selection_count = {code: (s if s >= min_groups else 0.0) for code, s in selection_count.items()}

    return selection_count
