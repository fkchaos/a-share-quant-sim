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

    positions = account.get_holdings()  # returns list

    for p in positions:
        code = p['code']
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
    print('[EXEC] available cash: {}'.format(available))
    if available < 10000:
        print('[EXEC] cash too low, skip')
        return

    # Use available cash as base if total value unavailable (backtest init)
    total_value = account.get_total_value()
    if total_value <= 0:
        total_value = available
    print('[EXEC] total value: {}'.format(total_value))

    for code, weight in target_weight.items():
        buy_amount = total_value * weight
        buy_amount = min(buy_amount, available * 0.95)
        print('[EXEC] {} weight={} buy_amount={:.0f}'.format(code, weight, buy_amount))

        if buy_amount < 5000:
            print('[EXEC] {} amount too low, skip'.format(code))
            continue

        from .data import get_close_price
        price = get_close_price(C, code)
        print('[EXEC] {} price={}'.format(code, price))
        if price <= 0:
            continue

        account.buy(code, buy_amount, price)


def get_market_data(C, stock_list):
    """Get market data."""
    from .data import load_kline
    return load_kline(C, stock_list)


def is_rebalance_day(C, rebalance_days):
    """Check if today is a rebalance day."""
    from .trading import _get_qmt_func, _find_account_from_frames, _find_account_type_from_frames
    _get_qmt_func()
    from .trading import get_trade_detail_data

    account_id = _find_account_from_frames()
    account_type = _find_account_type_from_frames()

    if not account_id:
        return True

    trades = get_trade_detail_data(account_id, account_type, "stockorders")

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
