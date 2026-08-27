#coding:gbk
"""
QMT Diagnostic Strategy (dual trigger mode)

Use to verify:
1. Timer registration (MODE='LIVE')
2. Cost price calculation (MODE='BACKTEST')
3. Basic buy/sell flow

MODE = 'BACKTEST' -> handlebar callback
MODE = 'LIVE'     -> schedule_run timer (14:50 daily)
"""
# ========== CONFIG ==========
MODE = 'BACKTEST'       # 'BACKTEST' or 'LIVE'
TIMER_INTERVAL = 24 * 3600  # seconds (1 day = 86400)
TIMER_TIME = '145000'  # HHMMSS format
# =============================

# Validate MODE
_VALID_MODES = ('BACKTEST', 'LIVE')
if MODE not in _VALID_MODES:
    raise ValueError("MODE must be %s, got %r" % (' or '.join(_VALID_MODES), MODE))

import datetime

account_id = 'SIMTEST'
bought = False
buy_price_used = 0
buy_shares = 0

def _get_bar_date(C):
    """Get current bar date from ContextInfo."""
    try:
        timetag = C.get_bar_timetag(C.barpos)
        if timetag > 0:
            return datetime.datetime.fromtimestamp(timetag / 1000).strftime('%Y%m%d')
    except Exception:
        pass
    return ''

def _get_bar_close(C, code, bar_date):
    """Get close price for bar date using official QMT way."""
    data = C.get_market_data_ex(
        ['close'], [code],
        period='1d', count=1,
        subscribe=False,
        end_time=bar_date
    )
    if code in data and len(data[code]) > 0:
        return data[code]['close'].iloc[-1]
    return 0

def init(ContextInfo):
    print('[DIAG] === QMT Diagnostic (dual trigger) ===')
    print('[DIAG] MODE: %s' % MODE)
    print('[DIAG] Account: %s' % account_id)

    # Live mode: register schedule_run timer
    if MODE == 'LIVE':
        now = datetime.datetime.now()
        today_str = now.strftime('%Y%m%d')
        target = datetime.datetime.strptime(today_str + TIMER_TIME, '%Y%m%d%H%M%S')
        if target <= now:
            target = target + datetime.timedelta(days=1)
        interval = datetime.timedelta(seconds=TIMER_INTERVAL)
        ContextInfo.schedule_run(on_timer, target, repeat_times=-1,
                                 interval=interval, name='diag_timer')
        print('[DIAG] schedule_run at %s, interval=%ds' % (target, TIMER_INTERVAL))
        print('[DIAG] Waiting for timer trigger...')
    else:
        print('[DIAG] Backtest mode: waiting for handlebar...')


def handlebar(ContextInfo):
    """Backtest mode: triggered on each bar close."""
    if MODE == 'BACKTEST':
        _on_signal(ContextInfo)


def on_timer(ContextInfo):
    """Live mode: triggered by schedule_run timer."""
    if MODE == 'LIVE':
        print('[DIAG] Timer fired at %s' % datetime.datetime.now().strftime('%H:%M:%S'))
        _on_signal(ContextInfo)


def _on_signal(ContextInfo):
    """Core diagnostic logic."""
    global bought, buy_price_used, buy_shares

    bar_date = _get_bar_date(ContextInfo)

    if bought:
        acc = getattr(ContextInfo, 'accID', account_id)
        positions = get_trade_detail_data(acc, 'stock', 'POSITION')
        if not positions:
            positions = get_trade_detail_data(account_id, 'stock', 'POSITION')

        cur_close = _get_bar_close(ContextInfo, '600584.SH', bar_date)

        print('[DIAG] --- Bar %s ---' % bar_date)
        print('[DIAG] Close price: %.4f' % cur_close)
        print('[DIAG] Position count: %d' % len(positions))

        try:
            accounts = get_trade_detail_data(acc, 'stock', 'ACCOUNT')
            for a in accounts:
                print('[DIAG] Balance: %.2f  Available: %.2f' % (
                    getattr(a, 'm_dBalance', 0), getattr(a, 'm_dAvailable', 0)))
        except Exception as e:
            print('[DIAG] Account query failed: %s' % str(e))

        for p in positions:
            code = p.m_strInstrumentID + '.' + p.m_strExchangeID
            vol = p.m_nVolume
            open_price = p.m_dOpenPrice
            settlement = p.m_dSettlementPrice
            profit = p.m_dPositionProfit
            print('[DIAG] %s:' % code)
            print('[DIAG]   m_dOpenPrice    = %.4f (QMT cost)' % open_price)
            print('[DIAG]   m_dSettlement   = %.4f' % settlement)
            print('[DIAG]   m_nVolume       = %d' % vol)
            print('[DIAG]   m_dPositionPnL  = %.4f' % profit)
            print('[DIAG]   Our buy price   = %.4f' % buy_price_used)
            print('[DIAG]   Cur close price = %.4f' % cur_close)
            print('[DIAG]   Price diff      = %.4f (%.2f%%)' % (
                open_price - buy_price_used,
                (open_price - buy_price_used) / buy_price_used * 100 if buy_price_used > 0 else 0))
        return

    code = '600584.SH'
    print('[DIAG] === Attempting Buy ===')
    print('[DIAG] Stock: %s  Bar date: %s' % (code, bar_date))

    price = _get_bar_close(ContextInfo, code, bar_date)
    if price <= 0:
        print('[DIAG] ERROR: cannot get price for %s' % code)
        return

    data = ContextInfo.get_market_data_ex(['volume'], [code], count=1, subscribe=False, end_time=bar_date)
    vol = 0
    if code in data and len(data[code]) > 0:
        vol = data[code]['volume'].iloc[-1]
    print('[DIAG] Bar close price: %.2f' % price)
    print('[DIAG] Bar volume: %.0f' % vol)

    target_value = 10000
    shares = int(target_value / price / 100) * 100
    if shares <= 0:
        print('[DIAG] ERROR: cannot afford 1 lot at %.2f' % price)
        return

    print('[DIAG] Buy: %d shares at ~%.2f (target value=%.0f)' % (shares, price, target_value))

    buy_price_used = price
    buy_shares = shares

    now = datetime.datetime.now()
    remark = 'DIAG-%s' % now.strftime('%H%M%S')
    passorder(
        23, 1101, account_id, code,
        14, -1, shares,
        'DIAG', 1, remark, ContextInfo
    )
    print('[DIAG] passorder sent, remark=%s' % remark)
    print('[DIAG] Waiting for execution...')
    bought = True
