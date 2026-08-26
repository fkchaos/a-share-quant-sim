#coding:gbk
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
TECH_SECTORS = ['电子', '计算机', '通信', '传媒']

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


def init(C):
    """Init strategy."""
    global _stock_pool, _stock_list, _account
    global _hold_days, _last_trade_date, _today_buys, _rebalance_days
    global _tech_codes, _industry_map, _kline_cache_tech, _kline_cache_date
    global _risk_config

    from .qmt_data import ZZ1800_STOCKS
    from .trading import QmtAccount
    from .config import RISK_CONFIG, REBALANCE_CONFIG
    from . import qmt_runner

    qmt_runner.qmt_init(C)

    _stock_pool = ZZ1800_STOCKS
    _stock_list = _stock_pool
    _account = QmtAccount(C)
    _hold_days = {}
    _last_trade_date = None
    _today_buys = 0
    _rebalance_days = REBALANCE_CONFIG.get('rebalance_days', 10)
    _risk_config = RISK_CONFIG
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


def handlebar(C):
    """Main strategy logic.

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
        from .config import RISK_CONFIG, REBALANCE_CONFIG
        from . import qmt_runner
        qmt_runner.qmt_init(C)
        _stock_pool = ZZ1800_STOCKS
        _stock_list = _stock_pool
        _account = QmtAccount(C)
        _rebalance_days = REBALANCE_CONFIG.get('rebalance_days', 10)
        _risk_config = RISK_CONFIG
        _kline_cache_tech = None
        _kline_cache_date = None
        if _tech_codes is None:
            _build_industry_map(C)

    from .config import REBALANCE_CONFIG
    max_holdings = 3

    today = datetime.now().strftime('%Y-%m-%d')

    # 1. Increment hold_days
    for code in list(_hold_days.keys()):
        _hold_days[code] = _hold_days.get(code, 0) + 1

    # 2. Risk control (SL/TP/HD) - sells individually
    from . import qmt_runner
    qmt_runner.check_risk(C, _account, _hold_days, _risk_config, bar_date=today)

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

    if _DEBUG and holdings:
        for p in holdings:
            print('[V75J] hold: %s shares=%d cost=%.2f days=%d' % (
                p['code'], p['shares'], p['avg_cost'], _hold_days.get(p['code'], 0)))

    if slots <= 0:
        return

    # Breadth filter before buying
    breadth = _calc_breadth(C)
    high_thresh = 0.50
    low_thresh = 0.30

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

    max_pos = 0.35
    target = {}
    for code in buy_list:
        target[code] = max_pos / max_holdings

    if _DEBUG:
        print('[V75J] buy targets:')
        for code, w in target.items():
            print('  %s weight=%.4f' % (code, w))

    qmt_runner.execute_buy(C, _account, target)


def _calc_breadth(C):
    """Calculate tech breadth: % of tech stocks with close > MA20.

    Returns float 0.0-1.0. Returns 1.0 if insufficient data.
    """
    global _tech_codes, _kline_cache_tech, _kline_cache_date

    if not _tech_codes:
        return 1.0

    today = datetime.now().strftime('%Y-%m-%d')

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
    today = datetime.now().strftime('%Y-%m-%d')
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
