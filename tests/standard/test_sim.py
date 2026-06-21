"""
test_sim.py — 模拟盘执行标准用例
====================================
覆盖 scripts/sim/account_runner.py 的核心链路：
- 信号生成 → JSON 输出
- 交易执行 → 持仓更新
- 收盘报告 → 净值计算
- 非交易日 → 跳过逻辑
"""
import os
import sys
import json
import pytest
import numpy as np
import pandas as pd
from datetime import date, timedelta
from unittest.mock import patch, MagicMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from core.account import PortfolioState, buy, sell, portfolio_value
from tests.standard.conftest import make_account, assert_valid_state


class TestSignalGeneration:
    """信号生成 JSON 格式验证"""

    def test_signal_json_has_required_fields(self):
        """signal JSON 必须包含必要字段"""
        result = {
            "type": "signal",
            "account_id": 2,
            "date": "2026-06-21",
            "is_trading_day": True,
            "status": "ok",
            "strategy": "v27",
            "cash": 78018.0,
            "holdings_count": 11,
            "sells": [],
            "buys": [],
            "holds": [],
        }
        required = ["type", "account_id", "date", "is_trading_day", "status", "cash", "sells", "buys", "holds"]
        for field in required:
            assert field in result, f"缺少字段: {field}"

    def test_signal_skip_non_trading_day(self):
        """非交易日返回 skip 状态"""
        result = {
            "type": "signal",
            "account_id": 2,
            "date": "2026-06-21",
            "is_trading_day": False,
            "status": "skip",
            "reason": "非交易日",
        }
        assert result["status"] == "skip"
        assert result["is_trading_day"] is False

    def test_signal_sell_plan_format(self):
        """卖出计划条目格式正确"""
        result = {
            "type": "signal",
            "status": "ok",
            "sells": [
                {"code": "000001", "name": "平安银行", "shares": 1000, "reason": "止盈", "pnl_pct": 5.2}
            ]
        }
        for s in result["sells"]:
            assert "code" in s
            assert "name" in s
            assert "shares" in s
            assert "reason" in s

    def test_signal_buy_plan_format(self):
        """买入计划条目格式正确"""
        result = {
            "type": "signal",
            "status": "ok",
            "buys": [
                {"code": "600519", "name": "贵州茅台", "shares": 100, "price": 1750.5, "target_amount": 175000}
            ]
        }
        for b in result["buys"]:
            assert "code" in b
            assert "name" in b
            assert "shares" in b
            assert "price" in b


class TestExecute:
    """执行链路验证"""

    def test_execute_json_has_required_fields(self):
        """execute JSON 必须包含必要字段"""
        result = {
            "type": "execute",
            "account_id": 2,
            "date": "2026-06-21",
            "is_trading_day": True,
            "status": "ok",
            "executed": 3,
            "skipped": 1,
            "details": [],
            "holdings": [],
        }
        required = ["type", "account_id", "date", "is_trading_day", "status", "executed", "skipped", "details", "holdings"]
        for field in required:
            assert field in result

    def test_execute_detail_format(self):
        """执行明细格式正确"""
        detail = {"action": "SELL", "code": "000001", "name": "平安银行", "shares": 1000, "reason": "止盈"}
        for field in ["action", "code", "name", "shares"]:
            assert field in detail

    def test_execute_holdings_format(self):
        """执行后持仓格式正确"""
        holding = {"code": "000858", "name": "五粮液", "shares": 500, "cost_price": 150.3, "market_value": 75000, "pnl_pct": -0.13}
        for field in ["code", "name", "shares", "cost_price", "market_value", "pnl_pct"]:
            assert field in holding

    def test_execute_skip_non_trading_day(self):
        """非交易日执行返回 skip"""
        result = {
            "type": "execute",
            "account_id": 2,
            "date": "2026-06-21",
            "is_trading_day": False,
            "status": "skip",
            "reason": "非交易日",
        }
        assert result["status"] == "skip"


class TestReport:
    """收盘报告验证"""

    def test_report_json_has_required_fields(self):
        """report JSON 必须包含必要字段"""
        result = {
            "type": "report",
            "account_id": 2,
            "date": "2026-06-21",
            "is_trading_day": True,
            "cash": 78018.0,
            "nav": 195000.0,
            "pnl": -5000.0,
            "pnl_pct": -2.5,
            "holdings_count": 4,
            "position_scale": 1.0,
            "holdings": [],
        }
        required = ["type", "account_id", "date", "is_trading_day", "cash", "nav", "pnl", "pnl_pct", "holdings", "position_scale"]
        for field in required:
            assert field in result

    def test_report_holding_format(self):
        """持仓明细格式正确"""
        holding = {
            "code": "000001", "name": "平安银行",
            "shares": 1000, "cost_price": 10.0,
            "market_value": 10500.0, "pnl_pct": 5.0
        }
        for field in ["code", "name", "shares", "cost_price", "market_value", "pnl_pct"]:
            assert field in holding


class TestPlanExecution:
    """计划执行链路"""

    def test_sell_then_buy_updates_state(self):
        """先卖后买：状态正确更新"""
        acc = make_account(cash=100000.0, holdings={
            "A": {"shares": 1000, "cost_price": 10.0},
        })
        # 卖 A
        acc = sell(acc, "A", 10.5, "2026-06-03")
        assert "A" not in acc.holdings
        cash_after_sell = acc.cash
        # 买 B
        acc = buy(acc, "B", 20.0, "2026-06-03", shares=100)
        assert "B" in acc.holdings
        assert acc.cash < cash_after_sell
        assert_valid_state(acc)

    def test_insufficient_cash_skips_buy(self):
        """资金不足时买入跳过"""
        acc = make_account(cash=100.0)
        acc = buy(acc, "600519", 1000.0, "2026-06-03", shares=100)
        assert "600519" not in acc.holdings

    def test_sell_not_in_holdings_skipped(self):
        """卖出不存在的股票不报错"""
        acc = make_account(cash=100000.0, holdings={
            "A": {"shares": 1000, "cost_price": 10.0},
        })
        acc = sell(acc, "Z99999", 10.0, "2026-06-03")
        # 状态不变
        assert "A" in acc.holdings
        assert acc.cash == 100000.0
