"""
conftest.py — 标准用例共享 fixtures

提供各测试模块共用的基准数据和工具函数。
所有 fixture 均为合成数据，不依赖外部文件或网络。
"""
import os
import sys
import pytest
import numpy as np
import pandas as pd
from datetime import date, timedelta

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from core.account import PortfolioState, buy, sell, portfolio_value, partial_sell


# ============================================================
# 账户 Fixtures
# ============================================================

@pytest.fixture
def empty_account():
    """空账户，20万现金"""
    return PortfolioState(cash=200000.0, initial_capital=200000.0)


@pytest.fixture
def sample_account():
    """含 3 只持仓的账户（15万现金 + 持仓）"""
    return PortfolioState(
        cash=50000.0,
        initial_capital=200000.0,
        holdings={
            "600000": {"shares": 1000, "cost_price": 10.0, "entry_date": "2026-05-15", "tp_taken": []},
            "000001": {"shares": 2000, "cost_price": 15.0, "entry_date": "2026-05-15", "tp_taken": []},
            "600519": {"shares": 100, "cost_price": 1800.0, "entry_date": "2026-05-15", "tp_taken": []},
        },
        trade_log=[],
        nav_history=[],
    )


@pytest.fixture
def sample_prices():
    """对应 sample_account 的价格序列"""
    return pd.Series({
        "600000": 10.5,   # +5%
        "000001": 15.0,   # 持平
        "600519": 1800.0, # 持平
        "000002": 25.0,   # 新买入候选
        "600036": 40.0,   # 新买入候选
    })


# ============================================================
# 数据面板 Fixtures
# ============================================================

@pytest.fixture
def price_panel():
    """合成价格面板：60天 × 5只股票"""
    rng = np.random.RandomState(42)
    dates = pd.date_range("2024-01-01", periods=60, freq="B")
    close = pd.DataFrame(index=dates)
    volume = pd.DataFrame(index=dates)
    for i in range(5):
        close[f"STK{i:04d}"] = 100 + rng.randn(60).cumsum() * 2
        close[f"STK{i:04d}"] = close[f"STK{i:04d}"].clip(lower=1.0)
        volume[f"STK{i:04d}"] = 1_000_000 + rng.randint(0, 500_000, 60)
    return {"close": close, "volume": volume}


@pytest.fixture
def long_panel():
    """长周期面板：250天 × 10只"""
    rng = np.random.RandomState(123)
    dates = pd.date_range("2023-01-01", periods=250, freq="B")
    close = pd.DataFrame(index=dates)
    volume = pd.DataFrame(index=dates)
    for i in range(10):
        trend = np.linspace(0, 20 * (rng.random() - 0.3), 250)
        close[f"STK{i:04d}"] = 100 + trend + rng.randn(250) * 2
        close[f"STK{i:04d}"] = close[f"STK{i:04d}"].clip(lower=1.0)
        volume[f"STK{i:04d}"] = 1_000_000 + rng.randint(0, 500_000, 250)
    return {"close": close, "volume": volume}


# ============================================================
# 工具函数
# ============================================================

def make_prices(n_days=60, n_stocks=5, seed=42):
    """生成合成价格面板"""
    rng = np.random.RandomState(seed)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="B")
    close = pd.DataFrame(index=dates)
    volume = pd.DataFrame(index=dates)
    for i in range(n_stocks):
        close[f"STK{i:04d}"] = 100 + rng.randn(n_days).cumsum() * 2
        close[f"STK{i:04d}"] = close[f"STK{i:04d}"].clip(lower=1.0)
        volume[f"STK{i:04d}"] = 1_000_000 + rng.randint(0, 500_000, n_days)
    return close, volume


def make_account(cash=200000.0, holdings=None):
    """快速构造测试账户"""
    return PortfolioState(
        cash=cash,
        initial_capital=200000.0,
        holdings=holdings or {},
        trade_log=[],
        nav_history=[],
    )


def assert_valid_state(account):
    """账户状态合法性检查"""
    assert account.cash >= 0, f"现金为负: {account.cash}"
    for code, h in account.holdings.items():
        assert h["shares"] > 0, f"{code} 持仓股数应为正: {h['shares']}"
        assert h["cost_price"] > 0, f"{code} 成本应为正: {h['cost_price']}"
