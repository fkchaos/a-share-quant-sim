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
_last_buy_date = None
_today_buys = 0
_last_trade_date = None
_last_buy_date = None
_kline_cache = None
_kline_cache_date = None
_risk_config = None


def _get_bar_date(C):
    """Get current bar date from ContextInfo. NEVER use datetime.now()."""
    # Method 1: get_bar_timetag (preferred)
    try:
        timetag = C.get_bar_timetag(C.barpos)
        from datetime import datetime
        if timetag > 0:
            return datetime.fromtimestamp(timetag / 1000).strftime('%Y%m%d')
    except Exception:
        pass
    # Method 2: get_market_data_ex (subscribe=True, default)
    try:
        _mk = C.stockcode + '.' + C.market
        _data = C.get_market_data_ex(['close'], [_mk], count=1)
        if _mk in _data and len(_data[_mk]) > 0:
            return str(_data[_mk].index[-1])[:10]
    except Exception:
        pass
    return 'unknown'


def init(C):
    """Init strategy."""
    global _stock_pool, _stock_list, _account, _last_buy_date, _sell_out_of
    global _hold_days, _last_trade_date, _today_buys
    global _kline_cache, _kline_cache_date, _risk_config

    from .qmt_data import ZZ1800_STOCKS
    from .trading import QmtAccount
    from .config import ACCOUNT_CONFIG, RISK_CONFIG, V61C_RISK_CONFIG, REBALANCE_CONFIG, SELL_OUT_OF_CONFIG
    from . import qmt_runner

    qmt_runner.qmt_init(C)

    _stock_pool = ZZ1800_STOCKS
    _stock_list = _stock_pool
    _account = QmtAccount(C)
    _hold_days = {}

    # Load persisted hold_days from file
    import json as _json
    import os as _os
    _persist_path = _os.path.join(_os.path.dirname(__file__), '_hold_days.json')
    try:
        with open(_persist_path, 'r') as _f:
            _data = _json.load(_f)
            _hold_days = _data.get('hold_days', {})
            _last_date = _data.get('last_date', '')
            # If date changed (new day), keep hold_days but update date
            if _last_date != today:
                print('[INIT] new day detected: %s -> %s, keeping %d positions' % (
                    _last_date, today, len(_hold_days)))
    except Exception:
        _hold_days = {}

    _last_trade_date = None
    _last_buy_date = None
    _today_buys = 0
    _risk_config = V61C_RISK_CONFIG
    _sell_out_of = SELL_OUT_OF_CONFIG.get('sell_out_of', 15)
    _kline_cache = None
    _kline_cache_date = None

    if _DEBUG:
        print('[INIT][V61C] init done. pool=%d, rebalance_days=%d' % (
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
    global _kline_cache, _kline_cache_date, _risk_config, _last_buy_date

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
        _sell_out_of = SELL_OUT_OF_CONFIG.get('sell_out_of', 15)
        _kline_cache = None
        _kline_cache_date = None

    from .config import REBALANCE_CONFIG
    rebalance_days = REBALANCE_CONFIG.get('rebalance_days', 5)
    max_holdings = 5

    today = _get_bar_date(C)
    if _DEBUG:
        print('[BAR] date=%s' % today)

    # 1. Increment hold_days
    for code in list(_hold_days.keys()):
        _hold_days[code] = _hold_days.get(code, 0) + 1
    if _DEBUG and _hold_days:
        print('[%s][V61C] hold_days: %s' % (today, dict(_hold_days)))

    # 2. Risk control (SL/TP/HD) - sells individually
    from . import qmt_runner
    sold = qmt_runner.check_risk(C, _account, _hold_days, _risk_config, bar_date=today)
    for code in sold:
        _hold_days.pop(code, None)

    # 3. Per-stock time exit with SELL_OUT_OF logic
    # Compute current rankings for hold period check
    ranked_codes = []
    try:
        from .data import get_kline_data_multi
        from .qmt_data import FLOAT_SHARES
        _kl = get_kline_data_multi(C, _stock_list, count=7)
        _turn = {}
        _mcap = {}
        for code in _stock_list:
            if code not in FLOAT_SHARES or code not in _kl:
                continue
            df = _kl[code]
            if len(df) < 3:
                continue
            fs = FLOAT_SHARES[code]
            vol = df['volume'].values
            close = df['close'].values
            n = min(5, len(vol))
            _turn[code] = np.mean(vol[-n:]) / fs if fs > 0 else 999
            _mcap[code] = close[-1] * fs if close[-1] > 0 else float('inf')
        if _turn:
            _codes = list(_turn.keys())
            _scores = pd.Series(0.0, index=_codes)
            _ts = pd.Series(_turn)
            if len(_ts) > 50:
                _scores = _scores.add(1 - _ts.rank(ascending=True, pct=True), fill_value=0)
            _ms = pd.Series(_mcap)
            if len(_ms) > 50:
                _scores = _scores.add(1 - _ms.rank(ascending=True, pct=True), fill_value=0)
            ranked_codes = _scores.sort_values(ascending=False).head(_sell_out_of).index.tolist()
    except Exception:
        pass

    holdings = _account.get_holdings()
    for p in holdings:
        code = p['code']
        days = _hold_days.get(code, 0)
        if days >= rebalance_days:
            if code in ranked_codes:
                # v61c core: still in Top N -> hold, reset days
                _hold_days[code] = 0
                if _DEBUG:
                    print('[%s][V61C] HOLD (renew): %s days=%d, still in Top%d' % (today, code, days, _sell_out_of))
            else:
                # Dropped out of ranking -> sell
                if _DEBUG:
                    print('[%s][V61C] time exit: %s days=%d, NOT in Top%d -> SELL' % (today, code, days, _sell_out_of))
                _account.sell_all(code)
                _hold_days.pop(code, None)

    # 4. Rank drop sell: sell holdings that dropped out of Top N
    if ranked_codes:
        holdings = _account.get_holdings()
        for p in holdings:
            code = p['code']
            if code not in ranked_codes and _hold_days.get(code, 0) > 0:
                if _DEBUG:
                    print('[%s][V61C] rank drop: %s NOT in Top%d -> SELL' % (today, code, _sell_out_of))
                _account.sell_all(code)
                _hold_days.pop(code, None)

    # 4. Check if slots are available -> buy
    holdings = _account.get_holdings()
    current_count = len([p for p in holdings if p['shares'] > 0])
    slots = max_holdings - current_count

    # Persist hold_days to file
    import json as _json
    import os as _os
    _persist_path = _os.path.join(_os.path.dirname(__file__), '_hold_days.json')
    try:
        with open(_persist_path, 'w') as _f:
            _json.dump({'hold_days': _hold_days, 'last_date': today}, _f)
    except Exception as _e:
        print('[WARN] failed to persist hold_days: %s' % str(_e))

    if _DEBUG and holdings:
        for p in holdings:
            print('[%s][V61C] hold: %s shares=%d cost=%.2f days=%d' % (today, 
                p['code'], p['shares'], p['avg_cost'], _hold_days.get(p['code'], 0)))

    if slots <= 0:
        return

    if _DEBUG:
        print('[%s][V61C] buy check: slots=%d last_buy=%s today=%s' % (today, slots, _last_buy_date, today))
    # Skip if already tried to buy today (no retry on same bar)
    # Note: use bar_date from context, not datetime.now() which returns system time in backtest
    if _last_buy_date == today:
        return

    if _DEBUG:
        print('[%s][V61C] %d slots available, selecting stocks...' % (today, slots))

    selected = _select_stocks(C)
    if not selected:
        return

    # Filter out currently held stocks
    held_codes = set(p['code'] for p in holdings)
    buy_list = [c for c in selected if c not in held_codes][:slots]

    if not buy_list:
        return

    max_pos = 0.50
    target = {}
    for code in buy_list:
        target[code] = max_pos / max_holdings

    if _DEBUG:
        print('[%s][V61C] buy targets:' % today)
        for code, w in target.items():
            print('  %s weight=%.4f' % (code, w))

    bought = qmt_runner.execute_buy(C, _account, target, bar_date=today)
    for code in bought:
        _hold_days[code] = 0
    _last_buy_date = today


def _select_stocks(C):
    """V61C stock selection: low turnover + small cap.

    Factor 1: 5-day average turnover (negative -> low turnover preferred)
    Factor 2: market cap (negative -> small cap preferred)
    Equal weight 50/50 rank scoring.
    """
    from .qmt_data import FLOAT_SHARES
    from .data import get_kline_data_multi

    today = _get_bar_date(C)

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
        avg_vol = np.mean(recent_vol)
        avg_turnover = avg_vol / fs
        turnover_scores[code] = avg_turnover

        # Market cap (use latest close)
        latest_close = close[-1]
        if latest_close > 0:
            mcap_scores[code] = latest_close * fs
        else:
            mcap_scores[code] = float('inf')
        # Debug: show raw data for first 5 candidates
        if _DEBUG and len(turnover_scores) <= 5:
            print("  [DATA] %s: price=%.2f vol_avg=%.0f float=%.0f turnover=%.4f%% mcap=%.1f" % (
                code, latest_close, avg_vol, fs,
                avg_turnover * 100, latest_close * fs / 1e8))

    if not turnover_scores:
        return []

    # Rank scoring (low turnover = high score, small cap = high score)
    codes = list(turnover_scores.keys())
    scores = pd.Series(0.0, index=codes)

    # Turnover rank: lower is better -> ascending=True means lowest gets highest rank
    turn_series = pd.Series(turnover_scores)
    if len(turn_series) > 50:
        turn_rank = 1 - turn_series.rank(ascending=True, pct=True)
        scores = scores.add(turn_rank, fill_value=0)

    # Market cap rank: smaller is better -> ascending=True means smallest gets highest rank
    mcap_series = pd.Series(mcap_scores)
    if len(mcap_series) > 50:
        mcap_rank = 1 - mcap_series.rank(ascending=True, pct=True)
        scores = scores.add(mcap_rank, fill_value=0)

    ranked = scores.sort_values(ascending=False)

    if _DEBUG:
        print('[V61C] candidates=%d, top 10:' % len(ranked))
        for code in ranked.head(10).index:
            print('  %s score=%.4f turnover=%.4f%% mcap=%.1fB' % (
                code, ranked[code],
                turnover_scores.get(code, 0) * 100,
                mcap_scores.get(code, 0) / 1e8))
        # Show turnover distribution
        all_turns = sorted(turnover_scores.values())
        n = len(all_turns)
        if n > 0:
            print('[V61C] turnover distribution: min=%.4f%% p25=%.4f%% median=%.4f%% p75=%.4f%% max=%.4f%%' % (
                all_turns[0]*100, all_turns[n//4]*100, all_turns[n//2]*100,
                all_turns[3*n//4]*100, all_turns[-1]*100))
            # Show where selected stocks fall
            for code in ranked.head(5).index:
                t = turnover_scores.get(code, 0)
                rank_pct = sum(1 for v in all_turns if v <= t) / n * 100
                print('  %s: turnover=%.4f%% -> percentile=%.0f%%' % (code, t*100, rank_pct))

    return ranked.index.tolist()
