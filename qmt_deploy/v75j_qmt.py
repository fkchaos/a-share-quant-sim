#coding:gbk
"""
v75j_qmt.py - v75j QMT实盘版
流动性单因子 + 广度过滤，科技板块专用。

Python 3.6.8兼容。
"""
import sys
import os
import numpy as np
import pandas as pd
from datetime import datetime

from qmt_adapter.trading import QmtAccount
from qmt_adapter.qmt_data import FLOAT_SHARES, INDUSTRY_MAP

# 科技板块
TECH_SECTORS = ['电子', '计算机', '通信', '传媒']

# 全局状态
class G():
    pass
g = G()
g.initialized = False
g.holdings = {}
g.day_count = 0
g.last_rebalance_day = -999

# 策略参数
STOP_LOSS = -0.08
TAKE_PROFIT = 0.25
HOLD_DAYS_MAX = 20
MAX_DAILY_BUY = 3
MAX_POSITION = 0.35
MAX_HOLDINGS = 3
REBALANCE_DAYS = 10
BREADTH_MA = 20
BREADTH_HIGH = 0.50
BREADTH_LOW = 0.30
ACCOUNT_ID = 'testS'
ACCOUNT_TYPE = 'stock'


def _get_tech_codes():
    """获取科技板块股票"""
    codes = []
    for code, industry in INDUSTRY_MAP.items():
        if industry in TECH_SECTORS:
            codes.append(code)
    return codes


def _calc_breadth(close_panel, tech_codes):
    """计算广度：科技股收盘价>MA20比例"""
    above = 0
    total = 0
    for code in tech_codes:
        if code not in close_panel:
            continue
        close = close_panel[code]
        if len(close) < BREADTH_MA:
            continue
        ma = np.nanmean(close[-BREADTH_MA:])
        if close[-1] > ma:
            above += 1
        total += 1
    return above / total if total > 0 else 1.0


def init(C):
    """QMT初始化"""
    print('[INIT] v75j QMT starting...')
    
    tech_codes = _get_tech_codes()
    print('[INIT] tech stocks=%d' % len(tech_codes))
    
    g.initialized = True
    print('[INIT] done, will run on each bar')


def handlebar(C):
    """QMT主循环"""
    if not g.initialized:
        return
    
    bar_date = timetag_to_datetime(C.get_bar_timetag(C.barpos), '%Y%m%d%H%M%S')
    today = bar_date[:8]
    
    g.day_count += 1
    
    now = datetime.now()
    now_time = now.strftime('%H%M%S')
    if now_time < '093000' or now_time > '150000':
        return
    
    print('[%s] day=%d holdings=%d' % (today, g.day_count, len(g.holdings)))
    
    acct = QmtAccount(C, ACCOUNT_ID, ACCOUNT_TYPE)
    cash = acct.get_cash()
    
    # 风控检查
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
    
    for code, reason in sell_codes:
        if code in g.holdings:
            shares = g.holdings[code]['shares']
            acct.sell(code, shares, price=-1, reason=reason)
            print('[SELL] %s %s %s' % (code, reason, bar_date))
            del g.holdings[code]
    
    # 调仓日
    days_since_rebalance = g.day_count - g.last_rebalance_day
    if days_since_rebalance < REBALANCE_DAYS:
        return
    
    if len(g.holdings) >= MAX_HOLDINGS:
        return
    
    # 获取科技板块股票
    tech_codes = _get_tech_codes()
    if not tech_codes:
        return
    
    # 获取行情
    data = C.get_market_data_ex(
        ['open', 'high', 'low', 'close', 'volume', 'amount'],
        tech_codes[:100],
        period='1d',
        count=30,
        subscribe=False,
    )
    
    # 计算广度
    close_panel = {}
    for code in tech_codes[:100]:
        if code in data and len(data[code]) > 0:
            close_panel[code] = data[code]['close'].values
    
    breadth = _calc_breadth(close_panel, tech_codes[:100])
    print('[BREADTH] %.2f' % breadth)
    
    # 广度过滤
    if breadth < BREADTH_LOW:
        print('[SKIP] breadth too low')
        return
    
    # 线性减仓
    actual_holdings = MAX_HOLDINGS
    if breadth < BREADTH_HIGH:
        actual_holdings = max(1, int(MAX_HOLDINGS * breadth / BREADTH_HIGH))
    
    # 选股：流动性因子
    candidates = []
    for code in tech_codes[:100]:
        if code in g.holdings:
            continue
        if code not in data or len(data[code]) < 20:
            continue
        
        df = data[code]
        amount = df['amount'].values
        
        # 流动性：20日均成交额（负向，越低流动性越差=溢价）
        avg_amount = np.nanmean(amount[-20:])
        if avg_amount <= 0:
            continue
        
        score = -avg_amount
        candidates.append((code, score))
    
    candidates.sort(key=lambda x: x[1], reverse=True)
    
    # 买入
    buy_count = min(actual_holdings - len(g.holdings), len(candidates))
    for i in range(buy_count):
        code, score = candidates[i]
        data = C.get_market_data_ex(['close'], [code], period='1d', count=1, subscribe=False)
        if code not in data or len(data[code]) == 0:
            continue
        price = data[code]['close'].values[-1]
        
        available = cash * 0.95 / (buy_count - i)
        shares = int(available / price / 100) * 100
        if shares <= 0:
            continue
        
        acct.buy(code, shares, price=price, reason='AUTO')
        g.holdings[code] = {'shares': shares, 'cost': price, 'entry_day': g.day_count}
        print('[BUY] %s %d @%.2f %s' % (code, shares, price, bar_date))
        cash -= shares * price
    
    g.last_rebalance_day = g.day_count
