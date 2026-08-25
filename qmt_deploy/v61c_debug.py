#coding:gbk
"""v61c_debug.py - V61C Debug entry point"""
from qmt_adapter.v61c_debug_strategy import (
    init as _init,
    handlebar as _handlebar,
)

def init(C):
    _init(C)

def handlebar(C):
    _handlebar(C)
