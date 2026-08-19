# -*- coding: utf-8 -*-
"""
core/trading.py — 交易门面层
==============================
统一的交易入口，兼容两种模式：
1. 直接调用模式（现有代码，account_runner.py）
2. Provider模式（新代码，QMT移植时使用）

Python 3.6.8 兼容。
"""

import logging

logger = logging.getLogger(__name__)

# 全局provider实例（可选）
_global_provider = None


def set_provider(provider):
    """设置全局交易Provider（可选）"""
    global _global_provider
    _global_provider = provider


def get_provider():
    """获取当前Provider，None表示使用直接模式"""
    return _global_provider


def buy(state, code, price, date, shares=None, reason='AUTO'):
    """统一买入入口
    
    如果设置了Provider，通过Provider执行；
    否则走core.account直接模式。
    
    返回: 新的state
    """
    if _global_provider is not None:
        result = _global_provider.buy(code, shares, price, date, reason)
        if result is None:
            return state  # 失败，返回原state
        return state

    # 直接模式（现有逻辑）
    from core.account import buy as _direct_buy
    return _direct_buy(state, code, price, date, shares=shares)


def sell(state, code, price, date, reason='SELL'):
    """统一卖出入口
    
    注意: core/account.py的sell()没有shares参数（全仓卖出）。
    
    返回: 新的state
    """
    if _global_provider is not None:
        # Provider模式：获取持仓股数后调用
        positions = _global_provider.get_positions()
        if code in positions:
            shares = positions[code].get('shares', 0)
            result = _global_provider.sell(code, shares, price, date, reason)
            if result is None:
                return state
        return state

    # 直接模式（现有逻辑）
    from core.account import sell as _direct_sell
    return _direct_sell(state, code, price, date, reason)


def portfolio_value(state, prices):
    """统一组合市值计算"""
    if _global_provider is not None:
        return _global_provider.portfolio_value(prices)

    from core.account import portfolio_value as _direct_pv
    return _direct_pv(state, prices)
