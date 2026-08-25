#coding:gbk
"""
qmt_runner.py - QMT通用运行器
封装QMT环境的初始化、全局状态、风控检查等通用逻辑。
策略文件只需实现选股逻辑。
"""
import sys
import numpy as np
import pandas as pd
from datetime import datetime

from qmt_adapter.trading import QmtAccount
from qmt_adapter.config import MARKET_CONFIG, ACCOUNT_CONFIG
from qmt_data import FLOAT_SHARES, ZZ1800_STOCKS


# 全局状态
class G():
    pass
g = G()
g.initialized = False
g.holdings = {}
g.day_count = 0
g.last_rebalance_day = -999


def qmt_init(C):
    """QMT通用初始化"""
    # 注入QMT内置函数到trading模块
    import qmt_adapter.trading as _trading
    _trading.get_trade_detail_data = get_trade_detail_data
    _trading.passorder = passorder
    _trading.get_last_order_id = get_last_order_id
    
    g.initialized = True
    print('[INIT] QMT runner ready')


def check_risk(C, acct, bar_date, today, params):
    """通用风控检查：止损/止盈/到期"""
    stop_loss = params.get('STOP_LOSS', -0.08)
    take_profit = params.get('TAKE_PROFIT', 0.25)
    hold_days_max = params.get('HOLD_DAYS_MAX', 10)
    
    sell_codes = []
    for code, info in list(g.holdings.items()):
        data = C.get_market_data_ex(['close'], [code], period='1d', count=1, subscribe=False)
        if code not in data or len(data[code]) == 0:
            continue
        current_price = data[code]['close'].values[-1]
        cost = info.get('cost', 0)
        if cost <= 0:
            continue
        
        pnl = (current_price - cost) / cost
        hold_days = g.day_count - info.get('entry_day', g.day_count)
        
        if pnl <= stop_loss:
            sell_codes.append((code, 'STOP_LOSS'))
        elif pnl >= take_profit:
            sell_codes.append((code, 'TAKE_PROFIT'))
        elif hold_days >= hold_days_max:
            sell_codes.append((code, 'HOLD_DAYS'))
    
    for code, reason in sell_codes:
        if code in g.holdings:
            shares = g.holdings[code]['shares']
            acct.sell(code, shares, price=-1, reason=reason)
            print('[SELL] %s %s %s' % (code, reason, bar_date))
            del g.holdings[code]


def execute_buy(C, acct, candidates, cash, bar_date, max_holdings, reason='AUTO'):
    """通用买入执行"""
    buy_count = min(max_holdings - len(g.holdings), len(candidates))
    if buy_count <= 0:
        return cash
    
    print('[BUY] buy_count=%d, cash=%.2f' % (buy_count, cash))
    
    for i in range(buy_count):
        code, score = candidates[i]
        data = C.get_market_data_ex(['close'], [code], period='1d', count=1, subscribe=False)
        if code not in data or len(data[code]) == 0:
            print('[BUY] %s no price, skip' % code)
            continue
        price = data[code]['close'].values[-1]
        
        available = cash * 0.95 / (buy_count - i)
        shares = int(available / price / 100) * 100
        print('[BUY] %s price=%.2f, shares=%d' % (code, price, shares))
        
        if shares <= 0:
            continue
        
        acct.buy(code, shares, price=price, reason=reason)
        g.holdings[code] = {'shares': shares, 'cost': price, 'entry_day': g.day_count}
        print('[BUY] OK: %s %d @%.2f' % (code, shares, price))
        cash -= shares * price
    
    return cash


def get_market_data(C, stock_list, count=None):
    """获取行情数据"""
    if count is None:
        count = MARKET_CONFIG['count']
    return C.get_market_data_ex(
        ['open', 'high', 'low', 'close', 'volume', 'amount'],
        stock_list, period=MARKET_CONFIG['period'],
        count=count, subscribe=MARKET_CONFIG['subscribe'],
    )


def is_rebalance_time(days_since_rebalance, rebalance_days):
    """判断是否调仓日"""
    return days_since_rebalance >= rebalance_days
