#coding:gbk
"""
trading.py - QMT Trading Adapter

Wraps passorder + account queries.
"""
import sys


def _get_qmt_func():
    """Get QMT built-in functions from caller's global namespace."""
    frame = sys._getframe(2)
    caller_globals = frame.f_globals

    from . import trading
    for name in ['get_trade_detail_data', 'passorder', 'get_last_order_id']:
        if name in caller_globals:
            setattr(trading, name, caller_globals[name])


def _init_functions():
    """Ensure QMT built-in functions are available."""
    from . import trading
    if not hasattr(trading, 'get_trade_detail_data'):
        _get_qmt_func()


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


class QmtAccount(object):
    """QMT account operations wrapper."""

    def __init__(self, C):
        # 1. Try QMT global 'account' variable (from model trading config)
        account = _find_account_from_frames()

        # 2. Fallback to config
        if not account:
            try:
                from .config import ACCOUNT_CONFIG
                account = ACCOUNT_CONFIG.get('account_id', '')
            except Exception:
                pass

        # 3. Last resort
        if not account:
            account = 'SIMTEST'

        self.account_id = account
        self.C = C

    def _query(self, query_type):
        """Query trade details."""
        _init_functions()
        from xtquant.xttype import StockAccount
        account = StockAccount(self.account_id, "STOCK")
        return get_trade_detail_data(account, "stock", query_type)

    def get_cash(self):
        """Get available cash."""
        accounts = self._query("account")
        if not accounts:
            return 0
        return accounts[0].m_dAvailable

    def get_holdings(self):
        """Get current holdings."""
        positions = self._query("stockpositions")
        holdings = []
        for p in positions:
            if p.m_nVolume > 0:
                holdings.append({
                    'code': p.m_strStockCode,
                    'shares': p.m_nVolume,
                    'available': p.m_nCanUseVolume,
                    'avg_cost': p.m_dSettlementPrice
                })
        return holdings

    def get_position_detail(self, stock_code):
        """Get position detail for a specific stock."""
        positions = self._query("stockpositions")
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
        _init_functions()
        from xtquant.xttype import StockAccount
        account = StockAccount(self.account_id, "STOCK")
        code = stock_code.split('.')[0]
        exchange = stock_code.split('.')[1] if '.' in stock_code else 'SH'
        market = 1 if exchange == 'SH' else 0

        passorder(
            23,
            1101,
            account,
            code,
            11,
            price,
            shares,
            reason,
            2,
            reason,
            self.C
        )

    def sell(self, stock_code, shares, price=-1, reason='SELL'):
        """Sell order."""
        _init_functions()
        from xtquant.xttype import StockAccount
        account = StockAccount(self.account_id, "STOCK")
        code = stock_code.split('.')[0]
        exchange = stock_code.split('.')[1] if '.' in stock_code else 'SH'
        market = 1 if exchange == 'SH' else 0

        passorder(
            24,
            1101,
            account,
            code,
            11,
            price,
            shares,
            reason,
            2,
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
