# -*- coding: utf-8 -*-
"""
qmt_adapter/trading.py — QMT交易适配层
========================================
封装passorder下单 + 账户查询，提供和我们SimProvider一致的接口。

注意: 本文件运行在QMT内置Python 3.6环境中，必须兼容3.6.8。
      编码声明必须是 #coding:gbk（QMT要求）。
"""
#coding:gbk


# ── 交易常量 ─────────────────────────────────────────────────────
STOCK_BUY = 23       # 买入
STOCK_SELL = 24      # 卖出
ORDER_TYPE = 1101    # 普通交易
PRICE_TYPE = 5       # 最新价
# PRICE_TYPE = 14    # 对手价（对方一档价格）


class QmtAccount(object):
    """QMT账户操作封装。

    Usage::

        acct = QmtAccount(C, 'testS', 'stock')
        cash = acct.get_cash()
        holdings = acct.get_holdings()
        acct.buy('600000.SH', 100, reason='TEST')
    """

    def __init__(self, C, account_id='testS', account_type='stock'):
        """
        Parameters
        ----------
        C : ContextInfo
            QMT策略上下文
        account_id : str
            资金账号
        account_type : str
            'stock' / 'credit'
        """
        self.C = C
        self.account_id = account_id
        self.account_type = account_type
        # 买入/卖出代码（两融不同）
        if account_type == 'stock':
            self.buy_code = STOCK_BUY
            self.sell_code = STOCK_SELL
        else:
            self.buy_code = 33
            self.sell_code = 34

    def _query(self, query_type):
        """查询交易明细。

        Parameters
        ----------
        query_type : str
            'account' / 'position' / 'deal' / 'order'

        Returns
        -------
        list
            QMT返回的对象列表
        """
        return get_trade_detail_data(
            self.account_id, self.account_type, query_type
        )

    def get_cash(self):
        """获取可用资金。

        Returns
        -------
        float
            可用资金（元）
        """
        accounts = self._query('account')
        if not accounts:
            return 0.0
        return float(accounts[0].m_dAvailable)

    def get_holdings(self):
        """获取当前持仓。

        Returns
        -------
        dict
            {stock_code: shares} 如 {'600000.SH': 1000}
        """
        positions = self._query('position')
        result = {}
        for p in positions:
            code = p.m_strInstrumentID + '.' + p.m_strExchangeID
            vol = p.m_nCanUseVolume  # 可用数量
            if vol > 0:
                result[code] = vol
        return result

    def get_position_detail(self, stock_code):
        """获取单只股票的持仓详情。

        Returns
        -------
        dict or None
            {'shares': int, 'cost_price': float, 'market_value': float}
        """
        positions = self._query('position')
        for p in positions:
            code = p.m_strInstrumentID + '.' + p.m_strExchangeID
            if code == stock_code:
                return {
                    'shares': p.m_nCanUseVolume,
                    'cost_price': getattr(p, 'm_dSettlementPrice', 0),
                    'market_value': getattr(p, 'm_dMarketValue', 0),
                }
        return None

    def buy(self, stock_code, shares, price=-1, reason='BUY'):
        """买入下单。

        Parameters
        ----------
        stock_code : str
            股票代码, 如 '600000.SH'
        shares : int
            买入股数（会向下取整到100的整数倍）
        price : float
            委托价格, 0=最新价
        reason : str
            备注

        Returns
        -------
        bool
            是否成功发起委托
        """
        shares = int(shares / 100) * 100
        if shares <= 0:
            return False

        passorder(
            self.buy_code,           # opType: 买入
            ORDER_TYPE,              # orderType: 普通交易
            self.account_id,         # accountid
            stock_code,              # orderCode
            PRICE_TYPE,              # prType: 最新价
            price,                   # price
            shares,                  # volume
            reason,                  # strategyName
            0,                       # quickTrade: 0=逐K线生效
            reason,                  # userOrderId
            self.C,                  # ContextInfo
        )
        return True

    def sell(self, stock_code, shares, price=-1, reason='SELL'):
        """卖出下单。

        Parameters
        ----------
        stock_code : str
        shares : int
            卖出股数
        price : float
            委托价格, 0=最新价
        reason : str

        Returns
        -------
        bool
        """
        if shares <= 0:
            return False

        passorder(
            self.sell_code,
            ORDER_TYPE,
            self.account_id,
            stock_code,
            PRICE_TYPE,
            price,
            shares,
            reason,
            0,
            reason,
            self.C,
        )
        return True

    def sell_all(self, stock_code, price=-1, reason='SELL_ALL'):
        """全仓卖出。

        Returns
        -------
        bool
        """
        holdings = self.get_holdings()
        if stock_code not in holdings:
            return False
        return self.sell(stock_code, holdings[stock_code], price, reason)

    def buy_value(self, stock_code, target_value, price, reason='BUY'):
        """按目标金额买入（自动计算股数）。

        Parameters
        ----------
        stock_code : str
        target_value : float
            目标买入金额（元）
        price : float
            当前价格
        reason : str

        Returns
        -------
        bool
        """
        if price <= 0:
            return False
        shares = int(target_value / price / 100) * 100
        return self.buy(stock_code, shares, price, reason)
