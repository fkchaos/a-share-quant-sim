#coding:gbk
"""v75j_qmt.py - V75J entry point"""
from qmt_adapter.v75j_strategy import (
    init as _init,
    handlebar as _handlebar,
)

def init(C):
    _init(C)

def handlebar(C):
    _handlebar(C)
