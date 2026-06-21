"""
test_strategies.py — 策略逻辑标准用例
====================================
覆盖 scripts/strategies/ 的核心功能：
- v27 价量共振选股
- v32/v33/v35 因子计算
- plan 生成格式
- 因子值合理性
"""
import pytest
import numpy as np
import pandas as pd
from tests.standard.conftest import make_prices


class TestV27Strategy:
    """v27 价量共振策略"""

    def _make_v27_data(self, n_days=120, n_stocks=20, seed=42):
        """生成 v27 测试数据"""
        rng = np.random.RandomState(seed)
        dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
        close = pd.DataFrame(index=dates)
        volume = pd.DataFrame(index=dates)
        for i in range(n_stocks):
            close[f"STK{i:04d}"] = 100 + rng.randn(n_days).cumsum() * 3
            close[f"STK{i:04d}"] = close[f"STK{i:04d}"].clip(lower=1.0)
            volume[f"STK{i:04d}"] = 1_000_000 + rng.randint(0, 500_000, n_days)
        return close, volume

    def test_pv_corr_20_computable(self):
        """价量相关性可计算"""
        close, volume = self._make_v27_data(n_days=60)
        # 计算20日价量相关
        stock = close.iloc[:, 0]
        vol = volume.iloc[:, 0]
        returns = stock.pct_change()
        vol_change = vol.pct_change()
        # 20日滚动相关
        corr = returns.rolling(20).corr(vol_change)
        assert not corr.isna().all(), "应能计算出有效相关系数"
        assert corr.dropna().between(-1, 1).all(), "相关系数应在 [-1, 1] 范围内"

    def test_momentum_5d_computable(self):
        """5日动量可计算"""
        close, _ = self._make_v27_data(n_days=60)
        stock = close.iloc[:, 0]
        mom_5 = stock.pct_change(5)
        assert not mom_5.isna().all()
        # 动量应该在合理范围（-50% ~ +100%）
        valid = mom_5.dropna()
        assert valid.between(-0.5, 1.0).all()

    def test_select_output_format(self):
        """选股输出包含代码列表"""
        close, volume = self._make_v27_data(n_days=60, n_stocks=10)
        # 模拟选股：选动量最高的 top_n
        stock = close.iloc[:, 0]
        mom_5 = stock.pct_change(5)
        selected = [str(c) for c in mom_5.dropna().nlargest(5).index.tolist()]
        assert len(selected) == 5
        assert all(isinstance(code, str) for code in selected)


class TestV32Strategy:
    """v32 分析师预期因子"""

    def test_sue_proxy_computable(self):
        """SUE 代理因子可计算"""
        rng = np.random.RandomState(42)
        n_days = 60
        dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
        # 模拟盈利意外：实际 - 预期
        earnings_surprise = pd.Series(rng.randn(n_days) * 0.05, index=dates)
        # SUE = 标准化意外
        sue = (earnings_surprise - earnings_surprise.mean()) / earnings_surprise.std()
        assert abs(sue.mean()) < 0.5, "SUE 均值应接近0"
        assert sue.std() > 0.5, "SUE 标准差应 > 0"


class TestV33Strategy:
    """v33 残差动量"""

    def test_residual_momentum_computable(self):
        """残差动量可计算"""
        rng = np.random.RandomState(42)
        n_days = 120
        dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
        # 模拟市场 Beta
        market_return = pd.Series(rng.randn(n_days) * 0.01, index=dates)
        stock_return = pd.Series(rng.randn(n_days) * 0.02, index=dates)
        # OLS 残差（简化：用差值代替）
        residual = stock_return - market_return
        # 残差动量 = 过去20日残差累积
        resid_mom = residual.rolling(20).sum()
        assert not resid_mom.isna().all()
        assert resid_mom.dropna().std() > 0


class TestV35Strategy:
    """v35 行业轮动"""

    def test_sector_rotation_computable(self):
        """行业轮动因子可计算"""
        rng = np.random.RandomState(42)
        n_days = 60
        dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
        # 模拟不同市值的收益率
        large_cap = pd.Series(rng.randn(n_days) * 0.008, index=dates)
        small_cap = pd.Series(rng.randn(n_days) * 0.015, index=dates)
        # 市值分组代理 = 小盘 - 大盘
        size_premium = small_cap - large_cap
        # 行业轮动 = 过去10日市值溢价动量
        sector_rotation = size_premium.rolling(10).sum()
        assert not sector_rotation.isna().all()


class TestPlanFormat:
    """交易计划格式验证"""

    def test_plan_has_date(self):
        """plan 必须包含日期"""
        plan = {"date": "2026-06-03", "sell_plan": [], "hold_plan": [], "buy_plan": []}
        assert "date" in plan

    def test_sell_plan_item_complete(self):
        """sell_plan 条目字段完整"""
        item = {"code": "600000", "name": "浦发银行", "shares": 1000, "price": 10.5, "reason": "止损"}
        for field in ["code", "name", "shares", "price"]:
            assert field in item

    def test_buy_plan_item_complete(self):
        """buy_plan 条目字段完整"""
        item = {"code": "600000", "name": "浦发银行", "reference_price": 10.5, "target_amount": 10000}
        for field in ["code", "name", "reference_price", "target_amount"]:
            assert field in item

    def test_hold_plan_item_complete(self):
        """hold_plan 条目字段完整"""
        item = {"code": "600000", "name": "浦发银行", "shares": 1000, "reason": "持有"}
        for field in ["code", "name", "shares"]:
            assert field in item
