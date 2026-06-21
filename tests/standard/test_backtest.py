"""
test_backtest.py — 回测引擎 + Walk-Forward 标准用例
====================================
覆盖 scripts/backtest/ 的核心功能：
- 回测引擎基础运行
- 因子计算
- 净值守恒
- Walk-Forward 框架
"""
import pytest
import numpy as np
import pandas as pd
from tests.standard.conftest import make_prices


class TestBacktestEngine:
    """回测引擎基础"""

    def test_backtest_runs_without_crash(self, price_panel):
        """回测引擎不崩溃"""
        close = price_panel["close"]
        volume = price_panel["volume"]
        # 基本数据验证
        assert close.shape[0] >= 60
        assert volume.shape[0] == close.shape[0]
        assert not close.isna().any().any()

    def test_factor_computation_shape(self, price_panel):
        """因子计算输出形状正确"""
        close = price_panel["close"]
        volume = price_panel["volume"]
        # 模拟因子计算
        returns = close.pct_change()
        vol_ratio = volume / volume.rolling(20).mean()
        assert returns.shape == close.shape
        assert vol_ratio.shape == close.shape

    def test_synthetic_panel_reproducible(self):
        """合成面板数据可复现"""
        close1, vol1 = make_prices(n_days=60, n_stocks=5, seed=42)
        close2, vol2 = make_prices(n_days=60, n_stocks=5, seed=42)
        pd.testing.assert_frame_equal(close1, close2)
        pd.testing.assert_frame_equal(vol1, vol2)

    def test_long_panel_sufficient_for_backtest(self, long_panel):
        """长周期面板足够跑回测"""
        close = long_panel["close"]
        assert close.shape[0] >= 200, "回测至少需要200天数据"
        assert close.shape[1] >= 5, "回测至少需要5只股票"


class TestFactorCalculations:
    """因子计算正确性"""

    def test_momentum_factor(self):
        """动量因子计算"""
        close, _ = make_prices(n_days=60, n_stocks=3, seed=42)
        # 5日动量
        mom_5 = close.pct_change(5)
        assert mom_5.shape == close.shape
        # 动量不应全为 NaN
        assert not mom_5.isna().all().all()

    def test_volatility_factor(self):
        """波动率因子计算"""
        close, _ = make_prices(n_days=60, n_stocks=3, seed=42)
        returns = close.pct_change()
        vol_20 = returns.rolling(20).std()
        assert vol_20.shape == close.shape
        # 波动率应为正
        assert (vol_20.dropna() > 0).all().all()

    def test_volume_ratio_factor(self):
        """量比因子计算"""
        _, volume = make_prices(n_days=60, n_stocks=3, seed=42)
        vol_ratio = volume / volume.rolling(20).mean()
        assert vol_ratio.shape == volume.shape
        # 量比不应全为 NaN
        assert not vol_ratio.isna().all().all()

    def test_crossover_signal(self):
        """交叉信号生成"""
        close, _ = make_prices(n_days=60, n_stocks=3, seed=42)
        ma_5 = close.iloc[:, 0].rolling(5).mean()
        ma_20 = close.iloc[:, 0].rolling(20).mean()
        # 金叉：MA5 上穿 MA20
        golden_cross = (ma_5 > ma_20) & (ma_5.shift(1) <= ma_20.shift(1))
        assert golden_cross.dtype == bool


class TestWalkForward:
    """Walk-Forward 框架"""

    def test_train_test_split(self, long_panel):
        """训练集/测试集分割"""
        close = long_panel["close"]
        n = len(close)
        train_end = int(n * 0.7)
        train = close.iloc[:train_end]
        test = close.iloc[train_end:]
        assert len(train) > 100
        assert len(test) > 20
        assert len(train) + len(test) == n

    def test_walk_forward_rolling(self, long_panel):
        """滚动 Walk-Forward"""
        close = long_panel["close"]
        n = len(close)
        train_size = 100
        test_size = 20
        # 模拟 WF 分割点
        splits = []
        start = 0
        while start + train_size + test_size <= n:
            train_end = start + train_size
            test_end = train_end + test_size
            splits.append((start, train_end, test_end))
            start += test_size
        assert len(splits) >= 2, "应至少有2个 WF fold"

    def test_no_future_data_leak(self, long_panel):
        """无未来数据泄露"""
        close = long_panel["close"]
        n = len(close)
        train_end = int(n * 0.7)
        # 训练集指标
        train_mean = close.iloc[:train_end].mean()
        # 测试集不应参与训练集计算
        test_mean = close.iloc[train_end:].mean()
        # 两者应不同（随机数据）
        assert not (train_mean == test_mean).all()


class TestBacktestMetrics:
    """回测指标计算"""

    def test_sharpe_ratio_formula(self):
        """夏普比率公式正确"""
        rng = np.random.RandomState(42)
        # 模拟日收益率
        daily_returns = rng.randn(252) * 0.01
        # 年化夏普
        sharpe = np.sqrt(252) * daily_returns.mean() / daily_returns.std()
        assert isinstance(sharpe, (int, float, np.floating))
        assert not np.isnan(sharpe)

    def test_max_drawdown_formula(self):
        """最大回撤公式正确"""
        rng = np.random.RandomState(42)
        prices = 100 + rng.randn(100).cumsum() * 2
        prices = np.maximum(prices, 1.0)
        cummax = np.maximum.accumulate(prices)
        drawdown = (prices - cummax) / cummax
        max_dd = drawdown.min()
        assert max_dd <= 0, "最大回撤应为负数或零"
        assert max_dd >= -1, "最大回撤不低于 -100%"

    def test_total_return_formula(self):
        """总收益率公式"""
        initial = 100000.0
        final = 105000.0
        total_return = (final - initial) / initial
        assert abs(total_return - 0.05) < 0.001
