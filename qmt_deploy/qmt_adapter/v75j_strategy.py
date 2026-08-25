#coding:gbk
"""
v75j_strategy.py - V75J Strategy Logic

Tech trend + liquidity factor + breadth filter.
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
_rebalance_days = 10


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
    _rebalance_days = REBALANCE_CONFIG.get('rebalance_days', 10)


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

    max_pos = 0.35
    max_holdings = 3
    target = {}
    for code in selected[:max_holdings]:
        target[code] = max_pos / len(selected[:max_holdings])

    qmt_runner.execute_buy(C, _account, target)


def _select_stocks(C):
    """Select: tech trend + liquidity + breadth."""
    from .qmt_data import FLOAT_SHARES, INDUSTRY_MAP
    from .config import SELECTION_CONFIG

    from . import qmt_runner
    df_dict = qmt_runner.get_market_data(C, _stock_list)

    candidates = []
    for code, df in df_dict.items():
        if df is None or len(df) < 20:
            continue

        close = df['close']
        volume = df['volume']
        amount = df['amount']

        # Tech trend: MA5 > MA20
        ma5 = close.rolling(5).mean()
        ma20 = close.rolling(20).mean()
        if pd.isna(ma5.iloc[-1]) or pd.isna(ma20.iloc[-1]):
            continue
        trend_up = ma5.iloc[-1] > ma20.iloc[-1]

        # Liquidity: recent avg amount
        avg_amount_5 = amount.tail(5).mean()
        if pd.isna(avg_amount_5) or avg_amount_5 <= 0:
            continue

        # Breadth: % of MA20 stocks in uptrend (simplified)
        # We use the stock's own trend as proxy
        if not trend_up:
            continue

        candidates.append({
            'code': code,
            'trend_score': 1.0 if trend_up else 0.0,
            'liquidity_score': avg_amount_5
        })

    if not candidates:
        return []

    # Sort by liquidity (higher is better for liquidity factor)
    candidates.sort(key=lambda x: x['liquidity_score'], reverse=True)
    return [c['code'] for c in candidates[:20]]
