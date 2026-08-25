#coding:gbk
"""
v61c_debug_strategy.py - V61C Debug Version

Verbose output for debugging in QMT backtest/live.
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

    print('[INIT] Pool: {} stocks, Account: {}, Risk: SL={}, TP={}, HD={}'.format(
        len(_stock_list), _account.account_id,
        RISK_CONFIG['stop_loss'], RISK_CONFIG['take_profit'], RISK_CONFIG['hold_days_max']))


def handlebar(C):
    """Main with debug output."""
    global _last_trade_date, _today_buys, _account, _stock_pool, _stock_list, _rebalance_days
    try:
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

        today = datetime.now().strftime('%Y-%m-%d')

        for code in list(_hold_days.keys()):
            _hold_days[code] = _hold_days.get(code, 0) + 1

        from . import qmt_runner

        qmt_runner.check_risk(C, _account, _hold_days)

        is_rebal = qmt_runner.is_rebalance_day(C, _rebalance_days)
        if not is_rebal:
            return

        selected = _select_stocks(C)
        if not selected:
            print('[{}] No stocks selected'.format(today))
            return

        print('[{}] Selected: {}'.format(today, selected[:5]))

        max_pos = 0.25
        max_holdings = 5
        target = {}
        for code in selected[:max_holdings]:
            target[code] = max_pos / len(selected[:max_holdings])

        print('[{}] Target: {}'.format(today, target))
        qmt_runner.execute_buy(C, _account, target)
    except Exception as e:
        print('[ERROR] {}'.format(e))
        import traceback
        traceback.print_exc()


def _select_stocks(C):
    """V61C stock selection: low turnover + small cap."""
    from .qmt_data import FLOAT_SHARES
    from .data import get_close_prices_batch

    candidates = []
    for code in _stock_list:
        if code in FLOAT_SHARES:
            candidates.append(code)

    if not candidates:
        return []

    prices = get_close_prices_batch(C, candidates)
    if not prices:
        print('[SELECT] no prices returned')
        return []

    scored = []
    for code in candidates:
        if code not in prices or prices[code] <= 0:
            continue
        fs = FLOAT_SHARES.get(code, 0)
        if fs <= 0:
            continue
        price = prices[code]
        mcap = price * fs
        scored.append((code, mcap))

    if not scored:
        return []

    scored.sort(key=lambda x: x[1])
    ranked = [code for code, _ in scored]

    print('[SELECT] {} candidates, top 5: {}'.format(len(ranked), ranked[:5]))
    return ranked
