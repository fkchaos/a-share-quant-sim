# -*- coding: utf-8 -*-
"""
core/providers/sim_provider.py — 模拟盘交易Provider
====================================================
封装 core/account.py 的现有逻辑，提供统一的Provider接口。
这是默认的交易执行器，零改动迁移。

Python 3.6.8 兼容。
"""

import os
import sys
import json
import logging
from datetime import datetime

from core.trading_provider import TradingProvider
from core.account import (
    PortfolioState, buy as _buy, sell as _sell,
    portfolio_value as _portfolio_value, compute_buy_shares,
)
from core.config import TradingCosts

logger = logging.getLogger(__name__)


class SimProvider(TradingProvider):
    """模拟盘Provider — 基于JSON持久化"""

    def __init__(self):
        self.state = None  # type: PortfolioState
        self.account_id = None
        self.portfolio_dir = None
        self._costs = TradingCosts()

    def initialize(self, config):
        """初始化模拟盘
        config: {
            'account_id': str,
            'portfolio_dir': str,
            'initial_cash': float (可选，默认100000),
        }
        """
        self.account_id = config['account_id']
        self.portfolio_dir = config.get('portfolio_dir', 'data/portfolio')
        initial_cash = config.get('initial_cash', 100_000)

        os.makedirs(self.portfolio_dir, exist_ok=True)
        self.state = self._load_state(initial_cash)
        logger.info("SimProvider initialized: account=%s, cash=%.0f",
                     self.account_id, self.state.cash)

    def _portfolio_file(self):
        return os.path.join(self.portfolio_dir,
                            '{}.json'.format(self.account_id))

    def _load_state(self, initial_cash):
        """从JSON文件加载状态，不存在则新建"""
        pf = self._portfolio_file()
        if os.path.exists(pf):
            with open(pf, 'r') as f:
                data = json.load(f)
            state = PortfolioState(
                cash=data.get('cash', initial_cash),
                initial_capital=data.get('initial_capital', initial_cash),
                holdings=data.get('holdings', {}),
                trade_log=data.get('trade_log', []),
                nav_history=data.get('nav_history', []),
            )
            return state
        return PortfolioState(cash=initial_cash, initial_capital=initial_cash)

    def _save_state(self):
        """保存状态到JSON"""
        data = {
            'cash': self.state.cash,
            'initial_capital': self.state.initial_capital,
            'holdings': self.state.holdings,
            'trade_log': self.state.trade_log,
            'nav_history': self.state.nav_history,
        }
        with open(self._portfolio_file(), 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def buy(self, code, shares, price, date, reason='AUTO'):
        """买入 — 调用core.account.buy()"""
        new_state = _buy(
            self.state, code, price, date,
            shares=shares,
        )
        if new_state is self.state:
            logger.warning("BUY FAILED: %s x %d @ %.2f", code, shares, price)
            return None

        self.state = new_state
        self._save_state()

        trade = self.state.trade_log[-1]
        logger.info("BUY: %s x %d @ %.2f cost=%.0f comm=%.0f",
                     code, shares, price,
                     trade.get('cost', 0), trade.get('commission', 0))
        return trade

    def sell(self, code, shares, price, date, reason='SELL'):
        """卖出 — 调用core.account.sell()
        
        注意: core.account.sell()固定全仓卖出（不接受shares参数）。
        如果传入的shares < 持仓量，需要特殊处理。
        """
        # 如果指定了部分卖出，需要分两步：
        # 1. 先全仓卖出
        # 2. 再买回剩余部分
        # 但core.account.sell()不支持部分卖出，所以暂时全仓卖出
        if code not in self.state.holdings:
            return None

        held = self.state.holdings[code].get('shares', 0)
        if shares < held:
            # 部分卖出：先全仓卖，再买回差额
            new_state = _sell(self.state, code, price, date)
            if new_state is self.state:
                return None
            buyback_shares = held - shares
            new_state = _buy(new_state, code, price, date, shares=buyback_shares)
            if new_state is self.state:
                # 买回失败，仍然全仓卖出
                pass
            self.state = new_state
        else:
            # 全仓卖出
            new_state = _sell(self.state, code, price, date)
            if new_state is self.state:
                return None
            self.state = new_state

        self._save_state()

        trade = self.state.trade_log[-1]
        logger.info("SELL: %s x %d @ %.2f reason=%s",
                     code, shares, price, reason)
        return trade

    def get_positions(self):
        """获取持仓"""
        return dict(self.state.holdings)

    def get_balance(self):
        """获取资金"""
        return {
            'cash': self.state.cash,
            'initial_capital': self.state.initial_capital,
        }

    def get_trade_log(self):
        """获取交易记录"""
        return list(self.state.trade_log)

    def portfolio_value(self, prices):
        """计算组合总市值"""
        return _portfolio_value(self.state, prices)

    def save(self):
        """显式保存"""
        self._save_state()
