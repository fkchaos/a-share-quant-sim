#coding:gbk
"""
qmt_runner.py - QMT Common Runner

Encapsulates QMT environment init, global state, risk control etc.
Strategy files only need to implement stock selection logic.
"""
import sys

_qmt_initialized = False


def qmt_init(C):
    """Common QMT initialization. Injects built-in functions into trading module."""
    global _qmt_initialized
    if _qmt_initialized:
        return

    from . import trading
    frame = sys._getframe(1)
    caller_globals = frame.f_globals

    for name in ['get_trade_detail_data', 'passorder', 'get_last_order_id']:
        if name in caller_globals:
            setattr(trading, name, caller_globals[name])

    _qmt_initialized = True


def check_risk(C, account, holding_days):
    """Common risk control: stop loss / take profit / max hold days."""
    from .config import RISK_CONFIG
    sl = RISK_CONFIG['stop_loss']
    tp = RISK_CONFIG['take_profit']
    hd = RISK_CONFIG['hold_days_max']

    from .trading import QmtAccount
    positions = account.get_holdings()

    for code, p in positions.items():
        shares = p['shares']
        if shares <= 0:
            continue

        hold_days = holding_days.get(code, 0)
        cost_price = p.get('avg_cost', 0)

        from .data import get_close_price
        cur_price = get_close_price(C, code)
        if cur_price <= 0 or cost_price <= 0:
            continue

        pnl = (cur_price - cost_price) / cost_price

        if pnl < sl:
            account.sell_all(code)
            continue

        if pnl > tp:
            account.sell_all(code)
            continue

        if hold_days >= hd:
            account.sell_all(code)


def execute_buy(C, account, target_weight):
    """Common buy execution."""
    from .config import MARKET_CONFIG

    available = account.get_cash()
    if available < 10000:
        return

    total_value = account.get_total_value()
    if total_value <= 0:
        return

    for code, weight in target_weight.items():
        buy_amount = total_value * weight
        buy_amount = min(buy_amount, available * 0.95)

        if buy_amount < 5000:
            continue

        from .data import get_close_price
        price = get_close_price(C, code)
        if price <= 0:
            continue

        account.buy(code, buy_amount, price)


def get_market_data(C, stock_list):
    """Get market data."""
    from .data import load_kline
    return load_kline(C, stock_list)


def is_rebalance_day(C, rebalance_days):
    """Check if today is a rebalance day."""
    from .trading import _get_qmt_func
    _get_qmt_func()
    from .trading import get_trade_detail_data
    from xtquant.xttype import StockAccount

    account_id = C.account_id
    account = StockAccount(account_id, "STOCK")
    trades = get_trade_detail_data(account, "trade", "", 1)

    if not trades:
        return True

    from datetime import datetime
    last_trade = max(trades, key=lambda t: t.ordertime if hasattr(t, "ordertime") else "")
    if hasattr(last_trade, "ordertime") and last_trade.ordertime:
        last_date = last_trade.ordertime.date()
        today = datetime.now().date()
        days_diff = (today - last_date).days
        return days_diff >= rebalance_days

    return True
