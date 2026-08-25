#coding:gbk
"""
v61c_debug_strategy.py - V61C Debug Version
"""
import pandas as pd
from datetime import datetime

# Module-level globals
_stock_pool = []
_stock_list = []
_account = None
_hold_days = {}
_last_rebalance_date = None
_today_buys = 0
_last_trade_date = None
_rebalance_days = 5


def init(C):
    """Init strategy."""
    global _stock_pool, _stock_list, _account, _rebalance_days
    global _hold_days, _last_rebalance_date, _today_buys, _last_trade_date

    from .qmt_data import ZZ1800_STOCKS
    from .trading import QmtAccount
    from .config import RISK_CONFIG, REBALANCE_CONFIG
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


def handlebar(C):
    """Main with debug output."""
    global _last_trade_date, _today_buys

    today = datetime.now().strftime('%Y-%m-%d')
    if _last_trade_date == today:
        return
    _last_trade_date = today

    print('[{}] Processing...'.format(today))

    # Update hold days
    for code in list(_hold_days.keys()):
        _hold_days[code] = _hold_days.get(code, 0) + 1

    from . import qmt_runner
    from .config import RISK_CONFIG

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
    max_pos = RISK_CONFIG.get('max_position', 0.25)
    max_holdings = RISK_CONFIG.get('max_holdings', 5)
    target = {}
    for code in selected[:max_holdings]:
        target[code] = max_pos / len(selected[:max_holdings])

    qmt_runner.execute_buy(C, _account, target)


def _select_stocks(C):
    """Select stocks: low turnover + small cap."""
    from .qmt_data import FLOAT_SHARES, INDUSTRY_MAP
    from .config import SELECTION_CONFIG
    from . import qmt_runner

    turnover_threshold = SELECTION_CONFIG.get('turnover_threshold', 0.05)
    top_n = SELECTION_CONFIG.get('top_n', 20)

    print('[SELECT] Pool: {} stocks'.format(len(_stock_list)))

    # Get market data
    df_dict = qmt_runner.get_market_data(C, _stock_list)

    print('[SELECT] Got data for {} stocks'.format(len(df_dict)))

    candidates = []
    for code, df in df_dict.items():
        if df is None or len(df) < 5:
            continue

        close = df['close']
        amount = df['amount']

        # Low turnover proxy: avg daily amount (lower is better)
        avg_amount_5 = amount.tail(5).mean()

        # Small cap
        float_shares = FLOAT_SHARES.get(code, 0)
        if float_shares <= 0:
            continue
        latest_close = close.iloc[-1]
        if pd.isna(latest_close) or latest_close <= 0:
            continue
        market_cap = latest_close * float_shares

        # Filter: not too large
        if market_cap > 20e9:
            continue

        candidates.append({
            'code': code,
            'market_cap': market_cap,
            'avg_amount_5': avg_amount_5
        })

    if not candidates:
        return []

    # Sort: low turnover first, then small cap
    # Normalize
    max_amt = max(c['avg_amount_5'] for c in candidates) if candidates else 1
    max_cap = max(c['market_cap'] for c in candidates) if candidates else 1

    for c in candidates:
        c['turnover_score'] = c['avg_amount_5'] / max_amt if max_amt > 0 else 1
        c['cap_score'] = c['market_cap'] / max_cap if max_cap > 0 else 1
        # Combined: lower is better
        c['combined'] = c['turnover_score'] * 0.5 + c['cap_score'] * 0.5

    # Sort ascending (lower = better)
    candidates.sort(key=lambda x: x['combined'])

    selected = [c['code'] for c in candidates[:top_n]]
    print('[SELECT] Selected {} stocks, top 5: {}'.format(
        len(selected), selected[:5]))

    return selected
