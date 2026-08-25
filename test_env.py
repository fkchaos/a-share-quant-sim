#coding:gbk
"""
test_env.py - QMT环境测试
放到QMT安装目录\\python\\下，运行验证环境是否正常。
"""

class G():
    pass
g = G()

def init(ContextInfo):
    print('='*50)
    print('[TEST] QMT...')
    print('='*50)
    
    import sys
    print('[1] Python: %s' % sys.version)
    print('[1] PASS')
    
    try:
        timetag_to_datetime
        print('[2] PASS - QMT API OK')
    except NameError:
        print('[2] FAIL - not in QMT')
        return
    
    try:
        import numpy as np
        import pandas as pd
        print('[3] PASS - numpy/pandas OK')
    except ImportError as e:
        print('[3] FAIL: %s' % str(e))
        return
    
    try:
        from qmt_adapter.trading import QmtAccount
        print('[4] PASS - qmt_adapter.trading OK')
    except ImportError as e:
        print('[4] FAIL: %s' % str(e))
        return
    
    try:
        from qmt_adapter.data import qmt_to_our_format
        print('[5] PASS - qmt_adapter.data OK')
    except ImportError as e:
        print('[5] FAIL: %s' % str(e))
        return
    
    try:
        acct = QmtAccount(ContextInfo, 'test', 'stock')
        print('[6] PASS - QmtAccount OK')
    except Exception as e:
        print('[6] FAIL: %s' % str(e))
        return
    
    try:
        data = ContextInfo.get_market_data_ex(
            ['open', 'high', 'low', 'close', 'volume', 'amount'],
            ['000001.SZ'],
            period='1d', count=5, subscribe=False,
        )
        if '000001.SZ' in data and len(data['000001.SZ']) > 0:
            print('[7] PASS - 000001.SZ %d bars' % len(data['000001.SZ']))
        else:
            print('[7] WARN - empty data')
    except Exception as e:
        print('[7] WARN: %s' % str(e))
    
    print('='*50)
    print('[TEST] ALL PASS')
    print('='*50)


def handlebar(ContextInfo):
    pass
