"""
test_account.py — 账户操作标准用例
====================================
覆盖 core.account 的核心功能：
- 买入/卖出/部分卖出
- 净值计算
- 止损/止盈
- 交易日志
- 状态不变性
"""
import pytest
import copy
import pandas as pd
from core.account import buy, sell, portfolio_value, partial_sell
from tests.standard.conftest import make_account, make_prices, assert_valid_state


class TestBuy:
    """买入操作"""

    def test_buy_creates_holding(self, empty_account):
        """买入后持仓增加"""
        acc = buy(empty_account, "600000", 10.0, "2026-06-03", shares=1000)
        assert "600000" in acc.holdings
        assert acc.holdings["600000"]["shares"] == 1000

    def test_buy_decreases_cash(self, empty_account):
        """买入后现金减少"""
        acc = buy(empty_account, "600000", 10.0, "2026-06-03", shares=1000)
        assert acc.cash < 200000.0

    def test_buy_zero_shares_skipped(self, empty_account):
        """0股不执行买入"""
        acc = buy(empty_account, "600000", 10.0, "2026-06-03", shares=0)
        assert "600000" not in acc.holdings

    def test_buy_min_lot_100(self, empty_account):
        """不足100股时取整为0，跳过"""
        # 100股 × 3000元 = 30万 > 20万
        acc = buy(empty_account, "600519", 3000.0, "2026-06-03", shares=66)
        assert "600519" not in acc.holdings

    def test_buy_cash_never_negative(self, empty_account):
        """现金不会为负"""
        acc = buy(empty_account, "600000", 100.0, "2026-06-03", shares=100000)
        assert acc.cash >= 0

    def test_buy_records_trade_log(self, empty_account):
        """买入记录到 trade_log"""
        acc = buy(empty_account, "600000", 10.0, "2026-06-03", shares=1000)
        assert len(acc.trade_log) == 1
        assert acc.trade_log[0]["action"] == "BUY"
        assert acc.trade_log[0]["code"] == "600000"


class TestSell:
    """卖出操作"""

    def test_sell_removes_holding(self, sample_account):
        """全部卖出后持仓移除"""
        acc = sell(sample_account, "600000", 10.5, "2026-06-03")
        assert "600000" not in acc.holdings

    def test_sell_increases_cash(self, sample_account):
        """卖出后现金增加"""
        acc = sell(sample_account, "600000", 10.5, "2026-06-03")
        assert acc.cash > 50000.0

    def test_sell_not_in_holdings_no_error(self, sample_account):
        """卖出不存在的股票不报错"""
        acc = sell(sample_account, "999999", 10.0, "2026-06-03")
        assert "999999" not in acc.holdings

    def test_partial_sell_reduces_shares(self, sample_account):
        """部分卖出减少持股"""
        old_shares = sample_account.holdings["600000"]["shares"]
        acc = partial_sell(sample_account, "600000", 10.5, "2026-06-03", sell_fraction=0.3)
        assert acc.holdings["600000"]["shares"] == int(old_shares * 0.7)

    def test_sell_records_trade_log(self, sample_account):
        """卖出记录到 trade_log"""
        acc = sell(sample_account, "600000", 10.5, "2026-06-03")
        assert len(acc.trade_log) == 1
        assert acc.trade_log[0]["action"] == "SELL"


class TestPortfolioValue:
    """净值计算"""

    def test_pv_equals_cash_plus_market_value(self, sample_account, sample_prices):
        """净值 = 现金 + 持仓市值"""
        from core.account import portfolio_value
        pv = portfolio_value(sample_account, "2026-06-03", sample_prices)
        expected = 50000.0 + 1000 * 10.5 + 2000 * 15.0 + 100 * 1800.0
        assert abs(pv - expected) < 1.0

    def test_pv_deterministic(self, sample_account, sample_prices):
        """相同输入相同输出"""
        from core.account import portfolio_value
        pv1 = portfolio_value(sample_account, "2026-06-03", sample_prices)
        pv2 = portfolio_value(sample_account, "2026-06-03", sample_prices)
        assert pv1 == pv2

    def test_pv_after_buy_conservation(self, sample_account, sample_prices):
        """买入前后净值近似不变（仅手续费差异）"""
        before = portfolio_value(sample_account, "2026-06-03", sample_prices)
        acc = buy(sample_account, "000002", 25.0, "2026-06-03", shares=100)
        after = portfolio_value(acc, "2026-06-03", sample_prices)
        assert abs(after - before) < 100

    def test_pv_after_sell_conservation(self, sample_account, sample_prices):
        """卖出前后净值近似不变"""
        before = portfolio_value(sample_account, "2026-06-03", sample_prices)
        acc = sell(sample_account, "600000", 10.5, "2026-06-03")
        after = portfolio_value(acc, "2026-06-03", sample_prices)
        assert abs(after - before) < 100


class TestStopLoss:
    """止损逻辑"""

    def test_stop_loss_sells_at_price(self):
        """止损卖出按指定价格"""
        acc = make_account(holdings={"RISKY": {"shares": 1000, "cost_price": 10.0}})
        acc = sell(acc, "RISKY", 7.5, "2026-06-03", reason="STOP_LOSS")
        assert "RISKY" not in acc.holdings

    def test_stop_loss_records_action(self):
        """止损卖出记录正确"""
        acc = make_account(holdings={"RISKY": {"shares": 1000, "cost_price": 10.0}})
        acc = sell(acc, "RISKY", 7.5, "2026-06-03")
        assert acc.trade_log[0]["action"] == "SELL"
        assert acc.trade_log[0]["code"] == "RISKY"


class TestStateIntegrity:
    """状态完整性"""

    def test_no_operation_preserves_state(self, sample_account):
        """不执行操作时状态不变"""
        original = copy.deepcopy(sample_account)
        assert sample_account.holdings == original.holdings
        assert sample_account.cash == original.cash

    def test_buy_sell_roundtrip(self, empty_account):
        """买卖往返：持仓为空，现金 ≈ 初始 - 手续费"""
        prices = pd.Series({"600000": 10.0})
        acc = buy(empty_account, "600000", 10.0, "2026-06-03", shares=1000)
        acc = sell(acc, "600000", 10.0, "2026-06-04")
        assert "600000" not in acc.holdings
        assert acc.cash < 200000.0
        assert acc.cash > 199000.0  # 手续费不多

    def test_account_state_always_valid(self, sample_account):
        """账户状态始终合法"""
        assert_valid_state(sample_account)
        acc = sell(sample_account, "600000", 10.5, "2026-06-03")
        assert_valid_state(acc)
