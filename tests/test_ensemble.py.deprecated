"""
v11b Ensemble 评分测试 — 验证多组选股逻辑正确性

覆盖场景：
1. ensemble_union_score (panel 模式) 输出正确
2. ensemble_union_score_single (单股模式) 输出正确
3. StrategyEngine ensemble 模式端到端
4. 模拟盘 score_single 返回正确格式
5. 并集逻辑：被多组选中的股票得分更高
"""

import os, sys, pytest
import numpy as np
import pandas as pd

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
os.environ.setdefault("BACKTEST_DATA_DIR", os.path.join(PROJECT_ROOT, "data"))

from core.scoring import (
    ensemble_union_score,
    ensemble_union_score_single,
    composite_score,
)
from core.strategy import StrategyEngine
from core.config import STRATEGY_PROFILES


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def sample_factors_panel():
    """构造 5 天 × 10 只股票的因子面板"""
    dates = pd.date_range("2026-01-01", periods=5, freq="B")
    stocks = [f"600{i:03d}" for i in range(1, 11)]
    np.random.seed(42)

    factors = {}
    for fname in ["mom_20", "mom_10", "rsi_14", "high_low_range",
                  "vol_60", "vol_20", "vol_10", "boll_width_20",
                  "rev_10", "rev_5", "rsi_6", "boll_pos_10"]:
        factors[fname] = pd.DataFrame(
            np.random.randn(5, 10), index=dates, columns=stocks
        )
    return factors


@pytest.fixture
def sample_factors_single():
    """构造单股模式因子数据：{code: {factor: value}}"""
    np.random.seed(42)
    stocks = [f"600{i:03d}" for i in range(1, 11)]
    factor_names = ["mom_20", "mom_10", "rsi_14", "high_low_range",
                    "vol_60", "vol_20", "vol_10", "boll_width_20",
                    "rev_10", "rev_5", "rsi_6", "boll_pos_10"]

    all_factors = {}
    for code in stocks:
        all_factors[code] = {f: np.random.randn() for f in factor_names}
    return all_factors


@pytest.fixture
def ensemble_groups():
    """v11b 的 3 个因子组"""
    return {
        "momentum": {"mom_20": 0.30, "mom_10": 0.25, "rsi_14": 0.25, "high_low_range": 0.20},
        "volatility": {"vol_60": 0.30, "vol_20": 0.25, "vol_10": 0.25, "boll_width_20": 0.20},
        "reversal": {"rev_10": 0.30, "rev_5": 0.25, "rsi_6": 0.25, "boll_pos_10": 0.20},
    }


# ============================================================
# Panel 模式测试
# ============================================================

class TestEnsembleUnionScore:
    def test_output_shape(self, sample_factors_panel, ensemble_groups):
        """输出 shape 与输入一致"""
        result = ensemble_union_score(sample_factors_panel, ensemble_groups, group_top_n=3)
        assert result.shape == (5, 10)
        assert list(result.columns) == list(sample_factors_panel["mom_20"].columns)

    def test_score_range(self, sample_factors_panel, ensemble_groups):
        """score 范围 [0, 3]（3 个组，每组最多选一次）"""
        result = ensemble_union_score(sample_factors_panel, ensemble_groups, group_top_n=3)
        assert result.min().min() >= 0.0
        assert result.max().max() <= 3.0

    def test_multi_group_selection(self, sample_factors_panel, ensemble_groups):
        """被多组选中的股票得分更高"""
        result = ensemble_union_score(sample_factors_panel, ensemble_groups, group_top_n=3)
        # 至少有一些股票被多组选中（概率上几乎必然）
        daily_max = result.max(axis=1)
        assert (daily_max > 1.0).any(), "应该有股票被多组选中"

    def test_empty_groups(self, sample_factors_panel):
        """空 ensemble_groups 返回全 0"""
        result = ensemble_union_score(sample_factors_panel, {}, group_top_n=3)
        assert (result == 0.0).all().all()

    def test_group_top_n_effect(self, sample_factors_panel, ensemble_groups):
        """group_top_n 越大，选中股票越多"""
        r3 = ensemble_union_score(sample_factors_panel, ensemble_groups, group_top_n=3)
        r5 = ensemble_union_score(sample_factors_panel, ensemble_groups, group_top_n=5)
        # top5 选出的股票数 >= top3
        n3 = (r3 > 0).sum().sum()
        n5 = (r5 > 0).sum().sum()
        assert n5 >= n3


# ============================================================
# Single-Stock 模式测试
# ============================================================

class TestEnsembleUnionScoreSingle:
    def test_output_format(self, sample_factors_single, ensemble_groups):
        """输出是 dict，key 为股票代码"""
        result = ensemble_union_score_single(sample_factors_single, ensemble_groups, group_top_n=3)
        assert isinstance(result, dict)
        assert set(result.keys()) == set(sample_factors_single.keys())

    def test_score_range(self, sample_factors_single, ensemble_groups):
        """score 范围 [0, 3]"""
        result = ensemble_union_score_single(sample_factors_single, ensemble_groups, group_top_n=3)
        vals = list(result.values())
        assert min(vals) >= 0.0
        assert max(vals) <= 3.0

    def test_some_selected(self, sample_factors_single, ensemble_groups):
        """至少有一些股票被选中"""
        result = ensemble_union_score_single(sample_factors_single, ensemble_groups, group_top_n=3)
        selected = sum(1 for v in result.values() if v > 0)
        assert selected > 0, "应该有股票被选中"

    def test_empty_groups(self, sample_factors_single):
        """空 groups 返回全 0"""
        result = ensemble_union_score_single(sample_factors_single, {}, group_top_n=3)
        assert all(v == 0.0 for v in result.values())


# ============================================================
# StrategyEngine 集成测试
# ============================================================

class TestStrategyEngineEnsemble:
    def test_ensemble_mode_init(self):
        """StrategyEngine 能初始化 ensemble 模式"""
        engine = StrategyEngine(profile="v11b_zz800_union", mode="ensemble")
        assert engine.mode == "ensemble"
        assert engine.prof.ensemble_groups is not None

    def test_ensemble_score_panel(self, sample_factors_panel):
        """score_panel 返回 DataFrame"""
        engine = StrategyEngine(profile="v11b_zz800_union", mode="ensemble")
        result = engine.score_panel(sample_factors_panel)
        assert isinstance(result, pd.DataFrame)
        assert result.shape == (5, 10)

    def test_ensemble_score_single(self, sample_factors_single):
        """score_single 返回 dict"""
        engine = StrategyEngine(profile="v11b_zz800_union", mode="ensemble")
        result = engine.score_single(sample_factors_single)
        assert isinstance(result, dict)
        assert len(result) == len(sample_factors_single)

    def test_factor_mode_still_works(self, sample_factors_panel):
        """factor 模式不受影响"""
        engine = StrategyEngine(profile="v6b_8f_pos_ic", mode="factor")
        result = engine.score_panel(sample_factors_panel)
        assert isinstance(result, pd.DataFrame)

    def test_unknown_profile_raises(self):
        """未知 profile 抛异常"""
        with pytest.raises(ValueError, match="未知策略"):
            StrategyEngine(profile="nonexistent", mode="ensemble")

    def test_ensemble_without_groups_raises(self):
        """factor profile 用 ensemble 模式抛异常"""
        with pytest.raises(ValueError, match="ensemble_groups 未配置"):
            engine = StrategyEngine(profile="v6b_8f_pos_ic", mode="ensemble")
            engine.score_panel({"mom_20": pd.DataFrame()})


# ============================================================
# v11b Profile 配置测试
# ============================================================

class TestV11bProfile:
    def test_profile_registered(self):
        """v11b_zz800_union 已注册"""
        assert "v11b_zz800_union" in STRATEGY_PROFILES

    def test_ensemble_groups_configured(self):
        """ensemble_groups 有 3 个组"""
        prof = STRATEGY_PROFILES["v11b_zz800_union"]
        assert prof.ensemble_groups is not None
        assert len(prof.ensemble_groups) == 3
        assert set(prof.ensemble_groups.keys()) == {"momentum", "volatility", "reversal"}

    def test_group_top_n(self):
        """group_top_n = 5"""
        prof = STRATEGY_PROFILES["v11b_zz800_union"]
        assert prof.ensemble_group_top_n == 5

    def test_factor_weights_none(self):
        """v11b 不用 factor_weights"""
        prof = STRATEGY_PROFILES["v11b_zz800_union"]
        assert prof.factor_weights is None
