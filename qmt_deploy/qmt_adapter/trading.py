#coding:gbk
"""
trading.py - QMT Trading Adapter

Based on official ThinkTrader API docs (v3.3.6):
- get_trade_detail_data: 'POSITION'/'ORDER'/'DEAL' (uppercase)
- passorder: prType=14 (counterparty), quickTrade=1 (fast)
- Position fields: m_dOpenPrice (not m_dSettlementPrice)
- order_callback/deal_callback for trade confirmation
"""
import sys
import datetime


# Module-level order tracking
_orders = {}  # remark -> {status, code, shares, price, filled, ts}
_risk_debug = False


def set_risk_debug(flag):
    global _risk_debug
    _risk_debug = flag


def _get_qmt_func():
    """Get QMT built-in functions by walking up ALL frames."""
    frame = sys._getframe(1)
    while frame is not None:
        g = frame.f_globals
        if 'get_trade_detail_data' in g:
            from . import trading
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

    def _query(self, query_type):
        """Query trade details.

        Official API: get_trade_detail_data(accountID, strAccountType, strDatatype)
        strDatatype must be UPPERCASE: 'POSITION', 'ORDER', 'DEAL', 'ACCOUNT', 'TASK'
        """
        _get_qmt_func()
        from . import trading
        has_func = hasattr(trading, 'get_trade_detail_data') and trading.get_trade_detail_data is not None
        if not has_func:
            if _risk_debug:
                print('[QMT] WARNING: get_trade_detail_data not available!')
            return []
        # Official API uses UPPERCASE datatype
        result = trading.get_trade_detail_data(self.account_id, self.account_type, query_type.upper())
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
            print('[HOLD] get_holdings: api=%d internal=%d total=%d' % (
                len(positions), len(self._internal_positions), len(holdings)))
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
          prType=14 (counterparty price), quickTrade=1 (fast trigger)
        """
        _get_qmt_func()
        from . import trading

        # Generate unique userOrderId for callback matching
        now = datetime.datetime.now()
        remark = '%s-%s-%s' % (reason, stock_code.split('.')[0], now.strftime('%H%M%S'))

        trading.passorder(
            23,                     # opType: stock buy
            1101,                   # orderType: single stock, shares
            self.account_id,        # account
            stock_code,             # orderCode
            14,                     # prType: counterparty price (counterparty)
            -1,                     # price: -1 for prType=14
            shares,                 # volume
            'V61C',                 # strategyName
            1,                      # quickTrade: 1=fast (official docs)
            remark,                 # userOrderId -> m_strRemark in callback
            self.C                  # ContextInfo
        )

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
          prType=14 (counterparty price), quickTrade=1 (fast trigger)
        """
        _get_qmt_func()
        from . import trading

        now = datetime.datetime.now()
        remark = '%s-%s-%s' % (reason, stock_code.split('.')[0], now.strftime('%H%M%S'))

        trading.passorder(
            24,                     # opType: stock sell
            1101,                   # orderType: single stock, shares
            self.account_id,        # account
            stock_code,             # orderCode
            14,                     # prType: counterparty price
            -1,                     # price: -1 for prType=14
            shares,                 # volume
            'V61C',                 # strategyName
            1,                      # quickTrade: 1=fast
            remark,                 # userOrderId
            self.C                  # ContextInfo
        )

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
            print("[BUY] SKIP %s: amount=%.0f price=%.2f -> shares=0" % (stock_code, target_value, price))
        return None


# ============================================================
# Callback functions (QMT auto-calls these, no registration needed)
# ============================================================

def order_callback(ContextInfo, orderInfo):
    """order callback - called when order status changes.

    Official fields:
      m_strInstrumentID     - code (without suffix)
      m_strExchangeID       - exchange (SH/SZ)
      m_strRemark           - userOrderId (our unique identifier)
      m_nVolumeTotalOriginal - total order volume
      m_nVolumeTraded       - filled volume
      m_dTradedPrice        - avg fill price
      m_nOrderStatus        - order status
      m_nOffsetFlag         - direction (48=buy, 49=sell)
    """
    remark = getattr(orderInfo, 'm_strRemark', '')
    code = getattr(orderInfo, 'm_strInstrumentID', '') + '.' + getattr(orderInfo, 'm_strExchangeID', '')
    vol = getattr(orderInfo, 'm_nVolumeTotalOriginal', 0)
    traded = getattr(orderInfo, 'm_nVolumeTraded', 0)
    status = getattr(orderInfo, 'm_nOrderStatus', -1)

    if _risk_debug:
        print('[ORDER] %s vol=%d traded=%d status=%d remark=%s' % (code, vol, traded, status, remark))


def deal_callback(ContextInfo, dealInfo):
    """deal callback - called when order is (partially) filled.

    Official fields:
      m_strInstrumentID     - code (without suffix)
      m_strExchangeID       - exchange (SH/SZ)
      m_strInstrumentName   - stock name
      m_dPrice              - fill price
      m_nVolume             - fill volume
      m_dTradeAmount        - fill amount (CNY)
      m_nOffsetFlag         - direction (48=buy, 49=sell)
      m_strRemark           - userOrderId
      m_strTradeDate        - fill date
      m_strTradeTime        - fill time
    """
    remark = getattr(dealInfo, 'm_strRemark', '')
    code = getattr(dealInfo, 'm_strInstrumentID', '') + '.' + getattr(dealInfo, 'm_strExchangeID', '')
    name = getattr(dealInfo, 'm_strInstrumentName', '')
    price = getattr(dealInfo, 'm_dPrice', 0)
    vol = getattr(dealInfo, 'm_nVolume', 0)
    amount = getattr(dealInfo, 'm_dTradeAmount', 0)
    direction = getattr(dealInfo, 'm_nOffsetFlag', 0)

    if _risk_debug:
        dir_str = 'BUY' if direction == 48 else 'SELL'
        print('[DEAL] %s %s %s %d股 @ %.2f = %.0f元 remark=%s' % (
            code, name, dir_str, vol, price, amount, remark))
