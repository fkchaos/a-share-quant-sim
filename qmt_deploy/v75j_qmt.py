#coding:gbk
"""v75j_qmt.py - V75J entry point (dual trigger mode)

MODE = 'BACKTEST'  -> handlebar callback (backtest + live universal)
MODE = 'LIVE'      -> schedule_run timer (live only, supports call auction etc)

Business logic lives in v75j_strategy.on_signal(), shared by both modes.
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

DEBUG = True

from qmt_adapter.v75j_strategy import (
    init as _init,
    on_signal as _on_signal,
    set_debug as _set_debug,
)
from qmt_adapter.qmt_runner import set_risk_debug as _set_risk_debug


def init(C):
    """QMT init - called once at strategy start."""
    _set_debug(DEBUG)
    _set_risk_debug(DEBUG)
    _init(C)

    # Live mode: register schedule_run timer
    if MODE == 'LIVE':
        from datetime import datetime, timedelta
        now = datetime.now()
        # Build target datetime from today + TIMER_TIME
        today_str = now.strftime('%Y%m%d')
        target = datetime.strptime(today_str + TIMER_TIME, '%Y%m%d%H%M%S')
        interval = timedelta(seconds=TIMER_INTERVAL)
        C.schedule_run(on_timer, target.strftime('%Y%m%d%H%M%S'), repeat_times=-1,
                        interval=interval, name='signal_timer')
        if DEBUG:
            print('[INIT] MODE=LIVE, schedule_run at %s, interval=%ds' % (target, TIMER_INTERVAL))
    
    # Register order check timer (every 10 seconds)
    C.run_time('check_order_timer', '10nSecond', '2026-01-01 00:00:00')


def check_order_timer(C):
    """Periodic order check - cancel stale orders and re-order."""
    from qmt_adapter.trading import check_order_timeout, process_pending_reorders
    check_order_timeout(C, max_seconds=60)
    process_pending_reorders(C)


def handlebar(C):
    """Backtest mode: triggered on each bar close."""
    if MODE == 'BACKTEST':
        _on_signal(C)


def on_timer(C):
    """Live mode: triggered by schedule_run timer."""
    if MODE == 'LIVE':
        _on_signal(C)
