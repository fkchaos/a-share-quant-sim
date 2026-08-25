#coding:gbk
"""v61c_debug.py - V61C Debug entry point"""
print('[ENTRY] v61c_debug.py loaded')

from qmt_adapter.v61c_debug_strategy import (
    init as _init,
    handlebar as _handlebar,
)

print('[ENTRY] imported strategy functions')

def init(C):
    print('[ENTRY] init called')
    _init(C)
    print('[ENTRY] init done')

def handlebar(C):
    print('[ENTRY] handlebar called')
    _handlebar(C)
    print('[ENTRY] handlebar done')
