#coding:gbk
"""
v61c_strategy.py - V61C Strategy Logic

Low turnover + small cap factors.
"""
import pandas as pd

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
    """Init."""
    global _stock_pool, _stock_list, _account, _rebalance_days

    from .qmt_data import ZZ1800_STOCKS
    from .trading import QmtAccount
    from .config import ACCOUNT_CONFIG, RISK_CONFIG, REBALANCE_CONFIG
    from . import qmt_runner

    qmt_runner.qmt_init(C)

    _stock_pool = ZZ1800_STOCKS
    _stock_list = _stock_pool
    _account = QmtAccount(C)
    _rebalance_days = REBALANCE_CONFIG.get('rebalance_days', 5)


def handlebar(C):
    """Main."""
    global _last_trade_date, _today_buys, _hold_days

    from datetime import datetime
    from . import qmt_runner

    today = datetime.now().strftime('%Y-%m-%d')
    if _last_trade_date == today:
        return
    _last_trade_date = today

    for code in list(_hold_days.keys()):
        _hold_days[code] = _hold_days.get(code, 0) + 1

    qmt_runner.check_risk(C, _account, _hold_days)

    is_rebal = qmt_runner.is_rebalance_day(C, _rebalance_days)
    if not is_rebal:
        return

    selected = _select_stocks(C)
    if not selected:
        return

    max_pos = 0.25
    max_holdings = 5
    target = {}
    for code in selected[:max_holdings]:
        target[code] = max_pos / len(selected[:max_holdings])

    qmt_runner.execute_buy(C, _account, target)


def _select_stocks(C):
    """Select: low turnover + small cap."""
    from .qmt_data import FLOAT_SHARES, INDUSTRY_MAP
    from .config import SELECTION_CONFIG

    top_n = SELECTION_CONFIG.get('top_n', 20)

    from . import qmt_runner
    df_dict = qmt_runner.get_market_data(C, _stock_list)

    candidates = []
    for code, df in df_dict.items():
        if df is None or len(df) < 5:
            continue

        close = df['close']
        amount = df['amount']
        avg_amount_5 = amount.tail(5).mean()

        float_shares = FLOAT_SHARES.get(code, 0)
        if float_shares <= 0:
            continue
        latest_close = close.iloc[-1]
        if pd.isna(latest_close) or latest_close <= 0:
            continue
        market_cap = latest_close * float_shares

        if market_cap > 20e9:
            continue

        candidates.append({
            'code': code,
            'market_cap': market_cap,
            'avg_amount_5': avg_amount_5
        })

    if not candidates:
        return []

    max_amt = max(c['avg_amount_5'] for c in candidates)
    max_cap = max(c['market_cap'] for c in candidates)

    for c in candidates:
        c['turnover_score'] = c['avg_amount_5'] / max_amt if max_amt > 0 else 1
        c['cap_score'] = c['market_cap'] / max_cap if max_cap > 0 else 1
        c['combined'] = c['turnover_score'] * 0.5 + c['cap_score'] * 0.5

    candidates.sort(key=lambda x: x['combined'])
    return [c['code'] for c in candidates[:top_n]]
