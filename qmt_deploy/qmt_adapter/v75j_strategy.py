#coding:utf-8
"""
v75j_strategy.py - V75J Strategy Logic

Tech trend + liquidity factor + breadth filter.
- Tech filter: electronic/computer/communication/media sectors
- Liquidity: 20-day average trading amount (higher = more liquid)
- Breadth: % of tech stocks above 20-day MA
- Linear position scaling in breadth neutral zone

Per-stock rebalance: each stock sells independently when hold_days >= REBALANCE_DAYS.
Buy when slots are available (breadth allows).
"""
import numpy as np
import pandas as pd
from datetime import datetime

# Debug switch (set by entry file via set_debug())
_DEBUG = False


def set_debug(flag):
    global _DEBUG
    _DEBUG = flag


# Tech sector names in QMT instrument detail
TECH_SECTORS = ['����', '�����', 'ͨ��', '��ý']

# Module-level globals
_stock_pool = None
_stock_list = None
_account = None
_hold_days = {}
_last_trade_date = None
_today_buys = 0
_last_trade_date = None
_rebalance_days = 10
_tech_codes = None
_industry_map = None
_kline_cache_tech = None
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
    global _stock_pool, _stock_list, _account
    global _hold_days, _last_trade_date, _today_buys, _rebalance_days
    global _tech_codes, _industry_map, _kline_cache_tech, _kline_cache_date
    global _risk_config

    from .qmt_data import ZZ1800_STOCKS
    from .trading import QmtAccount
    from .config import get_strategy_params, ACCOUNT_CONFIG
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
    _today_buys = 0
    _params = get_strategy_params('v75j')
    _rebalance_days = _params.get('rebalance_days', 10)
    _risk_config = {k: _params[k] for k in ('stop_loss', 'take_profit', 'hold_days_max')}
    _kline_cache_tech = None
    _kline_cache_date = None

    # Build industry map at init
    _build_industry_map(C)

    if _DEBUG:
        print('[V75J] init done. pool=%d, tech=%d, rebalance_days=%d' % (
            len(_stock_list), len(_tech_codes) if _tech_codes else 0, _rebalance_days))
        print('[V75J] risk: SL=%.2f TP=%.2f HD=%d' % (
            _risk_config['stop_loss'], _risk_config['take_profit'], _risk_config['hold_days_max']))
        if _tech_codes:
            print('[V75J] tech codes (first 10): %s' % ','.join(_tech_codes[:10]))


def _build_industry_map(C):
    """Build industry mapping for ZZ1800 stocks using QMT instrument detail."""
    global _tech_codes, _industry_map

    _industry_map = {}
    _tech_codes = []

    for code in _stock_list:
        try:
            detail = C.get_instrument_detail(code)
            if detail and 'IndustryClassification' in detail:
                industry = detail['IndustryClassification']
                _industry_map[code] = industry
                if industry in TECH_SECTORS:
                    _tech_codes.append(code)
            else:
                _industry_map[code] = ''
        except Exception:
            _industry_map[code] = ''


def on_signal(C):
    """Core business logic - shared by both handlebar and run_time triggers.

    Per-stock flow each bar:
    1. Increment hold_days
    2. Risk control (SL/TP/HD) -> sell if triggered
    3. Per-stock time exit: sell if hold_days >= REBALANCE_DAYS
    4. If any slots empty -> breadth check -> select new stocks -> buy
    """
    global _last_trade_date, _today_buys, _account, _stock_pool, _stock_list
    global _kline_cache_tech, _kline_cache_date, _risk_config, _rebalance_days

    if _account is None:
        from .qmt_data import ZZ1800_STOCKS
        from .trading import QmtAccount
        from .config import get_strategy_params
        from . import qmt_runner
        qmt_runner.qmt_init(C)
        _stock_pool = ZZ1800_STOCKS
        _stock_list = _stock_pool
        _account = QmtAccount(C)
        _params = get_strategy_params('v75j')
        _rebalance_days = _params.get('rebalance_days', 10)
        _risk_config = {k: _params[k] for k in ('stop_loss', 'take_profit', 'hold_days_max')}
        _kline_cache_tech = None
        _kline_cache_date = None
        if _tech_codes is None:
            _build_industry_map(C)

    from .config import get_strategy_params
    _params = get_strategy_params('v75j')
    max_holdings = _params.get('max_holdings', 3)
    max_per_stock = _params.get('max_per_stock', 0.35)

    today = _get_bar_date(C)

    # 1. Increment hold_days
    for code in list(_hold_days.keys()):
        _hold_days[code] = _hold_days.get(code, 0) + 1

    # 2. Risk control (SL/TP/HD) - sells individually
    from . import qmt_runner
    sold = qmt_runner.check_risk(C, _account, _hold_days, _risk_config, bar_date=today)
    for code in sold:
        _hold_days.pop(code, None)

    # 3. Per-stock time exit: sell if hold_days >= rebalance_days
    holdings = _account.get_holdings()
    for p in holdings:
        code = p['code']
        days = _hold_days.get(code, 0)
        if days >= _rebalance_days:
            if _DEBUG:
                print('[V75J] time exit: %s days=%d >= %d -> SELL' % (code, days, _rebalance_days))
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
            print('[V75J] hold: %s shares=%d cost=%.2f days=%d' % (
                p['code'], p['shares'], p['avg_cost'], _hold_days.get(p['code'], 0)))

    if slots <= 0:
        return

    # Breadth filter before buying
    breadth = _calc_breadth(C)
    high_thresh = _params.get('breadth_high', 0.50)
    low_thresh = _params.get('breadth_low', 0.30)

    if breadth < low_thresh:
        if _DEBUG:
            print('[V75J] breadth %.4f < %.2f -> SKIP (no buy)' % (breadth, low_thresh))
        return

    # Linear position scaling based on breadth
    if breadth < high_thresh:
        scaled_slots = max(1, int(max_holdings * breadth / high_thresh))
        slots = min(slots, scaled_slots)
        if _DEBUG:
            print('[V75J] breadth %.4f in [%.2f, %.2f) -> scale to %d slots' % (
                breadth, low_thresh, high_thresh, slots))

    if _DEBUG:
        print('[V75J] %d slots available, selecting stocks...' % slots)

    selected = _select_stocks(C, breadth)
    if not selected:
        return

    # Filter out currently held stocks
    held_codes = set(p['code'] for p in holdings)
    buy_list = [c for c in selected if c not in held_codes][:slots]

    if not buy_list:
        return

    target = {}
    for code in buy_list:
        target[code] = max_per_stock

    if _DEBUG:
        print('[V75J] buy targets:')
        for code, w in target.items():
            print('  %s weight=%.4f' % (code, w))

    qmt_runner.execute_buy(C, _account, target, bar_date=today, capital=_params.get('capital', 50000))



def handlebar(C):
    """Backward compat: handlebar -> on_signal."""
    on_signal(C)

def _calc_breadth(C):
    """Calculate tech breadth: % of tech stocks with close > MA20.

    Returns float 0.0-1.0. Returns 1.0 if insufficient data.
    """
    global _tech_codes, _kline_cache_tech, _kline_cache_date

    if not _tech_codes:
        return 1.0

    today = _get_bar_date(C)

    # Cache kline data per day
    if _kline_cache_tech is None or _kline_cache_date != today:
        from .data import get_kline_data_multi
        _kline_cache_tech = get_kline_data_multi(C, _tech_codes, count=25)
        _kline_cache_date = today

    kline_data = _kline_cache_tech
    ma_period = 20

    above = 0
    total = 0

    for code in _tech_codes:
        if code not in kline_data:
            continue
        df = kline_data[code]
        if len(df) < ma_period:
            continue
        close = df['close'].values
        latest = close[-1]
        if np.isnan(latest) or latest <= 0:
            continue
        total += 1
        ma = np.nanmean(close[-ma_period:])
        if latest > ma:
            above += 1

    breadth = above / total if total > 0 else 1.0

    if _DEBUG:
        print('[V75J] breadth: %.4f (%d/%d above MA20)' % (breadth, above, total))

    return breadth


def _select_stocks(C, breadth=None):
    """V75J stock selection: liquidity ranking from tech stocks.

    3. Rank tech stocks by 20-day avg amount (liquidity, higher = better)
    4. Select top N from tech stocks only
    """
    from .data import get_kline_data_multi

    # Ensure kline cache has enough data (20-day avg amount needs 25 bars)
    today = _get_bar_date(C)
    global _kline_cache_tech, _kline_cache_date
    if _kline_cache_tech is None or _kline_cache_date != today or \
            (len(_kline_cache_tech) > 0 and len(list(_kline_cache_tech.values())[0]) < 20):
        from .data import get_kline_data_multi as _get_kl
        _kline_cache_tech = _get_kl(C, _tech_codes, count=25)
        _kline_cache_date = today

    kline_data = _kline_cache_tech

    # Score tech stocks by 20-day average amount (liquidity)
    # Exclude STAR board (688/689) - same as original v75a
    scored = []
    for code in _tech_codes:
        if code.startswith(('688', '689')):
            continue
        if code not in kline_data:
            continue
        df = kline_data[code]
        if len(df) < 20:
            continue
        latest_close = df['close'].values[-1]
        if np.isnan(latest_close) or latest_close <= 0:
            continue
        if latest_close > 300:
            continue
        # 20-day average amount
        amounts = df['amount'].values[-20:]
        avg_amount = np.nanmean(amounts)
        if np.isnan(avg_amount) or avg_amount <= 0:
            continue
        scored.append((code, avg_amount))

    if not scored:
        return []

    # Sort by avg amount descending (more liquid first)
    scored.sort(key=lambda x: x[1], reverse=True)

    if _DEBUG:
        print('[V75J] liquidity candidates=%d, top 10:' % len(scored))
        for code, amt in scored[:10]:
            print('  %s avg_amount=%.0f' % (code, amt))

    ranked = [code for code, _ in scored]
    return ranked
