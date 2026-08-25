#coding:gbk
"""v61c_qmt.py - V61C entry point"""
from qmt_adapter.v61c_strategy import (
    init as _init,
    handlebar as _handlebar,
)

def init(C):
    _init(C)

def handlebar(C):
    _handlebar(C)
