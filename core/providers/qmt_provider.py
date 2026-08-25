# -*- coding: utf-8 -*-
"""
core/providers/qmt_provider.py — QMT实盘交易Provider
======================================================
封装 qmt_adapter/trading.py 的QmtAccount，
提供与 SimProvider 完全一致的 Provider 接口。

策略代码零改动，只需 trading.set_provider('qmt') 即可切换。

注意: 本文件运行在QMT内置Python 3.6环境中，必须兼容3.6.8。
      编码声明必须是 #coding:gbk（QMT要求）。
"""
#coding:gbk

import json
import logging
from datetime import datetime

from core.trading_provider import TradingProvider

logger = logging.getLogger(__name__)


class QmtProvider(TradingProvider):
    """QMT实盘Provider — 对接QmtAccount"""

    def __init__(self):
        self._acct = None       # QmtAccount实例
        self._trade_log = []    # 本地交易记录（JSON持久化）
        self._log_file = None

    def initialize(self, config):
        """初始化QMT Provider。

        config: {
            'account_id': str,          # QMT资金账号
            'account_type': str,        # 'stock' / 'credit'
            'C': ContextInfo,           # QMT上下文对象
            'log_dir': str,             # 交易日志目录（可选）
        }
        """
        from qmt_adapter.trading import QmtAccount

        account_id = config['account_id']
        account_type = config.get('account_type', 'stock')
        C = config['C']
        log_dir = config.get('log_dir', 'data/trade_logs')

        self._acct = QmtAccount(C, account_id, account_type)
        self._log_dir = log_dir
        self._log_file = '%s/%s_trade_log.json' % (log_dir, account_id)

        # 加载已有交易记录
        self._load_trade_log()

        logger.info("QmtProvider initialized: account=%s", account_id)

    def _load_trade_log(self):
        """加载本地交易记录"""
        try:
            with open(self._log_file, 'r') as f:
                self._trade_log = json.load(f)
        except (IOError, ValueError):
            self._trade_log = []

    def _save_trade_log(self):
        """保存交易记录到JSON"""
        try:
            with open(self._log_file, 'w') as f:
                json.dump(self._trade_log, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error("Failed to save trade log: %s", e)

    def _code_to_qmt(self, code):
        """股票代码转换: 600000 → 600000.SH"""
        code = str(code)
        if '.' in code:
            return code  # 已是QMT格式
        # 简单规则：6开头=上海，0/3开头=深圳
        if code.startswith('6'):
            return code + '.SH'
        else:
            return code + '.SZ'

    def _code_from_qmt(self, qmt_code):
        """QMT代码转回我们的格式: 600000.SH → 600000"""
        if '.' in qmt_code:
            return qmt_code.split('.')[0]
        return qmt_code

    def buy(self, code, shares, price, date, reason='AUTO'):
        """买入。

        Parameters
        ----------
        code : str
            股票代码，如 '600000' 或 '600000.SH'
        shares : int
            股数
        price : float
            委托价格（-1=最新价）
        date : str
            日期（用于记录）
        reason : str
            买入原因

        Returns
        -------
        dict or None
            交易记录，失败返回None
        """
        qmt_code = self._code_to_qmt(code)

        # 向下取整到100股
        shares = int(shares / 100) * 100
        if shares <= 0:
            logger.warning("BUY FAILED: %s shares=%d (too small)", code, shares)
            return None

        # 下单
        ok = self._acct.buy(qmt_code, shares, price, reason)
        if not ok:
            logger.warning("BUY FAILED: %s x %d @ %.2f", code, shares, price)
            return None

        # 记录交易
        trade = {
            'date': date,
            'code': code,
            'action': 'BUY',
            'shares': shares,
            'price': price,
            'reason': reason,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        self._trade_log.append(trade)
        self._save_trade_log()

        logger.info("BUY: %s x %d @ %.2f reason=%s", code, shares, price, reason)
        return trade

    def sell(self, code, shares, price, date, reason='SELL'):
        """卖出。

        Parameters
        ----------
        code : str
            股票代码
        shares : int
            卖出股数
        price : float
            委托价格（-1=最新价）
        date : str
            日期
        reason : str
            卖出原因

        Returns
        -------
        dict or None
            交易记录
        """
        qmt_code = self._code_to_qmt(code)

        # 先查实际持仓
        actual_holdings = self._acct.get_holdings()
        actual_shares = actual_holdings.get(qmt_code, 0)

        if actual_shares <= 0:
            logger.warning("SELL FAILED: %s not held", code)
            return None

        # 实际卖出量 = min(请求量, 持仓量)
        sell_shares = min(shares, actual_shares)
        sell_shares = int(sell_shares / 100) * 100
        if sell_shares <= 0:
            logger.warning("SELL FAILED: %s shares=%d", code, sell_shares)
            return None

        # 下单
        ok = self._acct.sell(qmt_code, sell_shares, price, reason)
        if not ok:
            logger.warning("SELL FAILED: %s x %d @ %.2f", code, sell_shares, price)
            return None

        # 记录交易
        trade = {
            'date': date,
            'code': code,
            'action': 'SELL',
            'shares': sell_shares,
            'price': price,
            'reason': reason,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
        self._trade_log.append(trade)
        self._save_trade_log()

        logger.info("SELL: %s x %d @ %.2f reason=%s", code, sell_shares, price, reason)
        return trade

    def get_positions(self):
        """获取当前持仓。

        Returns
        -------
        dict
            {code: {'shares': int, 'cost_price': float, ...}, ...}
        """
        holdings = self._acct.get_holdings()
        result = {}

        for qmt_code, shares in holdings.items():
            code = self._code_from_qmt(qmt_code)
            # 尝试获取成本价
            detail = self._acct.get_position_detail(qmt_code)
            cost_price = 0.0
            if detail:
                cost_price = detail.get('cost_price', 0)

            result[code] = {
                'shares': shares,
                'cost_price': cost_price,
            }

        return result

    def get_balance(self):
        """获取资金余额。

        Returns
        -------
        dict
            {'cash': float, 'total_value': float}
        """
        cash = self._acct.get_cash()
        return {
            'cash': cash,
            'total_value': cash,  # 实盘不自动算总值，需要调用portfolio_value
        }

    def get_trade_log(self):
        """获取交易记录。

        Returns
        -------
        list of dict
        """
        return list(self._trade_log)

    def portfolio_value(self, prices):
        """计算组合总市值。

        Parameters
        ----------
        prices : dict
            {code: price} 当前价格字典

        Returns
        -------
        float
        """
        positions = self.get_positions()
        total = self._acct.get_cash()

        for code, info in positions.items():
            price = prices.get(code, 0)
            total += info['shares'] * price

        return total

    def save(self):
        """显式保存（QMT Provider自动保存，此方法为空操作）"""
        self._save_trade_log()
