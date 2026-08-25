#coding:gbk
"""
v61c_debug_strategy.py - V61C Debug Version

Adds verbose output for debugging.
"""
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
    from .config import ACCOUNT_CONFIG, RISK_CONFIG, REBALANCE_CONFIG
    from . import qmt_runner

    qmt_runner.qmt_init(C)

    _stock_pool = ZZ1800_STOCKS
    _stock_list = _stock_pool
    _account = QmtAccount(C)
    _hold_days = {}
    _last_rebalance_date = None
    _today_buys = 0
    _last_trade_date = None
    _rebalance_days = REBALANCE_CONFIG.get('rebalance_days', 5)

    print('[INIT] Stock pool: {} stocks'.format(len(_stock_list)))
    print('[INIT] Account: {}'.format(_account.account_id))
    print('[INIT] Risk: SL={}, TP={}, HD={}'.format(
        RISK_CONFIG['stop_loss'], RISK_CONFIG['take_profit'], RISK_CONFIG['hold_days_max']))


def handlebar(C):
    """Main with debug output."""
    global _last_trade_date, _today_buys, _account, _stock_pool, _stock_list, _rebalance_days
    try:
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

        print('[DEBUG] handlebar start, account={}'.format(_account.account_id if _account else 'None'))

        today = datetime.now().strftime('%Y-%m-%d')

        # Update hold days
        for code in list(_hold_days.keys()):
            _hold_days[code] = _hold_days.get(code, 0) + 1

        from . import qmt_runner

        # Risk check
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
    except Exception as e:
        print('[ERROR] handlebar exception: {}'.format(e))
        import traceback
        traceback.print_exc()


def _select_stocks(C):
    """V61C stock selection: low turnover + small cap."""
    from .qmt_data import FLOAT_SHARES
    from .data import get_close_prices_batch

    # Get all stock codes with float_shares data
    candidates = []
    for code in _stock_list:
        if code in FLOAT_SHARES:
            candidates.append(code)

    if not candidates:
        return []

    # Get close prices
    prices = get_close_prices_batch(C, candidates)

    # Score: lower turnover (proxy: amount/float_shares) + smaller market cap (proxy: price*float_shares)
    scored = []
    for code in candidates:
        if code not in prices or prices[code] <= 0:
            continue
        fs = FLOAT_SHARES.get(code, 0)
        if fs <= 0:
            continue
        price = prices[code]
        # Small cap score (lower is better, rank later)
        mcap = price * fs
        # Use price as proxy for simplicity
        scored.append((code, mcap))

    if not scored:
        return []

    # Rank by market cap (ascending = smaller cap first)
    scored.sort(key=lambda x: x[1])
    ranked = [code for code, _ in scored]

    return ranked
