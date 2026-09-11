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
                print('[QMT] WARN: failed to load ACCOUNT_CONFIG')
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
          prType=14 (counterparty price), quickTrade=0(backtest)/2(live)
        """
        _get_qmt_func()
        
        # Duplicate order prevention: check if same stock has pending order
        for remark, o in _orders.items():
            if o['stock'] == stock_code and o['status'] in ('pending', 'ordered', 'partial'):
                print('[BUY] SKIP %s: duplicate order pending (remark=%s)' % (stock_code, remark))
                return None
        trading = sys.modules[__name__]

        # Generate unique userOrderId for callback matching
        now = datetime.datetime.now()
        remark = 'B-%s-%s-%s' % (reason, stock_code.split('.')[0], now.strftime('%H%M%S'))

        _is_bt = getattr(self.C, 'do_back_test', False)
        _qt = 0 if _is_bt else 2
        print('[PASSORDER] opType=23, orderType=1101, account=%s, stockCode=%s, prType=14, price=-1, vol=%d, strategyName=%s, quickTrade=%d, remark=%s' % (
            self.account_id, stock_code, shares, strategy_name, _qt, remark))
        trading.passorder(
            23,                     # opType: stock buy
            1101,                   # orderType: single stock, shares
            self.account_id,        # account
            stock_code,             # orderCode
            14,                     # prType: counterparty price (counterparty)
            -1,                     # price: -1 ignored when prType != 11
            shares,                 # volume
            strategy_name,          # strategyName
            _qt,                    # quickTrade: 0=backtest, 2=live
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
          prType=14 (counterparty price), quickTrade=0(backtest)/2(live)
        """
        _get_qmt_func()
        trading = sys.modules[__name__]

        now = datetime.datetime.now()
        remark = 'S-%s-%s-%s' % (reason, stock_code.split('.')[0], now.strftime('%H%M%S'))

        _is_bt = getattr(self.C, 'do_back_test', False)
        _qt = 0 if _is_bt else 2
        print('[PASSORDER] opType=24, orderType=1101, account=%s, stockCode=%s, prType=14, price=-1, vol=%d, strategyName=%s, quickTrade=%d, remark=%s' % (
            self.account_id, stock_code, shares, strategy_name, _qt, remark))
        trading.passorder(
            24,                     # opType: stock sell
            1101,                   # orderType: single stock, shares
            self.account_id,        # account
            stock_code,             # orderCode
            14,                     # prType: counterparty price (counterparty)
            -1,                     # price: -1 ignored when prType != 11
            shares,                 # volume
            strategy_name,          # strategyName
            _qt,                    # quickTrade: 0=backtest, 2=live
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

    def sell_all(self, stock_code, price=-1, reason='SELL_ALL', strategy_name='default'):
        """Sell all shares of a stock.

        Uses m_nVolume (total) not m_nCanUseVolume (available).
        A-share T+1: m_nCanUseVolume=0 for today's buy, but m_nVolume has the total.
        """
        pos = self.get_position_detail(stock_code)
        if pos and pos['shares'] > 0:
            return self.sell(stock_code, pos['shares'], price, reason, strategy_name=strategy_name)
        return None

    def buy_value(self, stock_code, target_value, price, reason='BUY', strategy_name='default'):
        """Buy by target value (auto-calculate shares with lot sizing)."""
        if price <= 0:
            return None
        shares = int(target_value / price / 100) * 100
        if shares > 0:
            return self.buy(stock_code, shares, price, reason, strategy_name=strategy_name)
        if _risk_debug:
            print("[BUY] SKIP %s: amount=%.0f price=%.2f -> lots=%d (need >=1)" % (
                stock_code, target_value, price, shares))
        return None


# ============================================================
# Order timeout check (call periodically)
# ============================================================
def cancel(order_id, account_id, account_type, C):
    """Cancel an order by order_id. Returns result string."""
    try:
        C.cancel_stock_order(order_id)
        return 'cancelled'
    except Exception as e:
        print('[CANCEL] failed: order_id=%s error=%s' % (order_id, e))
        return 'error: %s' % e


def cancel_safe(order_id, C):
    """Cancel order with automatic account detection. Safer wrapper."""
    try:
        C.cancel_stock_order(order_id)
        return 'cancelled'
    except Exception as e:
        print('[CANCEL] failed: order_id=%s error=%s' % (order_id, e))
        return 'error: %s' % e

# Order polling lifecycle (schedule_run based)
# ============================================================

def start_order_poll(C, remark, strategy_name='default'):
    """Start per-order polling timer. One timer per order, self-cancels on resolve."""
    import datetime as _dt
    timer_name = 'opoll_' + remark
    def _make_cb(r, s):
        def cb(ContextInfo):
            try:
                _do_order_check_single(ContextInfo, r, s)
            except Exception as e:
                print('[ORDER_POLL][%s] ERROR: %s' % (r, e))  # always log errors
            finally:
                import time as _t
                o = _orders.get(r)
                if not o or o['status'] not in ('pending', 'ordered', 'partial'):
                    try:
                        ContextInfo.cancel_schedule_run('opoll_' + r)
                    except Exception:
                        print('[ORDER_POLL][%s] WARN: cancel_schedule_run failed' % r)
                elif _t.time() - o.get('timestamp', _t.time()) > 300:
                    print('[ORDER_POLL][%s] WARN: >300s, force stop + cancel' % r)
                    # Cancel order on QMT before stopping timer
                    order_id = o.get('order_id', '')
                    if order_id and o['status'] in ('ordered', 'partial'):
                        try:
                            trading = sys.modules.get('qmt_adapter.trading')
                            if trading:
                                trading.cancel(order_id, o.get('account_id', ''), o.get('account_type', 'STOCK'), ContextInfo)
                        except Exception:
                            print('[ORDER_POLL][%s] WARN: cancel order failed: %s' % (r, order_id))
                    o['status'] = 'cancelled'
                    _orders.pop(r, None)
                    try:
                        ContextInfo.cancel_schedule_run('opoll_' + r)
                    except Exception:
                        print('[ORDER_POLL][%s] WARN: cancel_schedule_run (force) failed' % r)
        return cb
    # Kill existing timer for same order
    try:
        C.cancel_schedule_run(timer_name)
    except Exception:
        print('[ORDER_POLL] WARN: cancel existing timer failed: %s' % timer_name)
    now = _dt.datetime.now()
    target = now + _dt.timedelta(seconds=10)
    C.schedule_run(_make_cb(remark, strategy_name), target.strftime('%Y%m%d%H%M%S'),
                   repeat_times=-1, interval=_dt.timedelta(seconds=10),
                   name=timer_name)
    if _risk_debug:
        print('[ORDER_POLL] timer started: %s' % timer_name)


def _do_order_check_single(ContextInfo, remark, strategy_name):
    """Check status of a single order.
    THREE-LAYER CONFIRMATION: ORDER -> DEAL -> POSITION
    (QMT ORDER list may not include fully executed orders)
    """
    import time
    now = time.time()
    o = _orders.get(remark)
    if not o or o['status'] not in ('pending', 'ordered', 'partial'):
        return  # finished or unknown - skip

    _get_qmt_func()
    trading = sys.modules[__name__]
    acct = _get_account_id()

    # Layer 1: Query ORDER (委托列表)
    found = False
    try:
        qmt_orders = trading.get_trade_detail_data(acct, 'STOCK', 'ORDER', strategy_name)
        for order in (qmt_orders or []):
            r = getattr(order, 'm_strRemark', '')
            if r != remark:
                continue
            found = True
            traded = getattr(order, 'm_nVolumeTraded', 0)
            vol = getattr(order, 'm_nVolumeTotalOriginal', 0)
            order_id = getattr(order, 'm_strOrderSysID', '')
            if order_id:
                o['order_id'] = order_id

            # Delta fill: new volume since last check
            prev_filled = o['filled']
            delta = traded - prev_filled
            if delta > 0:
                o['filled'] = traded
                _update_internal_fill(o, remark, delta, traded, vol)
            break
    except Exception as e:
        print('[ORDER_POLL][%s] ORDER query failed: %s' % (remark, e))
        return

    # Layer 2: If ORDER not found, query DEAL (成交记录)
    if not found:
        try:
            qmt_deals = trading.get_trade_detail_data(acct, 'STOCK', 'DEAL', strategy_name)
            for deal in (qmt_deals or []):
                r = getattr(deal, 'm_strRemark', '')
                if r != remark:
                    continue
                found = True
                traded = getattr(deal, 'm_nVolume', 0)
                vol = o.get('vol', 0)
                order_id = getattr(deal, 'm_strOrderSysID', '')
                if order_id:
                    o['order_id'] = order_id

                # Deal found = fully executed
                if traded > 0 and o['status'] != 'filled':
                    o['status'] = 'filled'
                    o['filled'] = traded
                    _update_internal_fill(o, remark, traded, traded, vol)
                    print('[ORDER_POLL][%s] confirmed via DEAL: %d shares executed' % (remark, traded))
                break
        except Exception as e:
            print('[ORDER_POLL][%s] DEAL query failed: %s' % (remark, e))

    # Layer 3: If still not found, check POSITION (持仓验证)
    if not found:
        _code = o.get('stock', '')
        _expected_vol = o.get('vol', 0)
        _reason = remark.split('-')[0] if '-' in remark else ''
        try:
            _positions = trading.get_trade_detail_data(acct, 'STOCK', 'POSITION')
            for pos in (_positions or []):
                _pos_code = getattr(pos, 'm_strInstrumentID', '') + '.' + getattr(pos, 'm_strExchangeID', '')
                _pos_vol = abs(getattr(pos, 'm_nVolume', 0))
                if _pos_code == _code and _pos_vol > 0:
                    found = True
                    o['status'] = 'filled'
                    o['filled'] = _expected_vol
                    print('[ORDER_POLL][%s] confirmed via POSITION: %s has %d shares' % (remark, _code, _pos_vol))
                    _update_internal_position(_code, _pos_vol, _reason, o)
                    break
        except Exception as e:
            print('[ORDER_POLL][%s] POSITION query failed: %s' % (remark, e))

    # Status transitions and timeout handling
    if found:
        # Check if fully filled
        if o['filled'] >= o.get('vol', 0) and o['status'] != 'filled':
            o['status'] = 'filled'
            print('[ORDER_POLL][%s] fully filled %d/%d' % (remark, o['filled'], o.get('vol', 0)))
        elif o['filled'] > 0 and o['status'] in ('pending', 'ordered'):
            o['status'] = 'partial'
            o['ordered_time'] = now
            print('[ORDER_POLL][%s] partial %d/%d' % (remark, o['filled'], o.get('vol', 0)))
        elif o['status'] == 'pending':
            o['status'] = 'ordered'
            o['ordered_time'] = now
            print('[ORDER_POLL][%s] ordered vol=%d' % (remark, o.get('vol', 0)))

        # Remove if fully filled
        if o['status'] == 'filled':
            _orders.pop(remark, None)
        return

    # Not found in any layer - timeout check
    age = now - o.get('timestamp', now)
    if o['status'] == 'pending' and age > 60:
        o['status'] = 'rejected'
        print('[ORDER_POLL][%s] rejected after %ds (not found in ORDER/DEAL/POSITION)' % (remark, int(age)))
        _orders.pop(remark, None)
        return

    # Ordered/partial + 120s -> cancel entire order
    if o['status'] in ('ordered', 'partial') and age > 120:
        order_id = o.get('order_id', '')
        if order_id:
            try:
                ctx = o.get('context', None)
                if ctx:
                    result = trading.cancel(order_id, o.get('account_id', ''), o.get('account_type', 'STOCK'), ctx)
                    print('[ORDER_POLL][%s] cancel: order_id=%s (%d/%d filled after %ds) result=%s'
                          % (remark, order_id, o['filled'], o.get('vol', 0), int(age), result))
                    o['status'] = 'cancelled'
                    _orders.pop(remark, None)
            except Exception as e:
                print('[ORDER_POLL][%s] cancel failed: %s' % (remark, e))
        else:
            print('[ORDER_POLL][%s] ordered but no order_id' % remark)


def _update_internal_fill(o, remark, delta, traded, vol):
    """Update _internal_positions after fill detected."""
    code = o['stock']
    reason = remark.split('-')[0] if '-' in remark else ''
    if reason == 'BUY':
        if code in _internal_positions:
            _internal_positions[code]['shares'] += delta
        else:
            _internal_positions[code] = {'shares': delta, 'cost': o.get('price', 0), 'name': ''}
        print('[ORDER_POLL][%s] fill +%d -> %d/%d' % (remark, delta, traded, vol))
    elif reason in ('SELL', 'SELL_ALL', 'RISK'):
        if code in _internal_positions:
            _internal_positions[code]['shares'] -= delta
            if _internal_positions[code]['shares'] <= 0:
                del _internal_positions[code]
        print('[ORDER_POLL][%s] fill -%d -> %d/%d' % (remark, delta, traded, vol))


def _update_internal_position(code, pos_vol, reason, o):
    """Update _internal_positions after POSITION-based confirmation."""
    if reason == 'BUY':
        if code not in _internal_positions:
            _internal_positions[code] = {'shares': pos_vol, 'cost': o.get('price', 0), 'name': ''}
        else:
            _internal_positions[code]['shares'] = pos_vol
    elif reason in ('SELL', 'SELL_ALL', 'RISK'):
        if code in _internal_positions:
            _internal_positions[code]['shares'] = pos_vol
        else:
            _internal_positions[code] = {'shares': pos_vol, 'cost': 0, 'name': ''}

# ============================================================
# Callback functions (QMT auto-calls these, no registration needed)
# ============================================================

_orders = {}  # remark -> {status, stock, vol, price, filled, name}

def order_callback(ContextInfo, orderInfo):
    """Order callback - LOGGING ONLY. Status transitions in _do_order_check_single."""
    if not _risk_debug:
        return
    remark = getattr(orderInfo, 'm_strRemark', '')
    code = getattr(orderInfo, 'm_strInstrumentID', '') + '.' + getattr(orderInfo, 'm_strExchangeID', '')
    vol = getattr(orderInfo, 'm_nVolumeTotalOriginal', 0)
    traded = getattr(orderInfo, 'm_nVolumeTraded', 0)
    status = getattr(orderInfo, 'm_nOrderStatus', -1)
    print('[ORDER_CB] %s vol=%d traded=%d status=%d remark=%s' % (code, vol, traded, status, remark))


def deal_callback(ContextInfo, dealInfo):
    """Deal callback - LOGGING ONLY. All accounting in _do_order_check_single."""
    if not _risk_debug:
        return
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
