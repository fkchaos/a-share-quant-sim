#coding:gbk
"""
v75j_strategy.py - v75j策略逻辑
流动性单因子 + 广度过滤，科技板块专用。
"""
import numpy as np
import pandas as pd

from qmt_adapter.qmt_runner import (
    g, qmt_init, check_risk, execute_buy, get_market_data, is_rebalance_time,
    ZZ1800_STOCKS, FLOAT_SHARES,
)
from qmt_adapter.config import ACCOUNT_CONFIG

# 策略参数
PARAMS = {
    'STOP_LOSS': -0.08,
    'TAKE_PROFIT': 0.25,
    'HOLD_DAYS_MAX': 20,
    'MAX_HOLDINGS': 3,
    'REBALANCE_DAYS': 10,
    'BREADTH_MA': 20,
    'BREADTH_HIGH': 0.50,
    'BREADTH_LOW': 0.30,
}

# 科技板块
TECH_SECTORS = ['电子', '计算机', '通信', '传媒']
_tech_codes = None


def _load_tech_codes():
    global _tech_codes
    if _tech_codes is not None:
        return _tech_codes
    from qmt_adapter.qmt_data import INDUSTRY_MAP
    _tech_codes = [code for code, ind in INDUSTRY_MAP.items() if ind in TECH_SECTORS]
    return _tech_codes


def _calc_breadth(C):
    """广度：多少科技股收盘价>MA20"""
    codes = _load_tech_codes()
    ma_period = PARAMS['BREADTH_MA']
    
    above = 0
    total = 0
    for c in codes[:100]:  # 取前100只科技股
        data = C.get_market_data_ex(['close'], [c], period='1d', count=ma_period, subscribe=False)
        if c not in data or len(data[c]) < ma_period:
            continue
        close = data[c]['close'].values
        total += 1
        ma = np.nanmean(close)
        if close[-1] > ma:
            above += 1
    
    return above / total if total > 0 else 1.0


def init(C):
    qmt_init(C)
    print('[INIT] v75j strategy ready, tech stocks=%d' % len(_load_tech_codes()))


def handlebar(C):
    if not g.initialized:
        return
    
    bar_date = timetag_to_datetime(C.get_bar_timetag(C.barpos), '%Y%m%d%H%M%S')
    today = bar_date[:8]
    g.day_count += 1
    
    # 风控检查
    acct = QmtAccount(C, ACCOUNT_CONFIG['account_id'], ACCOUNT_CONFIG['account_type'])
    check_risk(C, acct, bar_date, today, PARAMS)
    
    # 广度过滤
    breadth = _calc_breadth(C)
    if breadth < PARAMS['BREADTH_LOW']:
        return
    
    # 调仓判断
    days_since_rebalance = g.day_count - g.last_rebalance_day
    if not is_rebalance_time(days_since_rebalance, PARAMS['REBALANCE_DAYS']):
        return
    if len(g.holdings) >= PARAMS['MAX_HOLDINGS']:
        return
    
    # 获取科技股行情
    tech_codes = _load_tech_codes()[:100]
    data = get_market_data(C, tech_codes)
    
    # 选股：流动性因子
    candidates = []
    for code in tech_codes:
        if code in g.holdings:
            continue
        if code not in data or len(data[code]) < 20:
            continue
        
        amount = data[code]['amount'].values
        avg_amount = np.nanmean(amount[-20:])
        
        candidates.append((code, avg_amount))
    
    candidates.sort(key=lambda x: x[1], reverse=True)
    
    # 买入
    cash = acct.get_cash()
    cash = execute_buy(C, acct, candidates, cash, bar_date, PARAMS['MAX_HOLDINGS'])
    g.last_rebalance_day = g.day_count
