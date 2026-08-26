#coding:gbk
"""
qmt_diagnostic.py - QMT Environment Diagnostic Strategy

NOT a real trading strategy. Run this first to verify QMT environment.
Tests: API calls, data access, account, industry mapping, breadth calc.

Usage: Load this file in QMT as a strategy and run backtest (1 day).
"""
from datetime import datetime

_results = []

def _log(tag, msg, ok=True):
    prefix = '[OK]' if ok else '[FAIL]'
    line = '%s %s: %s' % (prefix, tag, msg)
    print(line)
    _results.append((tag, ok, msg))


def init(C):
    """Run all diagnostic checks."""
    global _results
    _results = []

    print('=' * 60)
    print('QMT Diagnostic - %s' % datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    print('=' * 60)

    # --- 1. QMT built-in functions ---
    import sys
    frame = sys._getframe(0)
    has_gtd = 'get_trade_detail_data' in frame.f_globals
    has_po = 'passorder' in frame.f_globals
    _log('API', 'get_trade_detail_data available', has_gtd)
    _log('API', 'passorder available', has_po)

    # --- 2. get_market_data_ex ---
    test_code = '000001.SZ'
    try:
        data = C.get_market_data_ex(
            ['open', 'high', 'low', 'close', 'volume', 'amount'],
            [test_code], period='1d', count=5, subscribe=False
        )
        if test_code in data and len(data[test_code]) > 0:
            df = data[test_code]
            last_close = df['close'].iloc[-1]
            _log('DATA', 'get_market_data_ex OK, %s close=%.2f' % (test_code, last_close))
        else:
            _log('DATA', 'get_market_data_ex returned empty for %s' % test_code, False)
    except Exception as e:
        _log('DATA', 'get_market_data_ex error: %s' % e, False)

    # --- 3. Batch data fetch ---
    test_batch = ['000001.SZ', '600519.SH', '000858.SZ']
    try:
        data = C.get_market_data_ex(
            ['close'], test_batch, period='1d', count=1, subscribe=False
        )
        got = len([c for c in test_batch if c in data and len(data[c]) > 0])
        _log('DATA', 'batch fetch: %d/%d stocks returned data' % (got, len(test_batch)), got == len(test_batch))
    except Exception as e:
        _log('DATA', 'batch fetch error: %s' % e, False)

    # --- 4. get_instrument_detail ---
    try:
        detail = C.get_instrument_detail(test_code)
        if detail:
            name = detail.get('InstrumentName', 'N/A')
            industry = detail.get('IndustryClassification', 'N/A')
            _log('INST', '%s name=%s industry=%s' % (test_code, name, industry))
        else:
            _log('INST', 'get_instrument_detail returned None', False)
    except Exception as e:
        _log('INST', 'get_instrument_detail error: %s' % e, False)

    # --- 5. Float shares (QMT) ---
    try:
        detail = C.get_instrument_detail(test_code)
        if detail:
            fs = detail.get('FloatVolume', 0)
            _log('INST', 'FloatVolume of %s = %s' % (test_code, fs), fs > 0)
        else:
            _log('INST', 'Cannot get FloatVolume (no detail)', False)
    except Exception as e:
        _log('INST', 'FloatVolume error: %s' % e, False)

    # --- 6. ZZ1800 pool ---
    try:
        from qmt_adapter.qmt_data import ZZ1800_STOCKS
        _log('POOL', 'ZZ1800 has %d stocks' % len(ZZ1800_STOCKS), len(ZZ1800_STOCKS) > 100)
    except Exception as e:
        _log('POOL', 'ZZ1800 load error: %s' % e, False)

    # --- 7. Account info ---
    try:
        from qmt_adapter.trading import QmtAccount
        acct = QmtAccount(C)
        cash = acct.get_cash()
        holdings = acct.get_holdings()
        total = acct.get_total_value()
        _log('ACCT', 'account_id=%s cash=%.0f holdings=%d total=%.0f' % (
            acct.account_id, cash, len(holdings), total))
    except Exception as e:
        _log('ACCT', 'Account error: %s' % e, False)

    # --- 8. Tech industry scan (v75j) ---
    try:
        from qmt_adapter.qmt_data import ZZ1800_STOCKS
        tech_count = 0
        tech_sectors = ['电子', '计算机', '通信', '传媒']
        for code in ZZ1800_STOCKS[:200]:  # scan first 200 for speed
            try:
                d = C.get_instrument_detail(code)
                if d and d.get('IndustryClassification', '') in tech_sectors:
                    tech_count += 1
            except Exception:
                pass
        _log('TECH', 'found %d tech stocks in first 200 (est. %d total)' % (
            tech_count, int(tech_count * len(ZZ1800_STOCKS) / 200)))
    except Exception as e:
        _log('TECH', 'Tech scan error: %s' % e, False)

    # --- Summary ---
    print('')
    print('=' * 60)
    passed = sum(1 for _, ok, _ in _results if ok)
    total = len(_results)
    print('DIAGNOSTIC RESULT: %d/%d passed' % (passed, total))
    if passed == total:
        print('All checks passed. QMT environment is ready.')
    else:
        print('Some checks failed. See [FAIL] items above.')
    print('=' * 60)


def handlebar(C):
    """Nothing to do - all checks run in init."""
    pass
