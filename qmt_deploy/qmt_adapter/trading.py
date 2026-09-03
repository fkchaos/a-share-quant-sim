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

    def buy(self, stock_code, shares, price=-1, reason='BUY'):
        """Buy order.

        passorder params (official):
          opType=23 (stock buy), orderType=1101 (single, shares),
          prType=14 (counterparty price), quickTrade=0 (backtest mode)
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
            5,                      # prType: latest price (5=最新价)
            -1,                     # price: -1 ignored when prType != 11
            shares,                 # volume
            'V61C',                 # strategyName
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
            }

        if _risk_debug:
            print('[BUY] passorder sent: %s %d shares remark=%s' % (stock_code, shares, remark))

        if _risk_debug:
            print('[SELL] passorder sent: %s %d shares remark=%s' % (stock_code, shares, remark))

        # Internal position tracking (for backtest)
        if stock_code in self._internal_positions:
            old = self._internal_positions[stock_code]
            old_shares = old['shares']
            old_cost = old['cost']
            new_shares = old_shares + shares
            new_cost = (old_cost * old_shares + price * shares) / new_shares if new_shares > 0 else 0
            self._internal_positions[stock_code] = {
                'shares': new_shares,
                'cost': new_cost,
                'name': old.get('name', ''),
            }
        else:
            self._internal_positions[stock_code] = {
                'shares': shares,
                'cost': price if price > 0 else 0,
                'name': '',
            }

        return remark

    def sell(self, stock_code, shares, price=-1, reason='SELL'):
        """Sell order.

        passorder params (official):
          opType=24 (stock sell), orderType=1101 (single, shares),
          prType=14 (counterparty price), quickTrade=0 (backtest mode)
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
            5,                      # prType: latest price (5=最新价)
            -1,                     # price: -1 ignored when prType != 11
            shares,                 # volume
            'V61C',                 # strategyName
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
            }

        if _risk_debug:
            print('[BUY] passorder sent: %s %d shares remark=%s' % (stock_code, shares, remark))

        if _risk_debug:
            print('[SELL] passorder sent: %s %d shares remark=%s' % (stock_code, shares, remark))

        # Internal position tracking (for backtest)
        if stock_code in self._internal_positions:
            self._internal_positions[stock_code]['shares'] -= shares
            if self._internal_positions[stock_code]['shares'] <= 0:
                del self._internal_positions[stock_code]

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
# Callback functions (QMT auto-calls these, no registration needed)
# ============================================================

_orders = {}  # remark -> {status, stock, vol, price, filled, name}

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
            # Update position JSON
            from . import qmt_runner
            strategy_name = remark.split('-')[0].lower() if '-' in remark else 'v61c'
            if direction == 48:  # BUY
                qmt_runner.strategy_buy(strategy_name, code, o['vol'], price, '')
                print('[DEAL_CB] position JSON updated: BUY %s %d @ %.2f' % (code, o['vol'], price))
            else:  # SELL
                qmt_runner.strategy_sell(strategy_name, code, o['vol'])
                print('[DEAL_CB] position JSON updated: SELL %s %d' % (code, o['vol']))
        else:
            print('[DEAL_CB] %s partial fill: %d/%d, waiting...' % (code, o['filled'], o['vol']))
    else:
        print('[DEAL_CB] %s (unknown remark: %s)' % (code, remark))
