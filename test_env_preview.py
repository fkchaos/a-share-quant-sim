#coding:gbk
"""
test_env.py - QMT环境测试
放到QMT安装目录\python\下，运行验证环境是否正常。
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
        print('[2] PASS - QMT API')
    except NameError:
        print('[2] FAIL - not in QMT')
        return
    
    try:
        import numpy as np
        import pandas as pd
        print('[3] PASS - numpy/pandas')
    except ImportError as e:
        print('[3] FAIL: %s' % str(e))
        return
    
    try:
        from qmt_adapter.trading import QmtAccount
        print('[4] PASS - qmt_adapter.trading')
    except ImportError as e:
        print('[4] FAIL: %s' % str(e))
        return
    
    try:
        from qmt_adapter.data import qmt_to_our_format, load_kline_from_qmt
        print('[5] PASS - qmt_adapter.data')
    except ImportError as e:
        print('[5] FAIL: %s' % str(e))
        return
    
    try:
        acct = QmtAccount(ContextInfo, 'testS', 'stock')
        print('[6] PASS - QmtAccount init')
    except Exception as e:
        print('[6] FAIL: %s' % str(e))
        return
    
    try:
        cash = acct.get_cash()
        print('[7] PASS - get_cash() = %.2f' % cash)
    except Exception as e:
        print('[7] FAIL: %s' % str(e))
        return
    
    try:
        holdings = acct.get_holdings()
        print('[8] PASS - get_holdings() = %d stocks' % len(holdings))
        if holdings:
            for code, vol in list(holdings.items())[:3]:
                print('       %s: %d' % (code, vol))
    except Exception as e:
        print('[8] FAIL: %s' % str(e))
        return
    
    try:
        detail = acct.get_position_detail('000001.SZ')
        if detail:
            print('[9] PASS - get_position_detail(000001.SZ)')
        else:
            print('[9] PASS - get_position_detail(000001.SZ) = None')
    except Exception as e:
        print('[9] FAIL: %s' % str(e))
        return
    
    try:
        data = ContextInfo.get_market_data_ex(
            ['open', 'high', 'low', 'close', 'volume', 'amount'],
            ['000001.SZ'],
            period='1d', count=5, subscribe=False,
        )
        if '000001.SZ' in data and len(data['000001.SZ']) > 0:
            df = qmt_to_our_format(data, '000001.SZ')
            print('[10] PASS - qmt_to_our_format %d rows' % len(df))
            print('       cols: %s' % list(df.columns))
        else:
            print('[10] WARN - empty data')
    except Exception as e:
        print('[10] FAIL: %s' % str(e))
    
    print('='*50)
    print('[TEST] DONE')
    print('='*50)


def handlebar(ContextInfo):
    pass
