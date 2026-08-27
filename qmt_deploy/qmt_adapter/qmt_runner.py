# QMT_RUNNER v2 - set_risk_debug added
#coding:gbk
"""
qmt_runner.py - QMT Common Runner

Encapsulates QMT environment init, global state, risk control etc.
Strategy files only need to implement stock selection logic.
"""
import sys

_qmt_initialized = False
_risk_debug = False

def set_risk_debug(flag):
    global _risk_debug
    _risk_debug = flag


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


def check_risk(C, account, holding_days, risk_config=None, bar_date=None):
    """Common risk control. Returns list of sold codes."""
    if risk_config is None:
        from .config import RISK_CONFIG
        risk_config = RISK_CONFIG
    sl = risk_config['stop_loss']
    tp = risk_config['take_profit']
    hd = risk_config['hold_days_max']
    sold = []

    positions = account.get_holdings()
    if _risk_debug:
        print('[%s][RISK] check_risk: %d holdings' % (bar_date or '??', len(positions)))

    for p in positions:
        code = p['code']
        shares = p['shares']
        if shares <= 0:
            continue

        hold_days = holding_days.get(code, 0)
        cost_price = p.get('avg_cost', 0)

        from .data import get_close_price
        cur_price = get_close_price(C, code, bar_date)
        if cur_price <= 0 or cost_price <= 0:
            continue

        pnl = (cur_price - cost_price) / cost_price
        if _risk_debug:
            print('[%s][RISK] %s: cost=%.2f cur=%.2f pnl=%.2f%% (SL=%.2f%% TP=%.2f%%) days=%d (HD=%d) -> %s' % (bar_date or '??',
                code, cost_price, cur_price, pnl*100, sl*100, tp*100, hold_days, hd,
            'SELL' if (pnl < sl or pnl > tp or hold_days >= hd) else 'HOLD'))

        if pnl < sl:
            account.sell_all(code)
            sold.append(code)
            continue

        if pnl > tp:
            account.sell_all(code)
            sold.append(code)
            continue

        if hold_days >= hd:
            account.sell_all(code)
            sold.append(code)

    return sold


def execute_buy(C, account, target_weight, bar_date='', capital=50000):
    """Common buy execution. Returns list of actually bought codes.
    
    Args:
        capital: per-strategy fixed budget (independent of account total).
                 Each stock gets capital * weight.
    """
    from .config import MARKET_CONFIG
    bought = []

    available = account.get_cash()
    if available < 1000:
        return bought

    for code, weight in target_weight.items():
        buy_amount = capital * weight
        buy_amount = min(buy_amount, available * 0.95)

        from .data import get_close_price
        price = get_close_price(C, code, bar_date)
        lots = int(buy_amount / price / 100) if price > 0 else 0
        print('[BUY] %s: amount=%.0f price=%.2f lots=%d (need >=1)' % (code, buy_amount, price, lots))

        if buy_amount < 5000:
            print('[BUY] SKIP %s: amount < 5000' % code)
            continue
        if price <= 0:
            print('[BUY] SKIP %s: price <= 0' % code)
            continue
        if lots < 1:
            print('[BUY] SKIP %s: cannot afford 1 lot' % code)
            continue

        account.buy_value(code, buy_amount, price)
        print('[BUY] EXECUTED %s: %.0f CNY -> %d lots' % (code, buy_amount, lots))
        bought.append(code)

    return bought


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

    trades = get_trade_detail_data(account_id, account_type, "ORDER")

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
