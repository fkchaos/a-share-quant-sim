#coding:gbk
"""v75j_qmt.py - V75J entry point"""
# ========== DEBUG SWITCH ==========
# Set True for backtest debugging (prints detailed logs)
# Set False for production
DEBUG = True
# ==================================

from qmt_adapter.v75j_strategy import (
    init as _init,
    handlebar as _handlebar,
    set_debug as _set_debug,
)

def init(C):
    _set_debug(DEBUG)
    _init(C)

def handlebar(C):
    _handlebar(C)
