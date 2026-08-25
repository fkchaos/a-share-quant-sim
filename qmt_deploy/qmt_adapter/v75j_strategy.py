#coding:gbk
"""
v75j_strategy.py - V75J Strategy Logic

Tech trend + liquidity factor + breadth filter.
"""
import pandas as pd


def init(C):
    """Init strategy."""
    # Force reload all qmt_adapter modules to clear stale .pyc cache
    import importlib
    import sys
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith('qmt_adapter.'):
            del sys.modules[mod_name]

    from .qmt_data import ZZ1800_STOCKS
    from .trading import QmtAccount
    from .config import RISK_CONFIG, REBALANCE_CONFIG
    from . import qmt_runner

    qmt_runner.qmt_init(C)

    global _stock_pool, _stock_list, _account
    _stock_pool = ZZ1800_STOCKS
    _stock_list = _stock_pool
    _account = QmtAccount(C)

    global _hold_days, _last_rebalance_date, _today_buys, _last_trade_date, _rebalance_days
    _hold_days = {}
    _last_rebalance_date = None
    _today_buys = 0
    _last_trade_date = None
    _rebalance_days = REBALANCE_CONFIG.get('rebalance_days', 10)

    # V75J-specific
    global _breadth_count, _breadth_total
    _breadth_count = 0
    _breadth_total = 0


def handlebar(C):
    """Main strategy logic."""
    from datetime import datetime
    from . import qmt_runner
    from .config import RISK_CONFIG

    today = datetime.now().strftime('%Y-%m-%d')
    if _last_trade_date == today:
        return
    _last_trade_date = today

    # Update hold days
    for code in _hold_days:
        _hold_days[code] = _hold_days.get(code, 0) + 1

    # Risk check
    qmt_runner.check_risk(C, _account, _hold_days)

    # Check rebalance
    is_rebal = qmt_runner.is_rebalance_day(C, _rebalance_days)

    if not is_rebal:
        return

    # Stock selection
    selected = _select_stocks(C)

    if not selected:
        return

    # Target weight
    max_pos = RISK_CONFIG.get('max_position', 0.35)
    max_holdings = RISK_CONFIG.get('max_holdings', 3)
    target = {}
    for code in selected[:max_holdings]:
        target[code] = max_pos / len(selected[:max_holdings])

    qmt_runner.execute_buy(C, _account, target)
