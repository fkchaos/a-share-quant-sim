#coding:gbk
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

        # T+1: skip risk check on buy day
        if hold_days < 1:
            continue

        from .data import get_close_price
        cur_price = get_close_price(C, code, bar_date)
        if cur_price <= 0 or cost_price <= 0:
            continue

        pnl = (cur_price - cost_price) / cost_price

        # Limit up check: don't sell on take profit if limit up
        is_limit_up = False
        from .data import get_kline_data_multi
        try:
            _kl = get_kline_data_multi(C, [code], count=1)
            if code in _kl and len(_kl[code]) > 0:
                df = _kl[code]
                last_close = df['close'].iloc[-1]
                last_high = df['high'].iloc[-1]
                if last_close > 0 and last_high > 0 and last_close >= last_high:
                    is_limit_up = True
        except Exception:
            pass

        if _risk_debug:
            print('[%s][RISK] %s: cost=%.2f cur=%.2f pnl=%.2f%% (SL=%.2f%% TP=%.2f%%) days=%d (HD=%d) limit_up=%s -> %s' % (bar_date or '??',
                code, cost_price, cur_price, pnl*100, sl*100, tp*100, hold_days, hd, is_limit_up,
            'SELL' if (pnl < sl or (pnl > tp and not is_limit_up) or hold_days >= hd) else 'HOLD'))

        if pnl < sl:
            account.sell_all(code)
            sold.append(code)
            continue

        if pnl > tp and not is_limit_up:
            account.sell_all(code)
            sold.append(code)
            continue

        # HOLD_DAYS_EXTEND: extend if profit > threshold
        hd_extend = risk_config.get('hold_days_extend', hd)
        hd_extend_pnl = risk_config.get('hold_days_extend_pnl', 0.03)
        if pnl >= hd_extend_pnl:
            # Profitable: use extended hold days
            if hold_days >= hd_extend:
                account.sell_all(code)
                sold.append(code)
        else:
            # Not profitable: use normal hold days
            if hold_days >= hd:
                account.sell_all(code)
                sold.append(code)

    # Start order poll if any sells placed
    if sold:
        from .trading import start_order_poll
        start_order_poll(C)

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

    # Start order poll if any orders placed
    if bought:
        from .trading import start_order_poll
        start_order_poll(C)

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

# ���� Per-strategy position tracking (temporary) ����
def _get_positions_path(strategy_name):
    """Get path to strategy's local position JSON."""
    import os
    return os.path.join(os.path.dirname(__file__), '_positions_%s.json' % strategy_name)

def load_strategy_positions(strategy_name):
    """Load per-strategy positions. Empty dict if file not found."""
    import json, os
    path = _get_positions_path(strategy_name)
    try:
        with open(path, 'r') as f:
            return json.load(f)
    except:
        return {}

def save_strategy_positions(strategy_name, positions):
    """Save per-strategy positions to JSON."""
    import json
    path = _get_positions_path(strategy_name)
    with open(path, 'w') as f:
        json.dump(positions, f)

def strategy_buy(strategy_name, code, shares, cost_price, date=''):
    """Record a buy in strategy's position file."""
    pos = load_strategy_positions(strategy_name)
    if code in pos:
        old = pos[code]
        total_cost = old['cost_price'] * old['shares'] + cost_price * shares
        total_shares = old['shares'] + shares
        pos[code] = {
            'shares': total_shares,
            'cost_price': round(total_cost / total_shares, 4) if total_shares > 0 else 0,
            'added_at': old.get('added_at', date),
        }
    else:
        pos[code] = {'shares': shares, 'cost_price': cost_price, 'added_at': date}
    save_strategy_positions(strategy_name, pos)

def strategy_sell(strategy_name, code, shares):
    """Record a sell in strategy's position file."""
    pos = load_strategy_positions(strategy_name)
    if code in pos:
        pos[code]['shares'] -= shares
        if pos[code]['shares'] <= 0:
            del pos[code]
        save_strategy_positions(strategy_name, pos)

def get_strategy_holdings(strategy_name, account):
    """Get holdings: per-strategy if enabled, else account-wide."""
    from .config import PER_STRATEGY_POSITIONS
    if not PER_STRATEGY_POSITIONS:
        return account.get_holdings()
    pos = load_strategy_positions(strategy_name)
    return [{'code': c, 'shares': v['shares'], 'avg_cost': v['cost_price']} 
            for c, v in pos.items() if v.get('shares', 0) > 0]

def strategy_stock_count(strategy_name):
    """Count stocks held by this strategy."""
    from .config import PER_STRATEGY_POSITIONS
    if not PER_STRATEGY_POSITIONS:
        return None  # caller should use account
    pos = load_strategy_positions(strategy_name)
    return sum(1 for v in pos.values() if v.get('shares', 0) > 0)
