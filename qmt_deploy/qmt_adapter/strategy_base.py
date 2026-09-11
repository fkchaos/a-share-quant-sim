# coding:gbk
"""Shared strategy utilities for QMT adapter.

Extracted from v61c_strategy.py and v75j_strategy.py to eliminate
~150 lines of duplicated code. Both strategies import from here.

Usage in strategy files:
    from .strategy_base import get_bar_date, load_hold_days, persist_hold_days
"""


def get_bar_date(C):
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
    except Exception as _e:
        print('[BAR] WARN: get_bar_date both methods failed: %s' % _e)
    return 'unknown'


def load_hold_days(strategy_name):
    """Load persisted hold_days from file. Returns (hold_days_dict, last_date_str)."""
    import json, os
    path = os.path.join(os.path.dirname(__file__), '_hold_days_%s.json' % strategy_name)
    try:
        with open(path, 'r') as f:
            data = json.load(f)
        return data.get('hold_days', {}), data.get('last_date', '')
    except Exception as _e:
        print('[INIT] WARN: hold_days load failed, starting fresh: %s' % _e)
        return {}, ''


def persist_hold_days(strategy_name, hold_days, today):
    """Persist hold_days to file (atomic: tmp + rename)."""
    import json, os
    path = os.path.join(os.path.dirname(__file__), '_hold_days_%s.json' % strategy_name)
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w') as f:
            json.dump({'hold_days': hold_days, 'last_date': today}, f)
        os.rename(tmp, path)
    except Exception as _e:
        print('[WARN] failed to persist hold_days: %s' % str(_e))
        try:
            os.unlink(tmp)
        except Exception:
            pass


def is_limit_up(close, prev_close, market='SH'):
    """Check if stock is at limit up price.

    SH: 10% (5% for ST), SZ: 10% (5% for ST).
    Uses threshold of 9.5% to account for price rounding.
    """
    if prev_close <= 0:
        return False
    threshold = 0.095
    return close >= prev_close * (1 + threshold)


def apply_limit_up_filter(codes, kline_data):
    """Filter out limit-up stocks from candidate list."""
    filtered = []
    for code in codes:
        if code not in kline_data:
            filtered.append(code)
            continue
        df = kline_data[code]
        if len(df) < 2:
            filtered.append(code)
            continue
        close = df['close'].iloc[-1]
        prev_close = df['close'].iloc[-2]
        if is_limit_up(close, prev_close):
            continue  # skip limit up
        filtered.append(code)
    return filtered
