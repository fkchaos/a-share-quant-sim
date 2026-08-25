#coding:gbk
"""
v61c_qmt.py - v61c QMT实盘版
换手率+小市值，ZZ1800股票池，静态数据。

Python 3.6.8兼容。
"""
import sys
import os
import numpy as np
import pandas as pd
from datetime import datetime

from qmt_adapter.trading import QmtAccount
from qmt_adapter.qmt_data import FLOAT_SHARES, ZZ1800_STOCKS

# 全局状态（QMT要求）
class G():
    pass
g = G()
g.initialized = False
g.holdings = {}  # {code: {'shares': n, 'cost': p, 'entry_day': d}}
g.day_count = 0
g.last_rebalance_day = -999

# 策略参数
STOP_LOSS = -0.08
TAKE_PROFIT = 0.25
HOLD_DAYS_MAX = 5
SELL_OUT_OF = 15
MAX_HOLDINGS = 5
REBALANCE_DAYS = 5
ACCOUNT_ID = 'testS'
ACCOUNT_TYPE = 'stock'


def init(C):
    """QMT初始化"""
    print('[INIT] v61c QMT starting...')
    
    # 测试数据加载
    from qmt_adapter.qmt_data import FLOAT_SHARES, ZZ1800_STOCKS
    print('[INIT] stocks=%d, float_shares=%d' % (len(ZZ1800_STOCKS), len(FLOAT_SHARES)))
    
    g.initialized = True
    print('[INIT] done')


def handlebar(C):
    """QMT主循环 - 每根K线调用"""
    if not g.initialized:
        return
    
    # 获取当前日期
    bar_date = timetag_to_datetime(C.get_bar_timetag(C.barpos), '%Y%m%d%H%M%S')
    today = bar_date[:8]  # YYYYMMDD
    
    # 回测模式：只在最后一根执行
    g.day_count += 1
    
    # 交易时间检查（实盘用）
    now = datetime.now()
    now_time = now.strftime('%H%M%S')
    if now_time < '093000' or now_time > '150000':
        return
    
    print('[%s] day=%d holdings=%d' % (today, g.day_count, len(g.holdings)))
    
    # 获取账户信息
    acct = QmtAccount(C, ACCOUNT_ID, ACCOUNT_TYPE)
    cash = acct.get_cash()
    
    # 风控检查（止损/止盈/到期）
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
        
        if pnl <= STOP_LOSS:
            sell_codes.append((code, 'STOP_LOSS'))
        elif pnl >= TAKE_PROFIT:
            sell_codes.append((code, 'TAKE_PROFIT'))
        elif hold_days >= HOLD_DAYS_MAX:
            sell_codes.append((code, 'HOLD_DAYS'))
    
    # 执行卖出
    for code, reason in sell_codes:
        if code in g.holdings:
            shares = g.holdings[code]['shares']
            acct.sell(code, shares, price=-1, reason=reason)
            print('[SELL] %s %s %s' % (code, reason, bar_date))
            del g.holdings[code]
    
    # 选股（调仓日执行）
    days_since_rebalance = g.day_count - g.last_rebalance_day
    if days_since_rebalance < REBALANCE_DAYS:
        return
    
    if len(g.holdings) >= MAX_HOLDINGS:
        return
    
    # 获取股票池行情
    stock_list = ZZ1800_STOCKS[:200]  # 先取前200只测试
    data = C.get_market_data_ex(
        ['open', 'high', 'low', 'close', 'volume', 'amount'],
        stock_list,
        period='1d',
        count=30,
        subscribe=False,
    )
    
    # 计算因子并选股
    candidates = []
    for code in stock_list:
        if code in g.holdings:
            continue
        if code not in data or len(data[code]) < 20:
            continue
        
        df = data[code]
        close = df['close'].values
        volume = df['volume'].values
        amount = df['amount'].values
        
        # 流通股本
        float_sh = FLOAT_SHARES.get(code, 0)
        if float_sh <= 0:
            continue
        
        # 换手率（负向）
        turnover = volume * 100.0 / float_sh
        avg_turnover = np.nanmean(turnover[-5:])
        
        # 市值（负向）
        market_cap = close[-1] * float_sh
        
        # 综合评分（低换手+小市值=高分）
        score = -avg_turnover * 0.5 - market_cap * 0.5e-12
        candidates.append((code, score))
    
    # 按评分排序
    candidates.sort(key=lambda x: x[1], reverse=True)
    
    # 买入
    buy_count = min(MAX_HOLDINGS - len(g.holdings), len(candidates))
    for i in range(buy_count):
        code, score = candidates[i]
        data = C.get_market_data_ex(['close'], [code], period='1d', count=1, subscribe=False)
        if code not in data or len(data[code]) == 0:
            continue
        price = data[code]['close'].values[-1]
        
        # 计算买入数量
        available = cash * 0.95 / (buy_count - i)
        shares = int(available / price / 100) * 100
        if shares <= 0:
            continue
        
        acct.buy(code, shares, price=price, reason='AUTO')
        g.holdings[code] = {'shares': shares, 'cost': price, 'entry_day': g.day_count}
        print('[BUY] %s %d @%.2f %s' % (code, shares, price, bar_date))
        cash -= shares * price
    
    g.last_rebalance_day = g.day_count
