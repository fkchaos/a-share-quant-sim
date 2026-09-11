#coding:gbk
"""
v75j_strategy.py - V75J Strategy Logic

Tech trend + liquidity factor + breadth filter.
- Tech filter: electronic/computer/communication/media sectors
- Liquidity: 20-day average trading amount (higher = more liquid)
- Breadth: % of tech stocks above 20-day MA
- Linear position scaling in breadth neutral zone

Per-stock time exit: sell when hold_days >= hold_days_max.
Buy when slots are available (breadth allows).
"""
import numpy as np
import pandas as pd
from datetime import datetime
from .strategy_base import get_bar_date as _get_bar_date, load_hold_days, persist_hold_days

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
_hold_days_max = 10
_tech_codes = None
_industry_map = None
_kline_cache_tech = None
_kline_cache_date = None
_risk_config = None


# _get_bar_date imported from strategy_base


def init(C):
    """Init strategy."""
    global _stock_pool, _stock_list, _account
    global _hold_days, _last_trade_date, _today_buys, _hold_days_max
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
    _hold_days, _last_date = load_hold_days('v75j')
    _today_init = _get_bar_date(C)
    if _last_date and _last_date != _today_init:
        print('[INIT] new day detected: %s -> %s, keeping %d positions' % (
            _last_date, _today_init, len(_hold_days)))

    _last_trade_date = None
    _today_buys = 0
    _params = get_strategy_params('v75j')
    _hold_days_max = _params.get('hold_days_max', 10)
    _risk_config = {k: _params[k] for k in ('stop_loss', 'take_profit', 'hold_days_max')}
    _kline_cache_tech = None
    _kline_cache_date = None

    # Build industry map at init
    _build_industry_map(C)

    if _DEBUG:
        print('[V75J] init done. pool=%d, tech=%d, hold_days_max=%d' % (
            len(_stock_list), len(_tech_codes) if _tech_codes else 0, _hold_days_max))
        print('[V75J] risk: SL=%.2f TP=%.2f HD=%d' % (
            _risk_config['stop_loss'], _risk_config['take_profit'], _risk_config['hold_days_max']))
        if _tech_codes:
            print('[V75J] tech codes (first 10): %s' % ','.join(_tech_codes[:10]))


#coding:gbk
def _build_industry_map(C):
    """Build industry mapping from static data (qmt_data_static.py)."""
    global _tech_codes, _industry_map

    from .qmt_data_static import INDUSTRY, TECH_SECTORS

    _industry_map = {}
    _tech_codes = []

    for code in _stock_list:
        industry = INDUSTRY.get(code, '')
        _industry_map[code] = industry
        if industry in TECH_SECTORS:
            _tech_codes.append(code)

def on_signal(C):
    """Core business logic - shared by both handlebar and run_time triggers.

    Per-stock flow each bar:
    1. Increment hold_days
    2. Risk control (SL/TP/HD) -> sell if triggered
    3. Per-stock time exit: sell if hold_days >= hold_days_max
    4. If any slots empty -> breadth check -> select new stocks -> buy
    """
    global _last_trade_date, _today_buys, _account, _stock_pool, _stock_list
    global _kline_cache_tech, _kline_cache_date, _risk_config, _hold_days_max

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
        _hold_days_max = _params.get('hold_days_max', 10)
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
    sold = qmt_runner.check_risk(C, _account, _hold_days, _risk_config, bar_date=today, strategy_name='V75J')
    for code in sold:
        _hold_days.pop(code, None)

    # 3. Per-stock time exit: sell if hold_days >= rebalance_days
    holdings = qmt_runner.get_strategy_holdings('v75j', _account)
    for p in holdings:
        code = p['code']
        days = _hold_days.get(code, 0)
        if days >= _hold_days_max:
            if _DEBUG:
                print('[V75J] time exit: %s days=%d >= %d -> SELL' % (code, days, _hold_days_max))
            _account.sell_all(code, strategy_name='v75j')
            _hold_days.pop(code, None)

    # 4. Check if slots are available -> buy
    holdings = qmt_runner.get_strategy_holdings('v75j', _account)
    current_count = len([p for p in holdings if p.get('shares', 0) > 0])
    slots = max_holdings - current_count

    if _DEBUG and holdings:
        for p in holdings:
            print('[V75J] hold: %s shares=%d cost=%.2f days=%d' % (
                p['code'], p['shares'], p['avg_cost'], _hold_days.get(p['code'], 0)))

    if slots > 0:
        # Breadth filter before buying
        breadth = _calc_breadth(C)
        high_thresh = _params.get('breadth_high', 0.50)
        low_thresh = _params.get('breadth_low', 0.30)

        if breadth < low_thresh:
            if _DEBUG:
                print('[V75J] breadth %.4f < %.2f -> SKIP (no buy)' % (breadth, low_thresh))
        else:
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
            if selected:
                kline_data = _kline_cache_tech or {}

                # Filter out currently held stocks and limit up stocks
                held_codes = set(p['code'] for p in holdings)
                filtered = []
                for c in selected:
                    if c in held_codes:
                        continue
                    # Limit up check: price >= prev_close * threshold (board-specific)
                    # Skip vol=0 bars (empty data after market close)
                    if c in kline_data:
                        df = kline_data[c]
                        if len(df) >= 2:
                            today_close = df['close'].iloc[-1]
                            today_vol = df['volume'].iloc[-1] if 'volume' in df.columns else 0
                            prev_close_val = df['close'].iloc[-2]
                            if today_close > 0 and prev_close_val > 0 and today_vol > 0:
                                if c.startswith(('300', '301', '688', '689')):
                                    limit_price = prev_close_val * 1.195
                                elif c.startswith(('8', '4')):
                                    limit_price = prev_close_val * 1.295
                                else:
                                    limit_price = prev_close_val * 1.095
                                if today_close >= limit_price:
                                    if _DEBUG:
                                        print('[V75J] SKIP %s: limit up (%.2f >= %.2f)' % (c, today_close, limit_price))
                                    continue
                    filtered.append(c)

                # Price filter: skip if price > MAX_STOCK_PRICE
                max_stock_price = _params.get('max_stock_price', 0)
                price_filtered = []
                for c in filtered:
                    if max_stock_price > 0 and c in kline_data:
                        df = kline_data[c]
                        if len(df) > 0:
                            price = df['close'].iloc[-1]
                            if price > max_stock_price:
                                if _DEBUG:
                                    print('[V75J] SKIP %s: price %.2f > max %d' % (c, price, max_stock_price))
                                continue
                    price_filtered.append(c)
                filtered = price_filtered

                # Capital filter: skip if cannot afford 1 lot (100 shares)
                capital_per_stock = _params.get('capital', 50000) * _params.get('max_per_stock', 0.35)
                capital_filtered = []
                for c in filtered:
                    if c in kline_data:
                        df = kline_data[c]
                        if len(df) > 0:
                            price = df['close'].iloc[-1]
                            if price > 0 and price * 100 > capital_per_stock:
                                if _DEBUG:
                                    print('[V75J] SKIP %s: cannot afford 1 lot (price=%.2f, need=%.0f, have=%.0f)' % (c, price, price*100, capital_per_stock))
                                continue
                    capital_filtered.append(c)

                # Select top N from filtered list
                buy_list = capital_filtered[:slots]

                if buy_list:
                    target = {}
                    for code in buy_list:
                        target[code] = max_per_stock

                    if _DEBUG:
                        print('[V75J] buy targets:')
                        for code, w in target.items():
                            print('  %s weight=%.4f' % (code, w))

                    bought = qmt_runner.execute_buy(C, _account, target, bar_date=today, capital=_params.get('capital', 50000), strategy_name='V75J')

                    # Add newly bought stocks to hold_days
                    for code in bought:
                        _hold_days[code] = 1

    # Persist hold_days after all changes
    persist_hold_days('v75j', _hold_days, today)




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
        # Limit up check will be done in buy_list generation
        # 20-day average amount
        amounts = df['amount'].values[-20:]
        avg_amount = np.nanmean(amounts)
        if np.isnan(avg_amount) or avg_amount <= 0:
            continue
        scored.append((code, avg_amount))

    if not scored:
        return []

    # Debug: show kline details for top 10 to diagnose data freshness
    if _DEBUG:
        print('[V75J] kline data check (top 10 by avg_amount):')
        for code, avg in sorted(scored, key=lambda x: x[1], reverse=True)[:10]:
            df = kline_data[code]
            n_days = len(df)
            last_amt = df['amount'].values[-1]
            last_date = str(df.index[-1])[:10]
            print('  %s kline_days=%d last_date=%s last_amt=%.1f avg_amt=%.1f' % (
                code, n_days, last_date, last_amt/1e8, avg/1e8))
        # Diagnostic: show raw amount for 600183 (known discrepancy)
        diag_code = '600183.SH'
        if diag_code in kline_data:
            df = kline_data[diag_code]
            print('[V75J] DIAG 600183.SH: kline_days=%d' % len(df))
            for i in range(-5, 0):
                if abs(i) <= len(df):
                    d = str(df.index[i])[:10]
                    a = df['amount'].values[i]
                    c = df['close'].values[i]
                    print('  %s close=%.2f amount=%.1fB' % (d, c, a/1e8))

    # Sort by avg amount descending (more liquid first)
    scored.sort(key=lambda x: x[1], reverse=True)

    if _DEBUG:
        print('[V75J] liquidity candidates=%d, top 10:' % len(scored))
        for code, amt in scored[:10]:
            print('  %s avg_amount=%.0f' % (code, amt))

    ranked = [code for code, _ in scored]
    return ranked
