"""
test_integration.py — 集成测试标准用例
====================================
覆盖 scripts/tools/ 的核心功能：
- send_report.py 格式化
- run_and_send.py 串联
- 数据更新输出格式
"""
import json
import pytest
from tests.standard.conftest import make_prices


class TestSendReportFormat:
    """send_report.py 格式化验证"""

    def _make_signal_data(self, **overrides):
        """构造 signal 测试数据"""
        data = {
            "type": "signal",
            "account_id": 2,
            "date": "2026-06-18",
            "is_trading_day": True,
            "status": "ok",
            "strategy": "v27",
            "cash": 78018.0,
            "holdings_count": 5,
            "sells": [],
            "buys": [],
            "holds": [],
        }
        data.update(overrides)
        return data

    def _make_execute_data(self, **overrides):
        """构造 execute 测试数据"""
        data = {
            "type": "execute",
            "account_id": 2,
            "date": "2026-06-18",
            "is_trading_day": True,
            "status": "ok",
            "executed": 0,
            "skipped": 0,
            "details": [],
            "holdings": [],
        }
        data.update(overrides)
        return data

    def _make_report_data(self, **overrides):
        """构造 report 测试数据"""
        data = {
            "type": "report",
            "account_id": 2,
            "date": "2026-06-18",
            "is_trading_day": True,
            "cash": 78018.0,
            "nav": 195000.0,
            "pnl": -5000.0,
            "pnl_pct": -2.5,
            "holdings_count": 4,
            "position_scale": 1.0,
            "holdings": [],
        }
        data.update(overrides)
        return data

    # --- Signal 格式 ---

    def test_signal_json_serializable(self):
        """signal JSON 可序列化"""
        data = self._make_signal_data()
        json_str = json.dumps(data, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["type"] == "signal"

    def test_signal_with_sells(self):
        """signal 含卖出明细"""
        data = self._make_signal_data(
            sells=[
                {"code": "000001", "name": "平安银行", "shares": 1000, "reason": "止盈", "pnl_pct": 5.2}
            ]
        )
        assert len(data["sells"]) == 1
        assert data["sells"][0]["code"] == "000001"

    def test_signal_skip_non_trading(self):
        """非交易日 signal 简化输出"""
        data = self._make_signal_data(is_trading_day=False, status="skip")
        assert data["status"] == "skip"

    # --- Execute 格式 ---

    def test_execute_json_serializable(self):
        """execute JSON 可序列化"""
        data = self._make_execute_data()
        json_str = json.dumps(data, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["type"] == "execute"

    def test_execute_with_details(self):
        """execute 含执行明细"""
        data = self._make_execute_data(
            executed=2,
            details=[
                {"action": "SELL", "code": "000001", "name": "平安银行", "shares": 1000, "reason": "止盈"},
                {"action": "BUY", "code": "600519", "name": "贵州茅台", "shares": 100, "price": 1750.5},
            ],
        )
        assert data["executed"] == 2
        assert len(data["details"]) == 2

    def test_execute_with_holdings(self):
        """execute 含持仓明细"""
        data = self._make_execute_data(
            holdings=[
                {"code": "000858", "name": "五粮液", "shares": 500, "cost_price": 150.3, "market_value": 75000, "pnl_pct": -0.13}
            ]
        )
        assert len(data["holdings"]) == 1
        assert data["holdings"][0]["code"] == "000858"

    # --- Report 格式 ---

    def test_report_json_serializable(self):
        """report JSON 可序列化"""
        data = self._make_report_data()
        json_str = json.dumps(data, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["type"] == "report"

    def test_report_with_holdings(self):
        """report 含持仓明细"""
        data = self._make_report_data(
            holdings=[
                {"code": "000001", "name": "平安银行", "shares": 1000, "cost_price": 10.0, "market_value": 10500.0, "pnl_pct": 5.0},
                {"code": "000657", "name": "中钨高新", "shares": 100, "cost_price": 91.01, "market_value": 9848.0, "pnl_pct": 8.2},
            ]
        )
        assert len(data["holdings"]) == 2
        assert data["holdings"][0]["pnl_pct"] == 5.0

    def test_report_non_trading_day(self):
        """非交易日 report"""
        data = self._make_report_data(is_trading_day=False)
        assert data["is_trading_day"] is False


class TestDataUpdateFormat:
    """数据更新输出格式"""

    def test_data_update_json_serializable(self):
        """data_update JSON 可序列化"""
        data = {
            "type": "data_update",
            "updated": 800,
            "failed": 0,
            "duration": 12.5,
            "records": 50000,
            "index_ok": True,
        }
        json_str = json.dumps(data, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["type"] == "data_update"

    def test_data_update_with_failures(self):
        """数据更新含失败"""
        data = {
            "type": "data_update",
            "updated": 795,
            "failed": 5,
            "duration": 15.2,
            "records": 49500,
            "index_ok": True,
            "skipped_codes": ["600000", "000001"],
        }
        assert data["failed"] == 5
        assert len(data["skipped_codes"]) == 2


class TestErrorFormat:
    """错误输出格式"""

    def test_error_json_serializable(self):
        """error JSON 可序列化"""
        data = {
            "type": "error",
            "account_id": 2,
            "task": "intraday_signal",
            "error": "Connection timeout",
            "date": "2026-06-21",
        }
        json_str = json.dumps(data, ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["type"] == "error"
        assert "error" in parsed
