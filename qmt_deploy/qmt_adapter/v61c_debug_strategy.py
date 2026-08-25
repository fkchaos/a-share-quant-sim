#coding:gbk
"""
v61c_debug_strategy.py - V61C Debug Version

Adds verbose output for debugging.
"""
import sys
import pandas as pd
from datetime import datetime

# Module-level globals
_stock_pool = None
_stock_list = None
_account = None
_hold_days = {}
_last_rebalance_date = None
_today_buys = 0
_last_trade_date = None
_rebalance_days = 5


def init(C):
    """Init with debug output."""
    global _stock_pool, _stock_list, _account
    global _hold_days, _last_rebalance_date, _today_buys, _last_trade_date, _rebalance_days

    from .qmt_data import ZZ1800_STOCKS
    from .trading import QmtAccount
    from .config import RISK_CONFIG, REBALANCE_CONFIG
    from . import qmt_runner

    qmt_runner.qmt_init(C)

    _stock_pool = ZZ1800_STOCKS
    _stock_list = _stock_pool
    _account = QmtAccount(C)
    _rebalance_days = REBALANCE_CONFIG.get('rebalance_days', 5)

    # Subscribe stock pool to QMT
    C.set_universe(_stock_list)

    print('[INIT] Stock pool: {} stocks'.format(len(_stock_list)))
    print('[INIT] Account: {}'.format(_account.account_id))


def handlebar(C):
    """Main with debug output."""
    global _last_trade_date, _today_buys, _account, _stock_pool, _stock_list, _rebalance_days

    # Refresh state in case module was reloaded
    if _account is None:
        from .qmt_data import ZZ1800_STOCKS
        from .trading import QmtAccount
        from .config import RISK_CONFIG, REBALANCE_CONFIG
        from . import qmt_runner
        qmt_runner.qmt_init(C)
        _stock_pool = ZZ1800_STOCKS
        _stock_list = _stock_pool
        _account = QmtAccount(C)
        _rebalance_days = REBALANCE_CONFIG.get('rebalance_days', 5)

    # Skip non-last bars in backtest
    if not C.is_last_bar():
        return

    today = datetime.now().strftime('%Y-%m-%d')
    if _last_trade_date == today:
        return
    _last_trade_date = today

    print('[{}] Processing...'.format(today))

    # Update hold days
    for code in list(_hold_days.keys()):
        _hold_days[code] = _hold_days.get(code, 0) + 1

    # Risk check
    from . import qmt_runner
    qmt_runner.check_risk(C, _account, _hold_days)

    # Check rebalance
    is_rebal = qmt_runner.is_rebalance_day(C, _rebalance_days)

    if not is_rebal:
        print('[{}] Not rebalance day, skip'.format(today))
        return

    # Stock selection
    selected = _select_stocks(C)

    if not selected:
        print('[{}] No stocks selected'.format(today))
        return

    print('[{}] Selected: {}'.format(today, selected[:5]))

    # Target weight
    max_pos = 0.25
    max_holdings = 5
    target = {}
    for code in selected[:max_holdings]:
        target[code] = max_pos / len(selected[:max_holdings])

    qmt_runner.execute_buy(C, _account, target)


def _select_stocks(C):
    """V61C selection: low turnover + small cap."""
    from .qmt_data import FLOAT_SHARES, ZZ1800_STOCKS
    from .data import get_close_prices_batch

    # Get close prices
    prices = get_close_prices_batch(C, ZZ1800_STOCKS)
    if not prices:
        return []

    # Score by turnover (low is better) + small cap (low float shares is better)
    candidates = []
    for code in ZZ1800_STOCKS:
        if code not in prices or prices[code] <= 0:
            continue
        float_shares = FLOAT_SHARES.get(code, 0)
        if float_shares <= 0:
            continue
        candidates.append((code, float_shares, prices[code]))

    if not candidates:
        return []

    # Rank by float shares (ascending = small cap first)
    candidates.sort(key=lambda x: x[1])

    # Take top 20% as candidates
    n = max(5, len(candidates) // 5)
    return [c[0] for c in candidates[:n]]
