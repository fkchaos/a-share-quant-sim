#coding:gbk
"""v75j_qmt.py - V75J entry point (dual trigger mode)

MODE = 'BACKTEST'  -> handlebar callback (回测+实盘通用)
MODE = 'LIVE'      -> run_time timer (仅实盘，支持集合竞价等场景)

Business logic lives in v75j_strategy.on_signal(), shared by both modes.
"""
# ========== CONFIG ==========
MODE = 'BACKTEST'       # 'BACKTEST' or 'LIVE'
TIMER_INTERVAL = '1nDay'  # run_time interval (only used when MODE='LIVE')
TIMER_START = '14:50:00'     # run_time start time
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

    # Live mode: register run_time timer
    if MODE == 'LIVE':
        from datetime import datetime
        today_str = datetime.now().strftime('%Y-%m-%d')
        C.run_time('on_timer', TIMER_INTERVAL,
                    today_str + ' ' + TIMER_START)
        if DEBUG:
            print('[INIT] MODE=live, timer=%s start=%s' % (TIMER_INTERVAL, TIMER_START))


def handlebar(C):
    """Backtest mode: triggered on each bar close."""
    if MODE == 'BACKTEST':
        _on_signal(C)


def on_timer(C):
    """Live mode: triggered by run_time timer."""
    if MODE == 'LIVE':
        _on_signal(C)
