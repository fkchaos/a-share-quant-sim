#coding:gbk
"""
v61c_strategy.py - V61C Strategy Logic

Low turnover + small cap factors.
Turnover = volume / float_shares (QMT volume is in shares, not lots)
5-day average turnover + market cap, equal weight 50/50 rank scoring.

Per-stock rebalance: each stock sells independently when hold_days >= REBALANCE_DAYS.
No unified rebalance day. Buy when slots are available.
"""
import numpy as np
import pandas as pd
from datetime import datetime

# Debug switch (set by entry file via set_debug())
_DEBUG = False


def set_debug(flag):
    global _DEBUG
    _DEBUG = flag


# Module-level globals
_stock_pool = None
_stock_list = None
_account = None
_hold_days = {}
_last_trade_date = None
_today_buys = 0
_last_trade_date = None
_kline_cache = None
_kline_cache_date = None
_risk_config = None


def init(C):
    """Init strategy."""
    global _stock_pool, _stock_list, _account
    global _hold_days, _last_trade_date, _today_buys
    global _kline_cache, _kline_cache_date, _risk_config

    from .qmt_data import ZZ1800_STOCKS
    from .trading import QmtAccount
    from .config import ACCOUNT_CONFIG, RISK_CONFIG, V61C_RISK_CONFIG, REBALANCE_CONFIG
    from . import qmt_runner

    qmt_runner.qmt_init(C)

    _stock_pool = ZZ1800_STOCKS
    _stock_list = _stock_pool
    _account = QmtAccount(C)
    _hold_days = {}
    _last_trade_date = None
    _today_buys = 0
    _risk_config = V61C_RISK_CONFIG
    _kline_cache = None
    _kline_cache_date = None

    if DEBUG:
        print('[V61C] init done. pool=%d, rebalance_days=%d' % (
            len(_stock_list), REBALANCE_CONFIG.get('rebalance_days', 5)))
        print('[V61C] risk: SL=%.2f TP=%.2f HD=%d' % (
            _risk_config['stop_loss'], _risk_config['take_profit'], _risk_config['hold_days_max']))


def handlebar(C):
    """Main strategy logic.

    Per-stock flow each bar:
    1. Increment hold_days
    2. Risk control (SL/TP/HD) -> sell if triggered
    3. Per-stock time exit: sell if hold_days >= REBALANCE_DAYS
    4. If any slots empty -> select new stocks -> buy
    """
    global _last_trade_date, _today_buys, _account, _stock_pool, _stock_list
    global _kline_cache, _kline_cache_date, _risk_config

    if _account is None:
        from .qmt_data import ZZ1800_STOCKS
        from .trading import QmtAccount
        from .config import V61C_RISK_CONFIG, REBALANCE_CONFIG
        from . import qmt_runner
        qmt_runner.qmt_init(C)
        _stock_pool = ZZ1800_STOCKS
        _stock_list = _stock_pool
        _account = QmtAccount(C)
        _risk_config = V61C_RISK_CONFIG
        _kline_cache = None
        _kline_cache_date = None

    from .config import REBALANCE_CONFIG, DEBUG
    rebalance_days = REBALANCE_CONFIG.get('rebalance_days', 5)
    max_holdings = 5

    today = datetime.now().strftime('%Y-%m-%d')

    # 1. Increment hold_days
    for code in list(_hold_days.keys()):
        _hold_days[code] = _hold_days.get(code, 0) + 1

    # 2. Risk control (SL/TP/HD) - sells individually
    from . import qmt_runner
    qmt_runner.check_risk(C, _account, _hold_days, _risk_config)

    # 3. Per-stock time exit: sell if hold_days >= rebalance_days
    holdings = _account.get_holdings()
    for p in holdings:
        code = p['code']
        days = _hold_days.get(code, 0)
        if days >= rebalance_days:
            if DEBUG:
                print('[V61C] time exit: %s days=%d >= %d -> SELL' % (code, days, rebalance_days))
            _account.sell_all(code)
            _hold_days.pop(code, None)

    # 4. Check if slots are available -> buy
    holdings = _account.get_holdings()
    current_count = len([p for p in holdings if p['shares'] > 0])
    slots = max_holdings - current_count

    if DEBUG and holdings:
        for p in holdings:
            print('[V61C] hold: %s shares=%d cost=%.2f days=%d' % (
                p['code'], p['shares'], p['avg_cost'], _hold_days.get(p['code'], 0)))

    if slots <= 0:
        return

    if DEBUG:
        print('[V61C] %d slots available, selecting stocks...' % slots)

    selected = _select_stocks(C)
    if not selected:
        return

    # Filter out currently held stocks
    held_codes = set(p['code'] for p in holdings)
    buy_list = [c for c in selected if c not in held_codes][:slots]

    if not buy_list:
        return

    max_pos = 0.25
    target = {}
    for code in buy_list:
        target[code] = max_pos / max_holdings

    if DEBUG:
        print('[V61C] buy targets:')
        for code, w in target.items():
            print('  %s weight=%.4f' % (code, w))

    qmt_runner.execute_buy(C, _account, target)


def _select_stocks(C):
    """V61C stock selection: low turnover + small cap.

    Factor 1: 5-day average turnover (negative -> low turnover preferred)
    Factor 2: market cap (negative -> small cap preferred)
    Equal weight 50/50 rank scoring.
    """
    from .qmt_data import FLOAT_SHARES
    from .data import get_kline_data_multi

    today = datetime.now().strftime('%Y-%m-%d')

    # Cache kline data per day (avoid re-fetch in same day)
    global _kline_cache, _kline_cache_date
    if _kline_cache is not None and _kline_cache_date == today:
        kline_data = _kline_cache
    else:
        kline_data = get_kline_data_multi(C, _stock_list, count=7)
        _kline_cache = kline_data
        _kline_cache_date = today

    # Filter candidates: must have float_shares and kline data
    candidates = []
    for code in _stock_list:
        if code not in FLOAT_SHARES:
            continue
        fs = FLOAT_SHARES[code]
        if fs <= 0:
            continue
        if code not in kline_data:
            continue
        df = kline_data[code]
        if len(df) < 3:
            continue
        candidates.append(code)

    if not candidates:
        return []

    # Calculate factors
    turnover_scores = {}
    mcap_scores = {}

    for code in candidates:
        fs = FLOAT_SHARES[code]
        df = kline_data[code]

        # QMT volume is in shares (not lots)
        # Turnover = volume / float_shares
        vol = df['volume'].values
        close = df['close'].values

        # 5-day average turnover
        n = min(5, len(vol))
        recent_vol = vol[-n:]
        avg_turnover = np.mean(recent_vol) / fs
        turnover_scores[code] = avg_turnover

        # Market cap (use latest close)
        latest_close = close[-1]
        if latest_close > 0:
            mcap_scores[code] = latest_close * fs
        else:
            mcap_scores[code] = float('inf')

    if not turnover_scores:
        return []

    # Rank scoring (low turnover = high score, small cap = high score)
    codes = list(turnover_scores.keys())
    scores = pd.Series(0.0, index=codes)

    # Turnover rank: lower is better -> ascending=True means lowest gets highest rank
    turn_series = pd.Series(turnover_scores)
    if len(turn_series) > 50:
        turn_rank = turn_series.rank(ascending=True, pct=True)
        scores = scores.add(turn_rank, fill_value=0)

    # Market cap rank: smaller is better -> ascending=True means smallest gets highest rank
    mcap_series = pd.Series(mcap_scores)
    if len(mcap_series) > 50:
        mcap_rank = mcap_series.rank(ascending=True, pct=True)
        scores = scores.add(mcap_rank, fill_value=0)

    ranked = scores.sort_values(ascending=False)

    if _DEBUG:
        print('[V61C] candidates=%d, top 10:' % len(ranked))
        for code in ranked.head(10).index:
            print('  %s score=%.4f turnover=%.4f%% mcap=%.1f亿' % (
                code, ranked[code],
                turnover_scores.get(code, 0) * 100,
                mcap_scores.get(code, 0) / 1e8))

    return ranked.index.tolist()
