# -*- coding: utf-8 -*-
"""
core/trading_provider.py — 交易Provider基类
============================================
所有交易操作的统一接口。策略代码只认这个接口，
不关心底层是模拟盘、QMT还是其他平台。

Python 3.6.8 兼容。
"""


class TradingProvider:
    """交易Provider基类 — 所有交易操作的统一接口"""

    def initialize(self, config):
        """初始化Provider
        config: dict，包含账号ID、数据库路径等
        """
        raise NotImplementedError

    def buy(self, code, shares, price, date, reason='AUTO'):
        """买入
        code: 股票代码 '600000'
        shares: 股数 (int)
        price: 成交价 (float)
        date: 日期
        reason: 买入原因
        返回: dict (交易记录) 或 None (失败)
        """
        raise NotImplementedError

    def sell(self, code, shares, price, date, reason='AUTO'):
        """卖出
        返回: dict (交易记录) 或 None (失败)
        """
        raise NotImplementedError

    def get_positions(self):
        """获取当前持仓
        返回: {'code': {'shares': int, 'cost_price': float, ...}, ...}
        """
        raise NotImplementedError

    def get_balance(self):
        """获取资金余额
        返回: {'cash': float, 'total_value': float}
        """
        raise NotImplementedError

    def get_trade_log(self):
        """获取交易记录
        返回: [{'date': str, 'code': str, 'action': str, ...}, ...]
        """
        raise NotImplementedError

    def portfolio_value(self, prices):
        """计算组合总市值
        prices: {code: price} 当前价格字典
        返回: float
        """
        raise NotImplementedError
