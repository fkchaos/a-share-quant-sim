#coding:gbk
"""
trading.py - QMT Trading Adapter

Based on official ThinkTrader API docs (v3.3.6):
- get_trade_detail_data: 'POSITION'/'ORDER'/'DEAL' (uppercase)
- passorder: prType=14 (counterparty), quickTrade=0 (backtest mode)
- Position fields: m_dOpenPrice (not m_dSettlementPrice)
- order_callback/deal_callback for trade confirmation
"""
import sys
import datetime


# Module-level order tracking
_orders = {}  # remark -> {status, stock, vol, price, filled, timestamp, ...}
_pending_reorders = []  # list of (code, remaining, reason) to re-order after cancel  # remark -> {status, code, shares, price, filled, ts}
_risk_debug = False


def set_risk_debug(flag):
    global _risk_debug
    _risk_debug = flag



def _get_account_id():
    """Get account_id from ACCOUNT_CONFIG."""
    from .config import ACCOUNT_CONFIG
    return str(ACCOUNT_CONFIG.get('account_id', ''))


def _get_qmt_func():
    """Get QMT built-in functions by walking up ALL frames."""
    frame = sys._getframe(1)
    while frame is not None:
        g = frame.f_globals
        if 'get_trade_detail_data' in g:
            trading = sys.modules[__name__]
            trading.get_trade_detail_data = g['get_trade_detail_data']
            if 'passorder' in g:
                trading.passorder = g['passorder']
            if 'get_last_order_id' in g:
                trading.get_last_order_id = g['get_last_order_id']
            return
        frame = frame.f_back


def _find_account_from_frames():
    """Walk up the call stack to find 'account' in QMT's global scope."""
    frame = sys._getframe(1)
    while frame is not None:
        if 'account' in frame.f_globals:
            val = frame.f_globals['account']
            if val and val != 'test':
                return val
        frame = frame.f_back
    return None


def _find_account_type_from_frames():
    """Walk up the call stack to find 'accountType'."""
    frame = sys._getframe(1)
    while frame is not None:
        if 'accountType' in frame.f_globals:
            return frame.f_globals['accountType']
        frame = frame.f_back
    return 'STOCK'


class QmtAccount(object):
    """QMT account operations wrapper.

    Internal position tracking for backtest mode.
    In backtest, get_trade_detail_data('POSITION') may return empty,
    so we track positions internally via order/deal callbacks.
    """

    def __init__(self, C):
        self.C = C
        self._internal_positions = {}  # code -> {shares, cost, name}

        account = _find_account_from_frames()
        if not account:
            try:
                from .config import ACCOUNT_CONFIG
                account = ACCOUNT_CONFIG.get('account_id', '')
            except Exception:
                pass
        if not account:
            account = 'SIMTEST'

        self.account_id = str(account)
        self.account_type = _find_account_type_from_frames()

        # Detect backtest mode
        self._is_backtest = getattr(C, 'do_back_test', False)
        if _risk_debug:
            print('[INIT] account=%s type=%s backtest=%s' % (
                self.account_id, self.account_type, self._is_backtest))

    def _query(self, query_type):
        """Query trade details.

        Official API: get_trade_detail_data(accountID, strAccountType, strDatatype)
        strDatatype must be UPPERCASE: 'POSITION', 'ORDER', 'DEAL', 'ACCOUNT', 'TASK'
        """
        _get_qmt_func()
        trading = sys.modules[__name__]
        has_func = hasattr(trading, 'get_trade_detail_data') and trading.get_trade_detail_data is not None
        if not has_func:
            if _risk_debug:
                print('[QMT] WARNING: get_trade_detail_data not available!')
            return []
        # Official API uses UPPERCASE datatype
        result = trading.get_trade_detail_data(self.account_id, self.account_type, query_type.upper())
        if _risk_debug and query_type.upper() == 'POSITION':
            print('[QMT] query POSITION: %d raw results (account=%s type=%s)' % (
                len(result) if result else 0, self.account_id, self.account_type))
        return result if result else []

    def get_cash(self):
        """Get available cash."""
        accounts = self._query("ACCOUNT")
        if not accounts:
            if _risk_debug:
                print('[QMT] WARNING: get_cash returned 0 accounts')
            return 0
        cash = accounts[0].m_dAvailable
        if _risk_debug:
            print('[QMT] get_cash: %.2f (account=%s)' % (cash, self.account_id))
        return cash

    def get_holdings(self):
        """Get current holdings as list of dicts.

        Official Position fields:
          m_strInstrumentID     - code without suffix (e.g. '600519')
          m_strExchangeID       - exchange ('SH'/'SZ')
          m_strInstrumentName   - stock name
          m_nVolume             - total position
          m_nCanUseVolume       - available (T+1: today's buy = 0)
          m_dOpenPrice          - open cost price
          m_dPositionProfit     - unrealized P&L
          m_dInstrumentValue    - current market value
        """
        positions = self._query("POSITION")

        # Try QMT API first
        holdings = []
        for p in positions:
            vol = getattr(p, 'm_nVolume', 0)
            if vol > 0:
                code = getattr(p, 'm_strInstrumentID', '') + '.' + getattr(p, 'm_strExchangeID', '')
                holdings.append({
                    'code': code,
                    'shares': vol,
                    'available': getattr(p, 'm_nCanUseVolume', 0),
                    'avg_cost': getattr(p, 'm_dOpenPrice', 0),
                    'name': getattr(p, 'm_strInstrumentName', ''),
                })

        # Fallback to internal tracking (for backtest where POSITION returns empty)
        if not holdings and self._internal_positions:
            for code, pos in self._internal_positions.items():
                if pos['shares'] > 0:
                    holdings.append({
                        'code': code,
                        'shares': pos['shares'],
                        'avg_cost': pos['cost'],
                        'name': pos.get('name', ''),
                    })

        if _risk_debug:
            source = 'API' if positions else 'INTERNAL'
            print('[HOLD] get_holdings: api=%d internal=%d total=%d source=%s' % (
                len(positions), len(self._internal_positions), len(holdings), source))
            if holdings:
                for h in holdings:
                    print('  -> %s: %d shares @ %.2f' % (h['code'], h['shares'], h.get('avg_cost', 0)))
        return holdings

    def get_position_detail(self, stock_code):
        """Get position detail for a specific stock."""
        holdings = self.get_holdings()
        for p in holdings:
            if p['code'] == stock_code:
                return p
        return None

    def get_total_value(self):
        """Get total portfolio value."""
        accounts = self._query("ACCOUNT")
        if not accounts:
            return 0
        return accounts[0].m_dStockValue + accounts[0].m_dFundValue

    def buy(self, stock_code, shares, price=-1, reason='BUY', strategy_name='V61C'):
        """Buy order.

        passorder params (official):
          opType=23 (stock buy), orderType=1101 (single, shares),
          prType=14 (counterparty price), quickTrade=2 (immediate)
        """
        _get_qmt_func()
        
        # Duplicate order prevention: check if same stock has pending order
        for remark, o in _orders.items():
            if o['code'] == stock_code and o['status'] in ('pending', 'ordered', 'partial'):
                print('[BUY] SKIP %s: duplicate order pending (remark=%s)' % (stock_code, remark))
                return None
        trading = sys.modules[__name__]

        # Generate unique userOrderId for callback matching
        now = datetime.datetime.now()
        remark = '%s-%s-%s' % (reason, stock_code.split('.')[0], now.strftime('%H%M%S'))

        trading.passorder(
            23,                     # opType: stock buy
            1101,                   # orderType: single stock, shares
            self.account_id,        # account
            stock_code,             # orderCode
            14,                     # prType: counterparty price (对手价)
            -1,                     # price: -1 ignored when prType != 11
            shares,                 # volume
            strategy_name,          # strategyName
            2,                      # quickTrade: 2=immediate (live mode)
            remark,                 # userOrderId -> m_strRemark in callback
            self.C                  # ContextInfo
        )

        # Register order for callback tracking
        import time as _time
        _orders[remark] = {
                'status': 'pending',
                'stock': stock_code,
                'vol': shares,
                'price': price,
                'filled': 0,
                'name': '',
                'timestamp': _time.time(),
                'account_id': self.account_id,
                'account_type': self.account_type,
                'context': self.C,
                'strategy_name': strategy_name,
            }

        if _risk_debug:
            print('[BUY] passorder sent: %s %d shares remark=%s' % (stock_code, shares, remark))

        # Start order poll after placing order
        start_order_poll(self.C, remark)
        return remark

    def sell(self, stock_code, shares, price=-1, reason='SELL', strategy_name='V61C'):
        """Sell order.

        passorder params (official):
          opType=24 (stock sell), orderType=1101 (single, shares),
          prType=14 (counterparty price), quickTrade=2 (immediate)
        """
        _get_qmt_func()
        trading = sys.modules[__name__]

        now = datetime.datetime.now()
        remark = '%s-%s-%s' % (reason, stock_code.split('.')[0], now.strftime('%H%M%S'))

        trading.passorder(
            24,                     # opType: stock sell
            1101,                   # orderType: single stock, shares
            self.account_id,        # account
            stock_code,             # orderCode
            14,                     # prType: counterparty price (对手价)
            -1,                     # price: -1 ignored when prType != 11
            shares,                 # volume
            strategy_name,          # strategyName
            2,                      # quickTrade: 2=immediate (live mode)
            remark,                 # userOrderId,
            self.C                  # ContextInfo
        )

        # Register order for callback tracking
        import time as _time
        _orders[remark] = {
                'status': 'pending',
                'stock': stock_code,
                'vol': shares,
                'price': price,
                'filled': 0,
                'name': '',
                'timestamp': _time.time(),
                'account_id': self.account_id,
                'account_type': self.account_type,
                'context': self.C,
                'strategy_name': strategy_name,
            }

        if _risk_debug:
            print('[SELL] passorder sent: %s %d shares remark=%s' % (stock_code, shares, remark))

        # Start order poll after placing order
        start_order_poll(self.C, remark)
        return remark

    def sell_all(self, stock_code, price=-1, reason='SELL_ALL'):
        """Sell all shares of a stock.

        Uses m_nVolume (total) not m_nCanUseVolume (available).
        A-share T+1: m_nCanUseVolume=0 for today's buy, but m_nVolume has the total.
        """
        pos = self.get_position_detail(stock_code)
        if pos and pos['shares'] > 0:
            return self.sell(stock_code, pos['shares'], price, reason)
        return None

    def buy_value(self, stock_code, target_value, price, reason='BUY'):
        """Buy by target value (auto-calculate shares with lot sizing)."""
        if price <= 0:
            return None
        shares = int(target_value / price / 100) * 100
        if shares > 0:
            return self.buy(stock_code, shares, price, reason)
        if _risk_debug:
            print("[BUY] SKIP %s: amount=%.0f price=%.2f -> lots=%d (need >=1)" % (
                stock_code, target_value, price, shares))
        return None


# ============================================================
# Order timeout check (call periodically)
# ============================================================

def check_order_timeout(timeout_seconds=60):
    """Check for orders and cancel stale ones.
    - pending + timeout: no order_callback -> cancel
    - ordered + timeout: partial fill or no fill -> cancel remaining
    """
    import time
    now = time.time()
    for remark, o in list(_orders.items()):
        if o['status'] not in ('pending', 'ordered'):
            continue
        age = now - o.get('timestamp', now)
        if age <= timeout_seconds:
            continue

        code = o['stock']
        remaining = o['vol'] - o['filled']

        if o['status'] == 'pending':
            # Never got order_callback -> likely rejected
            o['status'] = 'rejected'
            print('[TIMEOUT] %s rejected after %ds (no order_callback): %s' % (code, age, remark))

        elif o['status'] == 'ordered' and remaining > 0:
            # Got order_callback but partial/no fill -> cancel remaining
            order_id = o.get('order_id', '')
            if order_id:
                try:
                    _get_qmt_func()
                    trading = sys.modules[__name__]
                    acct = o.get('account_id', '')
                    acct_type = o.get('account_type', 'STOCK')
                    ctx = o.get('context', None)
                    if ctx:
                        result = trading.cancel(order_id, acct, acct_type, ctx)
                        print('[CANCEL] %s cancel sent: order_id=%s remaining=%d result=%s' % (code, order_id, remaining, result))
                        o['status'] = 'cancelled'
                    else:
                        print('[CANCEL] %s no ContextInfo, cannot cancel: %s' % (code, remark))
                except Exception as e:
                    print('[CANCEL] %s cancel failed: %s' % (code, e))
            else:
                print('[TIMEOUT] %s ordered but no order_id, cannot cancel: %s' % (code, remark))


# ============================================================
# Order polling lifecycle (schedule_run based)
# ============================================================

def _confirm_fill(remark, o, fill_price=0):
    """Update _internal_positions and strategy JSON after order fill.
    Guard: only runs once per order (sets o['confirmed']=True).
    fill_price: actual trade price (from dealInfo or ORDER query).
    Skips accounting if price <= 0 (waits for deal_callback).
    """
    if o.get('confirmed'):
        return  # already processed, skip
    code = o['stock']
    filled = o['filled']
    price = fill_price if fill_price > 0 else o.get('price', 0)
    if price <= 0:
        print('[FILL] SKIP %s: no valid price (fill=%.2f order=%.2f), wait for deal_cb' % (code, fill_price, o.get('price', 0)))
        return

    o['confirmed'] = True
    strategy_name = o.get('strategy_name', 'V61C')
    reason = remark.split('-')[0] if '-' in remark else ''

    # Update _internal_positions
    if reason == 'BUY':
        if code in _internal_positions:
            old = _internal_positions[code]
            new_shares = old['shares'] + filled
            new_cost = (old['cost'] * old['shares'] + price * filled) / new_shares if new_shares > 0 else 0
            _internal_positions[code] = {'shares': new_shares, 'cost': new_cost, 'name': old.get('name', '')}
        else:
            _internal_positions[code] = {'shares': filled, 'cost': price, 'name': ''}
        print('[FILL] %s BUY %d @ %.2f -> _internal_positions' % (code, filled, price))
    elif reason in ('SELL', 'SELL_ALL', 'RISK'):
        if code in _internal_positions:
            _internal_positions[code]['shares'] -= filled
            if _internal_positions[code]['shares'] <= 0:
                del _internal_positions[code]
        print('[FILL] %s SELL %d -> _internal_positions' % (code, filled))

    # Update strategy position JSON
    try:
        from . import qmt_runner
        sn = strategy_name.lower()
        if reason == 'BUY':
            qmt_runner.strategy_buy(sn, code, filled, price, '')
        else:
            qmt_runner.strategy_sell(sn, code, filled)
        print('[FILL] strategy JSON: %s %s %d' % (sn, code, filled))
    except Exception as e:
        print('[FILL] strategy JSON failed: %s' % e)

def start_order_poll(C, remark, strategy_name='default'):
    """Start per-order polling timer. One timer per order, self-cancels on resolve."""
    import datetime as _dt
    timer_name = 'opoll_' + remark
    def _make_cb(r, s):
        def cb(ContextInfo):
            try:
                _do_order_check_single(ContextInfo, r, s)
            except Exception as e:
                print('[ORDER_POLL][%s] ERROR: %s' % (r, e))
            finally:
                import time as _t
                o = _orders.get(r)
                if not o or o['status'] not in ('pending', 'ordered', 'partial'):
                    try:
                        ContextInfo.cancel_schedule_run('opoll_' + r)
                    except Exception:
                        pass
                elif _t.time() - o.get('timestamp', _t.time()) > 300:
                    print('[ORDER_POLL][%s] WARN: >300s, force stop' % r)
                    try:
                        ContextInfo.cancel_schedule_run('opoll_' + r)
                    except Exception:
                        pass
        return cb
    # Kill existing timer for same order
    try:
        C.cancel_schedule_run(timer_name)
    except Exception:
        pass
    now = _dt.datetime.now()
    target = now + _dt.timedelta(seconds=10)
    C.schedule_run(_make_cb(remark, strategy_name), target.strftime('%Y%m%d%H%M%S'),
                   repeat_times=-1, interval=_dt.timedelta(seconds=10),
                   name=timer_name)
    print('[ORDER_POLL] timer started: %s' % timer_name)


def _do_order_check_single(ContextInfo, remark, strategy_name):
    """Check status of a single order. Status only - fill accounting in deal_callback."""
    import time
    now = time.time()
    o = _orders.get(remark)
    if not o or o['status'] not in ('pending', 'ordered', 'partial'):
        return

    # Query ORDER from QMT
    try:
        _get_qmt_func()
        trading = sys.modules[__name__]
        acct = _get_account_id()
        qmt_orders = trading.get_trade_detail_data(acct, 'STOCK', 'ORDER')
    except Exception as e:
        print('[ORDER_POLL][%s] query failed: %s' % (remark, e))
        return

    # Match by remark
    for order in (qmt_orders or []):
        r = getattr(order, 'm_strRemark', '')
        if r != remark:
            continue
        traded = getattr(order, 'm_nVolumeTraded', 0)
        vol = getattr(order, 'm_nVolumeTotalOriginal', 0)
        order_id = getattr(order, 'm_strOrderSysID', '')
        if o['status'] in ('pending', 'ordered', 'partial'):
            o['filled'] = traded
            if order_id:
                o['order_id'] = order_id
            if traded >= vol and vol > 0:
                o['status'] = 'filled'
                print('[ORDER_POLL][%s] filled %d/%d' % (remark, traded, vol))
                # fill accounting handled by deal_callback (has real price)
            elif traded > 0:
                o['status'] = 'partial'
                print('[ORDER_POLL][%s] partial %d/%d' % (remark, traded, vol))
            elif o['status'] == 'pending':
                o['status'] = 'ordered'
                print('[ORDER_POLL][%s] ordered vol=%d' % (remark, vol))
        break  # found our order, done

    # Timeout check
    age = now - o.get('timestamp', now)
    if age <= 60:
        return
    code = o['stock']
    remaining = o['vol'] - o['filled']
    if o['status'] == 'pending':
        o['status'] = 'rejected'
        print('[ORDER_POLL][%s] rejected after %ds (no callback)' % (remark, int(age)))
        # Queue remaining for re-order (if buy)
        remaining = o['vol'] - o['filled']
        reason = remark.split('-')[0] if '-' in remark else ''
        if reason == 'BUY' and remaining > 0:
            _pending_reorders.append({
                'code': o['stock'], 'shares': remaining, 'price': o.get('price', -1),
                'reason': reason, 'strategy_name': o.get('strategy_name', 'V61C'),
                'account_id': o.get('account_id'), 'account_type': o.get('account_type', 'STOCK'),
                'context': o.get('context'),
            })
            print('[ORDER_POLL][%s] queued %d shares for re-order' % (remark, remaining))
        _orders.pop(remark, None)
    elif remaining > 0:
        order_id = o.get('order_id', '')
        if order_id:
            try:
                ctx = o.get('context', None)
                if ctx:
                    trading = sys.modules[__name__]
                    result = trading.cancel(order_id, o.get('account_id', ''), o.get('account_type', 'STOCK'), ctx)
                    print('[ORDER_POLL][%s] cancel: order_id=%s remaining=%d result=%s' % (remark, order_id, remaining, result))
                    o['status'] = 'cancelled'
                    # Queue remaining for re-order
                    reason = remark.split('-')[0] if '-' in remark else ''
                    if remaining > 0:
                        _pending_reorders.append({
                            'code': o['stock'], 'shares': remaining, 'price': o.get('price', -1),
                            'reason': reason, 'strategy_name': o.get('strategy_name', 'V61C'),
                            'account_id': o.get('account_id'), 'account_type': o.get('account_type', 'STOCK'),
                            'context': o.get('context'),
                        })
                        print('[ORDER_POLL][%s] queued %d shares for re-order' % (remark, remaining))
                    _orders.pop(remark, None)
            except Exception as e:
                print('[ORDER_POLL][%s] cancel failed: %s' % (remark, e))
        else:
            print('[ORDER_POLL][%s] ordered but no order_id' % remark)

# ============================================================
# Callback functions (QMT auto-calls these, no registration needed)
# ============================================================

_orders = {}  # remark -> {status, stock, vol, price, filled, name}
_pending_reorders = []  # [{code, shares, price, reason, strategy_name, account_id, account_type, context}, ...]

def process_pending_reorders(C):
    """Process pending reorders - re-place orders that were cancelled with remaining shares.
    Called from on_signal (strategy level) each cycle.
    """
    if not _pending_reorders:
        return
    # Take one per cycle to avoid flooding
    item = _pending_reorders.pop(0)
    code = item['code']
    shares = item['shares']
    price = item['price']
    reason = item['reason']
    strategy_name = item['strategy_name']
    print('[REORDER] re-placing %s %d shares reason=%s' % (code, shares, reason))
    # Use QmtAccount's buy/sell which handles remark generation + _orders registration + start_order_poll
    _get_qmt_func()
    trading = sys.modules[__name__]
    acct = _get_account_id()
    # Create a temporary QmtAccount to use buy/sell
    ctx = item['context']
    account = QmtAccount(ctx, item['account_id'], item['account_type'])
    if reason in ('BUY',):
        account.buy(code, shares, price, reason='BUY', strategy_name=strategy_name)
    elif reason in ('SELL', 'SELL_ALL', 'RISK'):
        account.sell(code, shares, price, reason=reason, strategy_name=strategy_name)
    else:
        print('[REORDER] unknown reason: %s' % reason)

def order_callback(ContextInfo, orderInfo):
    """order callback - called when order status changes.
    Tracks order state via userOrderId (m_strRemark).
    """
    remark = getattr(orderInfo, 'm_strRemark', '')
    code = getattr(orderInfo, 'm_strInstrumentID', '') + '.' + getattr(orderInfo, 'm_strExchangeID', '')
    vol = getattr(orderInfo, 'm_nVolumeTotalOriginal', 0)
    traded = getattr(orderInfo, 'm_nVolumeTraded', 0)
    status = getattr(orderInfo, 'm_nOrderStatus', -1)

    if remark and remark in _orders:
        _orders[remark]['status'] = 'ordered'
        print('[ORDER_CB] %s vol=%d traded=%d status=%d remark=%s' % (code, vol, traded, status, remark))
    else:
        print('[ORDER_CB] %s vol=%d traded=%d status=%d remark=%s (unknown)' % (code, vol, traded, status, remark))


def deal_callback(ContextInfo, dealInfo):
    """deal callback - called when order is (partially) filled.
    Updates position JSON and order state.
    """
    remark = getattr(dealInfo, 'm_strRemark', '')
    code = getattr(dealInfo, 'm_strInstrumentID', '') + '.' + getattr(dealInfo, 'm_strExchangeID', '')
    name = getattr(dealInfo, 'm_strInstrumentName', '')
    price = getattr(dealInfo, 'm_dPrice', 0)
    vol = getattr(dealInfo, 'm_nVolume', 0)
    amount = getattr(dealInfo, 'm_dTradeAmount', 0)
    direction = getattr(dealInfo, 'm_nOffsetFlag', 0)

    dir_str = 'BUY' if direction == 48 else 'SELL'
    print('[DEAL_CB] %s %s %s %d shares @ %.2f = %.0f CNY remark=%s' % (
        code, name, dir_str, vol, price, amount, remark))

    # Update order state
    if remark and remark in _orders:
        o = _orders[remark]
        o['filled'] += vol
        if o['filled'] >= o['vol']:
            o['status'] = 'filled'
            print('[DEAL_CB] %s fully filled: %d/%d' % (code, o['filled'], o['vol']))
            _confirm_fill(remark, o, fill_price=price)
            # Clean up _orders after confirmed fill
            if o.get('confirmed'):
                _orders.pop(remark, None)
        else:
            print('[DEAL_CB] %s partial fill: %d/%d, waiting...' % (code, o['filled'], o['vol']))
    else:
        print('[DEAL_CB] %s (unknown remark: %s)' % (code, remark))
