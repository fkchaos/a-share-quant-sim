#coding:gbk
"""
v75j_strategy.py - V75J Strategy Logic

Tech trend + liquidity factor + breadth filter.
Only implements stock selection.
"""
import pandas as pd
import numpy as np


def init(C):
    """Init: load ZZ1800 and tech board."""
    from .qmt_data import ZZ1800_STOCKS
    from .trading import QmtAccount
    from .config import ACCOUNT_CONFIG, RISK_CONFIG, REBALANCE_CONFIG
    from . import qmt_runner
    
    qmt_runner.qmt_init(C)
    
    # v75j: tech board + ZZ1800
    C.stock_pool = ZZ1800_STOCKS
    C.stock_list = C.stock_pool
    C.account = QmtAccount(C)
    
    C.hold_days = {}
    C.last_rebalance_date = None
    C.today_buys = 0
    C.last_trade_date = None
    C.rebalance_days = REBALANCE_CONFIG.get('rebalance_days', 10)


def handlebar(C):
    """Main: stock selection -> target -> execute."""
    from datetime import datetime
    from . import qmt_runner
    from .config import ACCOUNT_CONFIG, RISK_CONFIG
    
    today = datetime.now().strftime('%Y-%m-%d')
    if C.last_trade_date == today:
        return
    C.last_trade_date = today
    
    # Update hold days
    for code in list(C.hold_days.keys()):
        C.hold_days[code] = C.hold_days.get(code, 0) + 1
    
    # Risk check
    qmt_runner.check_risk(C, C.account, C.hold_days)
    
    # Check rebalance
    is_rebal = qmt_runner.is_rebalance_day(C, C.rebalance_days)
    
    if not is_rebal:
        return
    
    # Breadth filter
    breadth = _calc_breadth(C)
    
    if breadth < 0.30:
        return
    
    # Stock selection
    selected = _select_stocks(C)
    
    if not selected:
        return
    
    # Target weight
    max_pos = 0.35
    max_holdings = 3
    target = {}
    for code in selected[:max_holdings]:
        target[code] = max_pos / len(selected[:max_holdings])
    
    qmt_runner.execute_buy(C, C.account, target)


def _calc_breadth(C):
    """Calculate market breadth: ratio of stocks above MA20."""
    from .data import load_kline
    from .qmt_data import FLOAT_SHARES
    
    sample_size = min(50, len(C.stock_list))
    sample_codes = C.stock_list[:sample_size]
    all_data = load_kline(C, sample_codes)
    
    above_ma = 0
    total = 0
    
    for code, df in all_data.items():
        if len(df) < 20:
            continue
        total += 1
        close = df['close']
        ma20 = close.rolling(20).mean()
        if close.iloc[-1] > ma20.iloc[-1]:
            above_ma += 1
    
    if total == 0:
        return 0.5
    
    return above_ma / total


def _select_stocks(C):
    """Stock selection: tech trend + liquidity."""
    from .data import load_kline
    from .qmt_data import FLOAT_SHARES
    
    all_data = load_kline(C, C.stock_list)
    
    scores = []
    for code in C.stock_list:
        if code not in FLOAT_SHARES or FLOAT_SHARES[code] <= 0:
            continue
        if code not in all_data or len(all_data[code]) < 20:
            continue
        
        df = all_data[code]
        
        # Momentum (20d return)
        if 'close' in df.columns and len(df) >= 20:
            mom = (df['close'].iloc[-1] / df['close'].iloc[-20] - 1) * 100
        else:
            mom = 0
        
        # Liquidity (lower turnover is better)
        if 'amount' in df.columns:
            avg_amount = df['amount'].tail(20).mean()
            float_shares = FLOAT_SHARES[code]
            avg_turnover = avg_amount / float_shares / 100
        else:
            avg_turnover = 999
        
        scores.append({
            'code': code,
            'momentum': mom,
            'turnover': avg_turnover,
        })
    
    if not scores:
        return []
    
    df = pd.DataFrame(scores)
    df['mom_rank'] = df['momentum'].rank(ascending=True, pct=True)
    df['liq_rank'] = df['turnover'].rank(ascending=False, pct=True)
    df['score'] = 0.45 * df['mom_rank'] + 0.30 * df['liq_rank']
    
    df = df.sort_values('score', ascending=False)
    
    return df.head(5)['code'].tolist()
