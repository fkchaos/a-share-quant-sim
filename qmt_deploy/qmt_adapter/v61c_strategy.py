#coding:gbk
"""
v61c_strategy.py - v61c策略逻辑
使用qmt_runner封装通用逻辑，只实现选股部分。
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
    'HOLD_DAYS_MAX': 5,
    'SELL_OUT_OF': 15,
    'MAX_HOLDINGS': 5,
    'REBALANCE_DAYS': 5,
}


def init(C):
    qmt_init(C)
    print('[INIT] v61c strategy ready')


def handlebar(C):
    if not g.initialized:
        return
    
    bar_date = timetag_to_datetime(C.get_bar_timetag(C.barpos), '%Y%m%d%H%M%S')
    today = bar_date[:8]
    g.day_count += 1
    
    # 风控检查
    acct = QmtAccount(C, ACCOUNT_CONFIG['account_id'], ACCOUNT_CONFIG['account_type'])
    check_risk(C, acct, bar_date, today, PARAMS)
    
    # 调仓判断
    days_since_rebalance = g.day_count - g.last_rebalance_day
    if not is_rebalance_time(days_since_rebalance, PARAMS['REBALANCE_DAYS']):
        return
    if len(g.holdings) >= PARAMS['MAX_HOLDINGS']:
        return
    
    # 获取行情
    stock_list = ZZ1800_STOCKS[:200]
    data = get_market_data(C, stock_list)
    
    # 选股
    candidates = []
    for code in stock_list:
        if code in g.holdings:
            continue
        if code not in data or len(data[code]) < 20:
            continue
        
        df = data[code]
        close = df['close'].values
        volume = df['volume'].values
        
        float_sh = FLOAT_SHARES.get(code, 0)
        if float_sh <= 0:
            continue
        
        # 换手率（负向）
        turnover = volume * 100.0 / float_sh
        avg_turnover = np.nanmean(turnover[-5:])
        
        # 市值（负向）
        market_cap = close[-1] * float_sh
        
        score = -avg_turnover * 0.5 - market_cap * 0.5e-12
        candidates.append((code, score))
    
    candidates.sort(key=lambda x: x[1], reverse=True)
    
    # 买入
    cash = acct.get_cash()
    cash = execute_buy(C, acct, candidates, cash, bar_date, PARAMS['MAX_HOLDINGS'])
    g.last_rebalance_day = g.day_count
