#coding:gbk
"""
trading.py - QMT Trading Adapter

Wraps passorder + account queries.
"""
from xtquant.xttype import StockAccount

# Built-in functions injected by qmt_runner
get_trade_detail_data = None
passorder = None
get_last_order_id = None


def _get_qmt_func():
    """Get QMT built-in functions from caller's global namespace."""
    global get_trade_detail_data, passorder, get_last_order_id
    if get_trade_detail_data is not None:
        return
    import sys
    frame = sys._getframe(2)
    caller_globals = frame.f_globals
    if 'get_trade_detail_data' in caller_globals:
        get_trade_detail_data = caller_globals['get_trade_detail_data']
    if 'passorder' in caller_globals:
        passorder = caller_globals['passorder']
    if 'get_last_order_id' in caller_globals:
        get_last_order_id = caller_globals['get_last_order_id']


class QmtAccount(object):
    """QMT Account wrapper."""

    def __init__(self, C):
        _get_qmt_func()
        # Get account_id from QMT global scope, not from C
        import sys
        frame = sys._getframe(1)
        caller_globals = frame.f_globals
        if 'account' in caller_globals:
            self.C = C
        self.account_id = caller_globals['account']
        elif 'account_id' in caller_globals:
            self.account_id = caller_globals['account_id']
        elif hasattr(C, 'account_id'):
            self.account_id = C.account_id
        else:
            raise ValueError("Cannot find account ID. Set 'account' in QMT strategy config.")

    def _query(self, query_type):
        """Query trade details."""
        account = StockAccount(self.account_id, "STOCK")
        return get_trade_detail_data(account, "trade", query_type, 1)

    def get_cash(self):
        """Get available cash."""
        accounts = get_trade_detail_data(
            StockAccount(self.account_id, "STOCK"), "trade", "account", 1
        )
        if accounts:
            return accounts[0].m_dAvailable
        return 0

    def get_holdings(self):
        """Get current holdings."""
        positions = self._query("stockpositions")
        result = {}
        for p in positions:
            code = p.m_strInstrumentID
            exchange = p.m_strExchangeID
            full_code = "{}.{}".format(code, exchange)
            result[full_code] = {
                'shares': p.m_nVolume,
                'available': p.m_nCanUseVolume,
                'avg_cost': p.m_dSettlementPrice
            }
        return result

    def get_position_detail(self, stock_code):
        """Get position detail for one stock."""
        holdings = self.get_holdings()
        return holdings.get(stock_code, None)

    def buy(self, stock_code, shares, price=-1, reason='BUY'):
        """Buy order."""
        account = StockAccount(self.account_id, "STOCK")
        code = stock_code.split('.')[0]
        exchange = stock_code.split('.')[1] if '.' in stock_code else 'SH'
        market = 1 if exchange == 'SH' else 0

        passorder(
            23,                 # order type: buy
            1101,               # account type
            account,            # account
            code,               # stock code (no suffix)
            11,                 # exchange type
            price,              # price (-1 = market)
            shares,             # volume
            reason,             # strategy name
            2,                  # price type
            reason,             # order comment
            self.C               # ContextInfo
        )

    def sell(self, stock_code, shares, price=-1, reason='SELL'):
        """Sell order."""
        account = StockAccount(self.account_id, "STOCK")
        code = stock_code.split('.')[0]
        exchange = stock_code.split('.')[1] if '.' in stock_code else 'SH'
        market = 1 if exchange == 'SH' else 0

        passorder(
            24,                 # order type: sell
            1101,               # account type
            account,            # account
            code,               # stock code (no suffix)
            11,                 # exchange type
            price,              # price (-1 = market)
            shares,             # volume
            reason,             # strategy name
            2,                  # price type
            reason,             # order comment
            self.C               # ContextInfo
        )

    def sell_all(self, stock_code, price=-1, reason='SELL_ALL'):
        """Sell all shares of a stock."""
        pos = self.get_position_detail(stock_code)
        if pos and pos['shares'] > 0:
            self.sell(stock_code, pos['shares'], price, reason)

    def buy_value(self, stock_code, target_value, price, reason='BUY'):
        """Buy by target value (auto-calculate shares)."""
        if price <= 0:
            return
        shares = int(target_value / price / 100) * 100
        if shares >= 100:
            self.buy(stock_code, shares, price, reason)
