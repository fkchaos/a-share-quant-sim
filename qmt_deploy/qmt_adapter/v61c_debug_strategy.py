#coding:gbk
"""
v61c_debug_strategy.py - V61C Debug Version

Adds verbose output for debugging.
"""
import pandas as pd


def init(C):
    """Init with debug output."""
    from .qmt_data import ZZ1800_STOCKS
    from .trading import QmtAccount
    from .config import ACCOUNT_CONFIG, RISK_CONFIG, REBALANCE_CONFIG
    from . import qmt_runner
    
    qmt_runner.qmt_init(C)
    
    C.stock_pool = ZZ1800_STOCKS
    C.stock_list = list(C.stock_pool.keys())
    C.account = QmtAccount(C)
    
    C.hold_days = {}
    C.last_rebalance_date = None
    C.today_buys = 0
    C.last_trade_date = None
    C.rebalance_days = REBALANCE_CONFIG.get('rebalance_days', 5)
    
    print('[INIT] Stock pool: {} stocks'.format(len(C.stock_list)))


def handlebar(C):
    """Main with debug output."""
    from datetime import datetime
    from . import qmt_runner
    from .config import ACCOUNT_CONFIG, RISK_CONFIG
    from .qmt_data import FLOAT_SHARES
    
    today = datetime.now().strftime('%Y-%m-%d')
    if C.last_trade_date == today:
        return
    C.last_trade_date = today
    
    print('[{}] Processing...'.format(today))
    
    # Update hold days
    for code in list(C.hold_days.keys()):
        C.hold_days[code] = C.hold_days.get(code, 0) + 1
    
    # Risk check
    qmt_runner.check_risk(C, C.account, C.hold_days)
    
    # Check rebalance
    is_rebal = qmt_runner.is_rebalance_day(C, C.rebalance_days)
    
    if not is_rebal:
        print('[{}] Not rebalance day, skip'.format(today))
        return
    
    # Stock selection
    selected = _select_stocks(C)
    
    if not selected:
        print('[{}] No stocks selected'.format(today))
        return
    
    print('[{}] Selected: {}'.format(today, selected[:5]))
    
    # Target weight
    max_pos = 0.25
    max_holdings = 5
    target = {}
    for code in selected[:max_holdings]:
        target[code] = max_pos / len(selected[:max_holdings])
    
    qmt_runner.execute_buy(C, C.account, target)


def _select_stocks(C):
    """Stock selection: low turnover + small cap."""
    from .data import load_kline
    from .qmt_data import FLOAT_SHARES
    
    all_data = load_kline(C, C.stock_list)
    print('[SELECT] got data for {}/{} stocks'.format(len(all_data), len(C.stock_list)))
    
    scores = []
    skipped_no_data = 0
    for code in C.stock_list:
        if code not in FLOAT_SHARES or FLOAT_SHARES[code] <= 0:
            continue
        if code not in all_data or len(all_data[code]) < 20:
            skipped_no_data += 1
            continue
        
        df = all_data[code]
        
        if 'amount' in df.columns:
            avg_amount = df['amount'].tail(20).mean()
            float_shares = FLOAT_SHARES[code]
            avg_turnover = avg_amount / float_shares / 100
        else:
            avg_turnover = 999
        
        if 'close' in df.columns:
            market_cap = df['close'].iloc[-1] * float_shares
        else:
            market_cap = 1e12
        
        scores.append({
            'code': code,
            'turnover': avg_turnover,
            'mcap': market_cap,
        })
    
    print('[SELECT] processed={}, skipped_no_data={}'.format(len(scores), skipped_no_data))
    
    if not scores:
        return []
    
    df = pd.DataFrame(scores)
    df['turnover_rank'] = df['turnover'].rank(ascending=True, pct=True)
    df['mcap_rank'] = df['mcap'].rank(ascending=True, pct=True)
    df['score'] = df['turnover_rank'] + df['mcap_rank']
    
    df = df.sort_values('score', ascending=True)
    
    return df.head(5)['code'].tolist()
