#coding:gbk
"""
trading.py - QMT Trading Adapter

Wraps passorder + account queries, provides same interface as SimProvider.
NOTE: Runs in QMT built-in Python 3.6, must be 3.6.8 compatible.
Encoding declaration must be #coding:gbk (QMT requirement).
"""
import sys


def _get_qmt_func():
    """Get QMT built-in functions from caller global namespace."""
    frame = sys._getframe(1)
    caller_globals = frame.f_globals
    
    needed = ['get_trade_detail_data', 'passorder', 'get_last_order_id']
    for name in needed:
        if name in caller_globals:
            globals()[name] = caller_globals[name]


# -- Trading Constants
STOCK_BUY = 23       # Buy
STOCK_SELL = 24      # Sell
ORDER_TYPE = 1101    # Normal order
PRICE_TYPE = 5       # Latest price
# PRICE_TYPE = 14    # Opponent price


class QmtAccount(object):
    """QMT Account operations wrapper."""
    
    def __init__(self, C):
        self.C = C
        self.account_id = C.account_id
        self.stock_account = C.stock_account
        self.buy_code = STOCK_BUY
        self.sell_code = STOCK_SELL
    
    def _query(self, query_type):
        """Query trade details."""
        _get_qmt_func()
        from xtquant.xttype import StockAccount
        account = StockAccount(self.account_id, "STOCK")
        return get_trade_detail_data(account, "trade", "", query_type)
    
    def get_cash(self):
        """Get available cash."""
        _get_qmt_func()
        from xtquant.xttype import StockAccount
        account = StockAccount(self.account_id, "STOCK")
        details = get_trade_detail_data(account, "asset", "", 0)
        if details:
            return details[0].m_nCash
        return 0
    
    def get_holdings(self):
        """Get current positions."""
        positions = {}
        details = self._query(1)
        for p in details:
            code = p.m_strInstrumentID
            vol = p.m_nCanUseVolume  # Available volume
            if vol > 0:
                positions[code] = vol
        return positions
    
    def get_position_detail(self, stock_code):
        """Get position details for single stock."""
        _get_qmt_func()
        from xtquant.xttype import StockAccount
        account = StockAccount(self.account_id, "STOCK")
        positions = get_trade_detail_data(account, "stock", stock_code, 0)
        
        for p in positions:
            if p.m_strInstrumentID == stock_code:
                return {
                    'shares': p.m_nVolume,
                    'available': p.m_nCanUseVolume,
                    'cost_price': p.m_dSettlementPrice,
                    'market_value': p.m_dMarketValue,
                }
        return None
    
    def buy(self, stock_code, shares, price=-1, reason='BUY'):
        """Place buy order."""
        if shares <= 0:
            return False
        
        shares = (shares // 100) * 100
        if shares <= 0:
            return False
        
        _get_qmt_func()
        from xtquant.xttype import StockAccount
        account = StockAccount(self.account_id, "STOCK")
        
        if price <= 0:
            from .data import get_close_price
            price = get_close_price(self.C, stock_code)
            if price <= 0:
                return False
        
        passorder(
            self.buy_code,           # opType: buy
            ORDER_TYPE,              # orderType: normal
            self.account_id,         # account
            stock_code,              # stock code
            0,                       # exchanged
            shares,                  # volume
            price,                   # price
            reason,                  # remark
            PRICE_TYPE,              # prType: latest
            [],                      # orders
            0,                       # quickTrade: per bar
        )
        return True
    
    def sell(self, stock_code, shares, price=-1, reason='SELL'):
        """Place sell order."""
        if shares <= 0:
            return False
        
        _get_qmt_func()
        from xtquant.xttype import StockAccount
        account = StockAccount(self.account_id, "STOCK")
        
        if price <= 0:
            from .data import get_close_price
            price = get_close_price(self.C, stock_code)
            if price <= 0:
                return False
        
        passorder(
            self.sell_code,          # opType: sell
            ORDER_TYPE,              # orderType: normal
            self.account_id,         # account
            stock_code,              # stock code
            0,                       # exchanged
            shares,                  # volume
            price,                   # price
            reason,                  # remark
            PRICE_TYPE,              # prType: latest
            [],                      # orders
            0,                       # quickTrade: per bar
        )
        return True
    
    def sell_all(self, stock_code, price=-1, reason='SELL_ALL'):
        """Sell all shares."""
        holdings = self.get_holdings()
        if stock_code in holdings:
            return self.sell(stock_code, holdings[stock_code], price, reason)
        return False
    
    def buy_value(self, stock_code, target_value, price, reason='BUY'):
        """Buy by target amount (auto-calc shares)."""
        if price <= 0:
            return False
        
        shares = int(target_value / price / 100) * 100
        if shares <= 0:
            return False
        
        return self.buy(stock_code, shares, price, reason)
