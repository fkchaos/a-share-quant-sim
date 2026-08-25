#coding:gbk
"""
v61c_strategy.py - V61C Strategy Logic

Low turnover + small cap factors.
"""
import pandas as pd


def init(C):
    """Init strategy."""
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
    _rebalance_days = REBALANCE_CONFIG.get('rebalance_days', 5)


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
    max_pos = RISK_CONFIG.get('max_position', 0.25)
    max_holdings = RISK_CONFIG.get('max_holdings', 5)
    target = {}
    for code in selected[:max_holdings]:
        target[code] = max_pos / len(selected[:max_holdings])

    qmt_runner.execute_buy(C, _account, target)
