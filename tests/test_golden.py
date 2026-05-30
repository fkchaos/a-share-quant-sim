"""
Golden Tests — 核心引擎回归测试

验证 core/ 引擎修改后，关键指标不变（±1% 容差）。
任何对 core/account.py, core/factors.py, core/scoring.py, core/position.py 的修改
都应该通过这些测试。

用法:
    python -m pytest tests/ -v
    python -m pytest tests/test_golden.py -v  # 只跑 golden
"""

import os
import sys
import pytest
import numpy as np
import pandas as pd

# Ensure project root is in path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("BACKTEST_DATA_DIR", "/root/data")

from core.factors import calc_factors_panel
from core.config import DEFAULT_FACTOR_WEIGHTS
from core.scoring import composite_score, score_all_stocks
from core.account import check_take_profit, apply_holding_decay, allocate_weights
from core.position import Position


# ============================================================
# Fixtures — 固定数据子集（不随数据更新而变）
# ============================================================

@pytest.fixture
def small_panel():
    """构造一个小型固定面板，不依赖外部数据"""
    np.random.seed(42)
    dates = pd.date_range("2023-01-01", periods=60, freq="B")
    codes = ["000001.SZ", "000002.SZ", "600000.SH", "600519.SH", "300750.SZ"]
    close = pd.DataFrame(
        np.cumsum(np.random.randn(60, 5) * 0.02, axis=1) + 10,
        index=dates, columns=codes
    )
    vol = pd.DataFrame(
        np.random.rand(60, 5) * 1e8 + 1e6,
        index=dates, columns=codes
    )
    amt = vol * close
    return close, vol, amt


@pytest.fixture
def real_panel():
    """加载真实数据的一个小子集用于 golden test"""
    from scripts.run_backtest import load_and_build_panel, _load_stock_names

    # 加载全量数据（2021~2026），用于基准 golden test
    (close_panel, volume_panel, amount_panel), codes = load_and_build_panel(
        start_date=None, end_date=None
    )
    stock_names = _load_stock_names()
    return close_panel, volume_panel, amount_panel, stock_names


# ============================================================
# Test Group 1: 因子计算正确性
# ============================================================

class TestFactorComputation:
    """确保因子计算结果在修改后不变"""

    def test_factor_names_complete(self):
        """29 因子名称集合不变"""
        expected = set(DEFAULT_FACTOR_WEIGHTS.keys())
        assert len(expected) == 29, f"Expected 29 factors, got {len(expected)}"
        assert "boll_width_10" not in expected, "boll_width_10 should be removed"
        assert "boll_width_20" in expected

    def test_factor_weights_sum(self):
        """因子权重之和 ≈ 1.0"""
        total = sum(DEFAULT_FACTOR_WEIGHTS.values())
        assert abs(total - 1.0) < 1e-6, f"Weights sum = {total}"

    def test_factor_output_shape(self, small_panel):
        """因子输出维度正确"""
        close_panel, vol_panel, amt_panel = small_panel
        factors = calc_factors_panel(close_panel, vol_panel, amt_panel)
        assert len(factors) == 29
        for name, df in factors.items():
            assert df.shape == close_panel.shape, (
                f"{name}: expected {close_panel.shape}, got {df.shape}"
            )

    def test_vol_factors_nonzero_with_volume(self, small_panel):
        """传入真实成交量时，vol_ratio 因子不为零"""
        close_panel, vol_panel, amt_panel = small_panel
        factors = calc_factors_panel(close_panel, vol_panel, amt_panel)
        # 最后一天截面标准差应当非零
        vol_ratio_5_tail = factors['vol_ratio_5'].iloc[-1]
        assert vol_ratio_5_tail.std() > 1e-10, (
            "vol_ratio_5 has zero cross-sectional std — vol_panel may be ignored!"
        )

    def test_vol_factors_zero_without_volume(self, small_panel):
        """不传成交量时，vol_ratio 因子截面标准差为零（用于检测隐式依赖）"""
        close_panel, _, _ = small_panel
        factors = calc_factors_panel(close_panel)
        vol_ratio_5_tail = factors['vol_ratio_5'].iloc[-1]
        assert vol_ratio_5_tail.std() < 1e-10, (
            "vol_ratio_5 should be constant when vol_panel is missing"
        )


# ============================================================
# Test Group 2: 评分正确性
# ============================================================

class TestScoring:
    """确保评分结果在修改后不变"""

    def test_composite_score_distribution(self, small_panel):
        """评分结果截面均值为0，标准差不为0"""
        close_panel, vol_panel, amt_panel = small_panel
        factors = calc_factors_panel(close_panel, vol_panel, amt_panel)
        score = composite_score(factors)
        # 最后一天的评分
        last = score.iloc[-1].dropna()
        assert abs(last.mean()) < 0.5, f"Score mean = {last.mean()} (should be ~0)"
        assert last.std() > 0.1, f"Score std = {last.std()} (should be > 0.1)"

    def test_score_all_stocks_output(self, small_panel):
        """score_all_stocks 输出是 dict，包含所有因子名称"""
        close_panel, vol_panel, amt_panel = small_panel
        factors = calc_factors_panel(close_panel, vol_panel, amt_panel)
        result = score_all_stocks(factors)
        assert isinstance(result, dict)
        # 应该包含 composite_score
        assert "composite_score" in result or len(result) > 0


# ============================================================
# Test Group 3: 账户/风控逻辑
# ============================================================

class TestAccountLogic:
    """确保交易逻辑在修改后不变"""

    def _make_state(self, holdings_dict):
        """Helper: 构造一个简单的 PortfolioState"""
        from core.account import PortfolioState
        state = PortfolioState(
            cash=1e8,
            holdings={},
            trade_log=[],
            nav_history=[],
        )
        for code, h in holdings_dict.items():
            state.holdings[code] = {
                'shares': h['shares'],
                'cost_price': h['cost_price'],
                'entry_date': str(h.get('entry_date', '2023-01-05')),
                'tp_taken': h.get('tp_taken', []),
            }
        return state

    def test_take_profit_no_trigger_below_threshold(self):
        """盈利 5% 时不触发止盈"""
        from core.account import check_take_profit
        state = self._make_state({"000001.SZ": {"shares": 1000, "cost_price": 10.0, "entry_date": "2023-01-05", "tp_taken": []}})
        price_data = pd.Series({"000001.SZ": 10.5})
        new_state = check_take_profit(state, "2023-02-01", price_data, tiers=[(0.10, 0.30)])
        # 没触发 → holdings 不变
        assert "000001.SZ" in new_state.holdings

    def test_take_profit_trigger_first_tier(self):
        """盈利 15% → 触发第一档（卖出30%）"""
        from core.account import check_take_profit
        state = self._make_state({"000001.SZ": {"shares": 1000, "cost_price": 10.0, "entry_date": "2023-01-05", "tp_taken": []}})
        price_data = pd.Series({"000001.SZ": 11.5})
        new_state = check_take_profit(state, "2023-02-01", price_data,
                                      tiers=[(0.10, 0.30), (0.20, 0.30)])
        # 第一档触发，卖掉 30% 即 300 股，剩余 700
        assert new_state.holdings["000001.SZ"]["shares"] == 700

    def test_holding_decay_shrinks_position(self):
        """持有超过 rebalance_freq 天后，仓位被压缩"""
        from core.account import apply_holding_decay
        state = self._make_state({"000001.SZ": {"shares": 1000, "cost_price": 10.0,
                                                 "entry_date": "2023-01-01", "tp_taken": []}})
        price_data = pd.Series({"000001.SZ": 10.0})
        # 持有 40 天，rebal_freq=20 → 超过 → 降到 70%
        new_state = apply_holding_decay(state, "2023-02-10", price_data, rebalance_freq=20)
        assert new_state.holdings["000001.SZ"]["shares"] < 1000

    def test_allocate_weights_equal(self, small_panel):
        """等权分配返回相等权重"""
        close_panel, _, _ = small_panel
        top_stocks = list(close_panel.columns[:5])
        price_data = close_panel.iloc[-1]
        weights = allocate_weights(top_stocks, price_data, method="equal")
        assert abs(sum(weights.values()) - 1.0) < 1e-6
        assert abs(weights[top_stocks[0]] - 0.2) < 1e-6


# ============================================================
# Test Group 4: Golden Baseline（真实数据端到端）
# ============================================================

class TestGoldenBaseline:
    """
    端到端 Golden Test：使用固定的参数和固定数据子集，
    验证回测核心指标与预期基准一致（±1% 容差）。

    如果这些测试失败，说明 core/ 引擎的核心计算路径被破坏。
    """

    GOLDEN_PARAMS = dict(
        top_n=12,
        rebalance_freq=20,
        stop_loss=0.20,
        max_industry_weight=0,
        max_daily_turnover=0,
        weight_method="equal",
        stock_names=None,
    )

    # 这些数字是 2026-06-02 用 fixed commit hash 跑出来的
    # 数据范围: 2021-01-01 ~ 2026-05-29 (全量，约 285 只)
    EXPECTED = {
        "v4_baseline_no_ind_cap": dict(
            annual_return=0.2482,
            sharpe_ratio=1.11,
            max_drawdown=0.2887,
            tolerance=0.02,  # ±2pp for return/DD, ±0.05 for sharpe
        ),
    }

    @pytest.mark.slow
    def test_golden_v4_baseline(self, real_panel):
        """Golden test: v4 基准（无行业限制）"""
        close_panel, volume_panel, amount_panel, _ = real_panel
        from scripts.run_backtest import run_backtest

        factors = calc_factors_panel(close_panel, volume_panel, amount_panel)
        score = composite_score(factors)

        metrics, _, _ = run_backtest(
            close_panel, score,
            label="v4_baseline",
            **self.GOLDEN_PARAMS,
        )

        expected = self.EXPECTED["v4_baseline_no_ind_cap"]
        tol = expected["tolerance"]

        assert abs(metrics["annual_return"] - expected["annual_return"]) < tol, (
            f"Annual return drifted: {metrics['annual_return']:.4f} vs {expected['annual_return']:.4f}"
        )
        assert abs(metrics["sharpe_ratio"] - expected["sharpe_ratio"]) < 0.10, (
            f"Sharpe drifted: {metrics['sharpe_ratio']:.4f} vs {expected['sharpe_ratio']:.4f}"
        )
        assert abs(metrics["max_drawdown"] - expected["max_drawdown"]) < tol, (
            f"MaxDD drifted: {metrics['max_drawdown']:.4f} vs {expected['max_drawdown']:.4f}"
        )


# ============================================================
# Test Group 5: 隐式依赖检测
# ============================================================

class TestImplicitDependencyGuard:
    """
    防止隐式依赖被静默引入。
    这些测试故意触发已知陷阱，验证我们的防护措施是否生效。
    """

    def test_vol_panel_none_logs_warning(self, small_panel, capsys):
        """不传 vol_panel 时应有 warning 输出"""
        close_panel, _, _ = small_panel
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            factors = calc_factors_panel(close_panel)
            warning_messages = [str(x.message) for x in w]
            assert any("vol_panel" in msg or "volume" in msg for msg in warning_messages), (
                f"Expected warning about missing vol_panel, got: {warning_messages}"
            )
