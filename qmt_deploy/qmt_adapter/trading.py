#coding:gbk
"""
trading.py - QMT Trading Adapter

Wraps passorder + account queries.
"""
import sys


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
    """QMT account operations wrapper."""

    def __init__(self, C):
        self.C = C

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
        """Query trade details."""
        _get_qmt_func()
        from . import trading
        has_func = hasattr(trading, 'get_trade_detail_data') and trading.get_trade_detail_data is not None
        if not has_func:
            print('[QMT] WARNING: get_trade_detail_data not available!')
            return []
        result = trading.get_trade_detail_data(self.account_id, self.account_type, query_type)
        if query_type == 'position':
            print('[QMT] query position: %d results, account=%s type=%s' % (len(result) if result else 0, self.account_id, self.account_type))
            if result:
                for p in result[:3]:
                    print('[QMT]   pos: code=%s vol=%s canUse=%s cost=%s' % (
                        getattr(p, 'm_strStockCode', '?'),
                        getattr(p, 'm_nVolume', '?'),
                        getattr(p, 'm_nCanUseVolume', '?'),
                        getattr(p, 'm_dSettlementPrice', '?')))
        return result

    def get_cash(self):
        """Get available cash."""
        accounts = self._query("account")
        if not accounts:
            print('[QMT] WARNING: get_cash returned 0 accounts')
            return 0
        cash = accounts[0].m_dAvailable
        print('[QMT] get_cash: %.2f (account=%s)' % (cash, self.account_id))
        return cash

    def get_holdings(self):
        """Get current holdings as list of dicts."""
        positions = self._query("position")
        holdings = []
        for p in positions:
            vol = getattr(p, 'm_nVolume', 0)
            if vol > 0:
                holdings.append({
                    'code': getattr(p, 'm_strStockCode', '?'),
                    'shares': vol,
                    'available': getattr(p, 'm_nCanUseVolume', 0),
                    'avg_cost': getattr(p, 'm_dSettlementPrice', 0)
                })
        print('[HOLD] get_holdings: raw=%d filtered=%d' % (len(positions) if positions else 0, len(holdings)))
        return holdings

    def get_position_detail(self, stock_code):
        """Get position detail for a specific stock."""
        positions = self._query("position")
        for p in positions:
            if p.m_strStockCode == stock_code and p.m_nVolume > 0:
                return {
                    'code': p.m_strStockCode,
                    'shares': p.m_nVolume,
                    'available': p.m_nCanUseVolume,
                    'avg_cost': p.m_dSettlementPrice
                }
        return None

    def get_total_value(self):
        """Get total portfolio value."""
        accounts = self._query("account")
        if not accounts:
            return 0
        return accounts[0].m_dStockValue + accounts[0].m_dFundValue

    def buy(self, stock_code, shares, price=-1, reason='BUY'):
        """Buy order."""
        _get_qmt_func()
        from . import trading
        trading.passorder(
            23,
            1101,
            self.account_id,
            stock_code,
            14,
            -1,
            shares,
            reason,
            1,
            reason,
            self.C
        )

    def sell(self, stock_code, shares, price=-1, reason='SELL'):
        """Sell order."""
        _get_qmt_func()
        from . import trading
        trading.passorder(
            24,
            1101,
            self.account_id,
            stock_code,
            14,
            -1,
            shares,
            reason,
            1,
            reason,
            self.C
        )

    def sell_all(self, stock_code, price=-1, reason='SELL_ALL'):
        """Sell all shares of a stock."""
        pos = self.get_position_detail(stock_code)
        if pos and pos['available'] > 0:
            self.sell(stock_code, pos['available'], price, reason)

    def buy_value(self, stock_code, target_value, price, reason='BUY'):
        """Buy by target value (auto-calculate shares)."""
        if price <= 0:
            return
        shares = int(target_value / price / 100) * 100
        if shares > 0:
            self.buy(stock_code, shares, price, reason)
