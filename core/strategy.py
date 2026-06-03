"""
Strategy Engine — 统一选股评分入口
====================================

所有选股评分的统一入口，支持：
  1. 纯因子加权（传统方式，score_all_stocks）
  2. ML 预测（MLPredictor）
  3. Hybrid：α × ML + (1-α) × 因子加权

设计原则：
  - 策略名称（profile）即配置，不硬编码
  - 所有策略共用同一套选股过滤（板块/流动性/行业分散）
  - 回测（ml_rolling_train.py）和模拟盘（sim_daily_v7.py）用同一入口
  - ML 训练/推理分离（ml_predictor.py 负责）

评分模式：
  - mode = "factor"   → 纯因子加权（score_all_stocks）
  - mode = "ml"       → 纯 ML 推理（MLPredictor）
  - mode = "hybrid"   → ML + 因子加权混合

Usage (回测):
    from core.strategy import StrategyEngine
    engine = StrategyEngine(profile="v6b_8f_pos_ic", mode="hybrid", hybrid_alpha=0.8)
    scores = engine.score(all_factors_panel)  # DataFrame (dates × stocks)

Usage (模拟盘):
    from core.strategy import StrategyEngine
    engine = StrategyEngine(profile="v6b_8f_pos_ic", mode="hybrid")
    scores = engine.score_single(all_factors_dict)  # {code: score}
"""

import os
import numpy as np
import pandas as pd
from typing import Dict, Optional, Tuple, List

from core.config import config, STRATEGY_PROFILES
from core.scoring import score_all_stocks, composite_score
from core.ml_predictor import MLPredictor


class StrategyEngine:
    """
    统一策略评分引擎。

    Parameters
    ----------
    profile : str
        策略 profile 名（STRATEGY_PROFILES 中的 key）
    mode : str
        'factor' | 'ml' | 'hybrid'
    hybrid_alpha : float
        ML 因子混合比（仅 hybrid 模式），0.8 = 80% ML + 20% 因子
    model_dir : str
        ML 模型目录（ml/hybrid 模式必须）
    dynamic_weights : dict
        动态权重回调（如小市值择时），可选
    """

    def __init__(
        self,
        profile: str = "v6b_8f_pos_ic",
        mode: str = "factor",
        hybrid_alpha: float = 0.8,
        model_dir: str = "/root/data/ml_models",
        dynamic_weights: dict = None,
    ):
        self.profile_name = profile
        self.mode = mode
        self.hybrid_alpha = hybrid_alpha
        self.dynamic_weights = dynamic_weights

        if profile not in STRATEGY_PROFILES:
            raise ValueError(f"未知策略: {profile}，可选: {list(STRATEGY_PROFILES.keys())}")
        self.prof = STRATEGY_PROFILES[profile]

        # ML predictor
        self._predictor = None
        if mode in ("ml", "hybrid"):
            self._predictor = MLPredictor(model_dir=model_dir)

    # ── Panel 模式（回测用）──────────────────────────────────

    def score_panel(
        self,
        factors_panel: dict,
        close_panel: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        对因子面板做评分（回测用）。

        Parameters
        ----------
        factors_panel : dict
            {factor_name: DataFrame (dates × stocks)}
        close_panel : DataFrame
            收盘价面板 (dates × stocks)

        Returns
        -------
        score : DataFrame (dates × stocks)
        """
        if self.mode == "factor":
            return self._score_panel_factor(factors_panel)
        elif self.mode == "ml":
            raise NotImplementedError(
                "ML panel 模式用 ml_rolling_train.py 的 run_ml_pipeline()，"
                "本引擎仅支持 panel 模式的纯因子评分"
            )
        elif self.mode == "hybrid":
            # hybrid panel：先算 ML panel，再混合
            # 注意：这里需要 Walk-Forward 训练，不在此实现
            raise NotImplementedError(
                "Hybrid panel 模式用 ml_rolling_train.py --hybrid-alpha，"
                "本引擎仅支持 panel 模式的纯因子评分"
            )
        else:
            raise ValueError(f"未知模式: {self.mode}")

    def _score_panel_factor(self, factors_panel: dict) -> pd.DataFrame:
        """纯因子加权评分（面板模式）"""
        weights = self.prof.factor_weights
        if weights:
            filtered = {k: v for k, v in factors_panel.items() if k in weights}
            return composite_score(filtered, weights)
        return composite_score(factors_panel)

    # ── Single-Stock 模式（模拟盘用）──────────────────────────

    def score_single(
        self,
        all_factors: Dict[str, Dict[str, float]],
    ) -> Dict[str, float]:
        """
        对当日截面做评分（模拟盘用）。

        Parameters
        ----------
        all_factors : dict
            {code: {factor_name: float}}

        Returns
        -------
        scores : dict
            {code: score_float}，已按分数降序
        """
        if self.mode == "factor":
            return self._score_single_factor(all_factors)
        elif self.mode == "ml":
            return self._score_single_ml(all_factors)
        elif self.mode == "hybrid":
            return self._score_single_hybrid(all_factors)
        else:
            raise ValueError(f"未知模式: {self.mode}")

    def _score_single_factor(
        self, all_factors: Dict[str, Dict[str, float]]
    ) -> Dict[str, float]:
        """纯因子加权（单股模式，与 score_all_stocks 对齐）"""
        weights = dict(self.prof.factor_weights) if self.prof.factor_weights else {}
        return score_all_stocks(all_factors, weights=weights, dynamic_weights=self.dynamic_weights)

    def _score_single_ml(
        self, all_factors: Dict[str, Dict[str, float]]
    ) -> Dict[str, float]:
        """纯 ML 推理"""
        return self._predictor.predict(all_factors)

    def _score_single_hybrid(
        self, all_factors: Dict[str, Dict[str, float]]
    ) -> Dict[str, float]:
        """
        Hybrid 评分: α × ML + (1-α) × 因子加权。

        两种评分需要归一化后再混合（因子和 ML 预测值量纲不同）。
        方法：各自截面 z-score 标准化后按 α 加权。
        """
        alpha = self.hybrid_alpha

        # ML 评分
        ml_scores = self._predictor.predict(all_factors)
        if not ml_scores:
            return self._score_single_factor(all_factors)

        # 因子评分
        factor_scores = score_all_stocks(
            all_factors,
            weights=dict(self.prof.factor_weights) if self.prof.factor_weights else {},
            dynamic_weights=self.dynamic_weights,
        )

        # 合并全集
        all_codes = set(ml_scores.keys()) | set(factor_scores.keys())

        # 各自 z-score 标准化
        ml_vals = np.array([ml_scores.get(c, 0.0) for c in all_codes])
        f_vals = np.array([factor_scores.get(c, 0.0) for c in all_codes])

        ml_mean, ml_std = ml_vals.mean(), ml_vals.std() + 1e-10
        f_mean, f_std = f_vals.mean(), f_vals.std() + 1e-10

        ml_z = (ml_vals - ml_mean) / ml_std
        f_z = (f_vals - f_mean) / f_std

        hybrid = alpha * ml_z + (1 - alpha) * f_z

        return dict(zip(all_codes, hybrid))

    # ── 选股过滤（模拟盘用，回测在 run_backtest 里做）──────────

    def filter_stocks(
        self,
        scores: Dict[str, float],
        price_data: "pd.Series",
        portfolio_value: float,
        current_holdings: Dict = None,
        stock_names_map: Dict[str, str] = None,
        get_industry_fn=None,
    ) -> Tuple[List[str], Dict[str, float]]:
        """
        统一选股过滤：评分 → 板块过滤 → 流动性过滤 → 行业分散 → top_n。

        Parameters
        ----------
        scores : dict
            {code: score}，预评分
        price_data : Series
            index=code, value=price
        portfolio_value : float
            当前组合净值（用于计算最小买入门槛）
        current_holdings : dict
            当前持仓（含 shares/cost_price 等）
        stock_names_map : dict
            {code: name}（用于查行业）
        get_industry_fn : callable
            (code, name) -> industry_name

        Returns
        -------
        filtered_codes : list — 过滤后的股票代码（分数降序）
        filtered_scores : dict — {code: score}（仅过滤后的）
        """
        from core.config import config as _cfg

        market_filter = _cfg.market
        max_pos = self.prof.max_position
        top_n = self.prof.top_n
        ind_cap = self.prof.max_industry_weight

        min_price = portfolio_value * max_pos / 100  # 至少能买 100 股
        ind_max_count = int(np.ceil(ind_cap * top_n))

        filtered = []
        industry_counts = {}

        for code, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
            # 板块过滤
            if market_filter.include_prefixes and not any(
                code.startswith(p) for p in market_filter.include_prefixes
            ):
                continue
            if market_filter.exclude_prefixes and any(
                code.startswith(p) for p in market_filter.exclude_prefixes
            ):
                continue
            # 流动性过滤
            p = price_data.get(code, np.nan)
            if pd.isna(p) or p <= 0 or p > min_price:
                continue
            # 行业分散
            if get_industry_fn and stock_names_map:
                ind = get_industry_fn(code, stock_names_map.get(code, ""))
                if industry_counts.get(ind, 0) >= ind_max_count:
                    continue
                industry_counts[ind] = industry_counts.get(ind, 0) + 1

            filtered.append(code)
            if len(filtered) >= top_n:
                break

        filtered_scores = {c: scores[c] for c in filtered if c in scores}
        return filtered, filtered_scores
