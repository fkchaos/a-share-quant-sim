#coding:gbk
"""v75j_qmt.py - V75J entry point (dual trigger mode)

MODE = 'BACKTEST'  -> handlebar callback (回测+实盘通用)
MODE = 'LIVE'      -> schedule_run timer (仅实盘，支持集合竞价等场景)

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
    handlebar as _handlebar,
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


def handlebar(C):
    """Backtest mode: triggered on each bar close."""
    if MODE == 'BACKTEST':
        _on_signal(C)


def on_timer(C):
    """Live mode: triggered by schedule_run timer."""
    if MODE == 'LIVE':
        _on_signal(C)
