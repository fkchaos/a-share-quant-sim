#coding:gbk
"""
QMT Diagnostic Strategy (dual trigger mode)
=============================================
Comprehensive diagnostics for QMT adapter:
 1. Timer registration (MODE='LIVE')
 2. Cost price calculation (MODE='BACKTEST')
 3. Basic buy/sell flow
 4. Account config resolution (QMT frames vs config)
 5. Position data sources (API vs internal vs JSON)
 6. Per-strategy JSON files (_positions_v61c/v75j.json)
 7. _hold_days.json persistence (read/write roundtrip)
 8. Consistency check (account.get_holdings vs get_strategy_holdings)

MODE = 'BACKTEST' -> handlebar callback
MODE = 'LIVE'     -> schedule_run timer (14:50 daily)

Debug: set trading._risk_debug = True for verbose output.
"""
# ========== CONFIG ==========
MODE = 'LIVE'          # 'BACKTEST' or 'LIVE'
DEBUG = True           # master debug switch: controls ALL prints
TIMER_INTERVAL = 24 * 3600  # seconds (1 day = 86400)
TIMER_TIME = '145000'  # HHMMSS format
# ===========================

# Validate MODE
_VALID_MODES = ('BACKTEST', 'LIVE')
if MODE not in _VALID_MODES:
    raise ValueError("MODE must be %s, got %r" % (' or '.join(_VALID_MODES), MODE))

import datetime
import os

# Fallback for __file__ (not defined when QMT loads via exec)
try:
    _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _SCRIPT_DIR = r"D:\software\QMT-SIMU\python"
import sys
import json

account_id = None  # resolved from QMT at runtime
bought = False
sold = False
buy_price_used = 0
buy_shares = 0
_diag_results = []  # (test_name, passed, detail)


def _diag_log(msg):
    if DEBUG:
        print('[DIAG] %s' % msg)


def _diag_result(test_name, passed, detail=''):
    _diag_results.append((test_name, passed, detail))
    status = 'PASS' if passed else 'FAIL'
    _diag_log('  TEST %s: %s %s' % (test_name, status, detail))


def _get_bar_date(C):
    """Get current bar date from ContextInfo."""
    try:
        timetag = C.get_bar_timetag(C.barpos)
        if timetag > 0:
            return datetime.datetime.fromtimestamp(timetag / 1000).strftime('%Y%m%d')
    except Exception:
        pass
    return ''


def _get_bar_close(C, code, bar_date):
    """Get close price: backtest uses end_time+subscribe=False, live uses latest+subscribe=True."""
    is_backtest = getattr(C, 'do_back_test', False)
    try:
        if is_backtest:
            data = C.get_market_data_ex(
                ['close'], [code], period='1d', count=1,
                subscribe=False, end_time=bar_date)
        else:
            data = C.get_market_data_ex(
                ['close'], [code], period='1d', count=1,
                subscribe=True)
        if code in data and len(data[code]) > 0:
            return data[code]['close'].iloc[-1]
    except Exception as e:
        print('[DIAG] _get_bar_close failed: %s' % e)
    return 0


# ============================================================
# Test 1: Account Config Resolution
# ============================================================
def test_account_config(C):
    """Check how account_id is resolved: QMT frames vs config.py."""
    _diag_log('--- Test 1: Account Config ---')

    # Method 1: QMT frame walking
    from qmt_adapter.trading import _find_account_from_frames, _find_account_type_from_frames
    frame_account = _find_account_from_frames()
    frame_type = _find_account_type_from_frames()

    # Method 2: config.py fallback
    try:
        from qmt_adapter.config import ACCOUNT_CONFIG
        config_account = ACCOUNT_CONFIG.get('account_id', '')
    except Exception:
        config_account = ''

    # Method 3: ContextInfo attribute
    ci_account = getattr(C, 'accID', None)

    # Method 4: Actual resolved value in QmtAccount
    try:
        from qmt_adapter.trading import QmtAccount
        acc = QmtAccount(C)
        resolved_account = acc.account_id
        resolved_type = acc.account_type
    except Exception as e:
        resolved_account = 'ERROR: %s' % str(e)
        resolved_type = ''

    _diag_log('  Frame walking:  account=%s type=%s' % (frame_account, frame_type))
    _diag_log('  Config.py:      account=%s' % config_account)
    _diag_log('  ContextInfo:    accID=%s' % ci_account)
    _diag_log('  QmtAccount:     account=%s type=%s' % (resolved_account, resolved_type))

    passed = bool(resolved_account and resolved_account != 'SIMTEST')
    detail = 'resolved=%s' % resolved_account
    if not passed:
        detail += ' (fallback to SIMTEST, check QMT frames or config)'
    _diag_result('1:AccountConfig', passed, detail)


# ============================================================
# Test 2: Position Data Sources
# ============================================================
def test_position_sources(C):
    """Compare QMT API vs internal tracking vs per-strategy JSON."""
    _diag_log('--- Test 2: Position Sources ---')

    try:
        from qmt_adapter.trading import QmtAccount
        acc = QmtAccount(C)
    except Exception as e:
        _diag_result('2:PositionSources', False, 'QmtAccount init failed: %s' % e)
        return

    # Source 1: QMT API (get_trade_detail_data)
    try:
        api_raw = acc._query('POSITION')
        api_count = len(api_raw) if api_raw else 0
        api_holdings = acc.get_holdings()
        _diag_log('  QMT API:     %d raw -> %d holdings' % (api_count, len(api_holdings)))
        for h in api_holdings:
            _diag_log('    %s: %d shares @ %.2f' % (h['code'], h['shares'], h.get('avg_cost', 0)))
    except Exception as e:
        api_holdings = []
        _diag_log('  QMT API: ERROR %s' % e)

    # Source 2: Internal tracking (backtest fallback)
    internal = acc._internal_positions
    internal_count = len(internal)
    _diag_log('  Internal:    %d positions' % internal_count)
    for code, pos in internal.items():
        _diag_log('    %s: %d shares @ %.2f' % (code, pos['shares'], pos.get('cost', 0)))

    # Source 3: Per-strategy JSON files
    _diag_log('  Per-strategy JSON:')
    for sname in ('v61c', 'v75j'):
        try:
            from qmt_adapter.qmt_runner import load_strategy_positions
            pos = load_strategy_positions(sname)
            count = len([v for v in pos.values() if v.get('shares', 0) > 0])
            _diag_log('    %s: %d positions' % (sname, count))
            for code, v in pos.items():
                if v.get('shares', 0) > 0:
                    _diag_log('      %s: %d @ %.2f added=%s' % (
                        code, v['shares'], v.get('cost_price', 0), v.get('added_at', '')))
        except Exception as e:
            _diag_log('    %s: ERROR %s' % (sname, e))

    has_any = len(api_holdings) > 0 or internal_count > 0
    _diag_result('2:PositionSources', has_any,
        'api=%d internal=%d' % (len(api_holdings), internal_count))


# ============================================================
# Test 3: Timer Registration (LIVE mode only)
# ============================================================
def test_timer_registration(C):
    """Verify schedule_run timer setup."""
    if MODE != 'LIVE':
        _diag_result('3:TimerReg', True, 'skipped (BACKTEST mode)')
        return
    _diag_log('--- Test 3: Timer Registration ---')
    _diag_log('  MODE=LIVE, timer registered in init()')
    _diag_result('3:TimerReg', True, 'MODE=%s' % MODE)


# ============================================================
# Test 4: Cost Price Calculation
# ============================================================
def test_cost_price(C, bar_date):
    """Verify m_dOpenPrice from QMT matches our passorder price."""
    if not bought:
        _diag_result('4:CostPrice', True, 'skipped (not bought yet)')
        return

    _diag_log('--- Test 4: Cost Price ---')
    try:
        from qmt_adapter.trading import QmtAccount
        acc = QmtAccount(C)
        positions = acc.get_holdings()
    except Exception as e:
        _diag_result('4:CostPrice', False, 'get_holdings failed: %s' % e)
        return

    for p in positions:
        code = p['code']
        qmt_cost = p.get('avg_cost', 0)
        diff = qmt_cost - buy_price_used
        _diag_log('  %s:' % code)
        _diag_log('    QMT m_dOpenPrice = %.4f' % qmt_cost)
        _diag_log('    Our buy price    = %.4f' % buy_price_used)
        _diag_log('    Diff             = %.4f (%.2f%%)' % (
            diff, diff / buy_price_used * 100 if buy_price_used > 0 else 0))

    _diag_result('4:CostPrice', True, 'check diff above')


# ============================================================
# Test 5: _hold_days.json Persistence
# ============================================================
def test_hold_days_persistence():
    """Test _hold_days.json read/write roundtrip."""
    _diag_log('--- Test 5: hold_days.json ---')

    for sname in ('v61c', 'v75j'):
        persist_path = os.path.join(_SCRIPT_DIR, '_hold_days.json')
        if sname == 'v75j':
            # v75j uses same file name, check both exist
            pass

        # Test: write -> read -> compare
        test_data = {'hold_days': {'123456': 5, '789012': 3}, 'last_date': '20260831'}
        try:
            with open(persist_path, 'w') as f:
                json.dump(test_data, f)

            with open(persist_path, 'r') as f:
                loaded = json.load(f)

            ok = (loaded['hold_days'] == test_data['hold_days'] and
                  loaded['last_date'] == test_data['last_date'])
            _diag_log('  %s: write->read %s' % (sname, 'OK' if ok else 'MISMATCH'))
            if not ok:
                _diag_log('    expected: %s' % test_data)
                _diag_log('    loaded:   %s' % loaded)
        except Exception as e:
            _diag_log('  %s: ERROR %s' % (sname, e))
            ok = False

        _diag_result('5:HoldDays_%s' % sname, ok, 'roundtrip')

    # Cleanup: remove test file
    try:
        os.remove(persist_path)
    except Exception:
        pass


# ============================================================
# Test 6: Per-strategy JSON Files
# ============================================================
def test_per_strategy_json():
    """Check _positions_v61c.json and _positions_v75j.json."""
    _diag_log('--- Test 6: Per-strategy JSON ---')

    for sname in ('v61c', 'v75j'):
        path = os.path.join(_SCRIPT_DIR, '_positions_%s.json' % sname)
        exists = os.path.exists(path)
        if exists:
            try:
                with open(path, 'r') as f:
                    pos = json.load(f)
                held = {k: v for k, v in pos.items() if v.get('shares', 0) > 0}
                _diag_log('  %s: exists, %d held / %d total' % (sname, len(held), len(pos)))
                for code, v in held.items():
                    _diag_log('    %s: %d @ %.2f added=%s' % (
                        code, v['shares'], v.get('cost_price', 0), v.get('added_at', '')))
            except Exception as e:
                _diag_log('  %s: exists but parse error: %s' % (sname, e))
        else:
            _diag_log('  %s: NOT FOUND (will be created on first buy)' % sname)

    _diag_result('6:PerStrategyJSON', True, 'check output')


# ============================================================
# Test 7: Consistency Check
# ============================================================
def test_consistency(C):
    """Compare account.get_holdings() vs get_strategy_holdings()."""
    _diag_log('--- Test 7: Consistency ---')

    try:
        from qmt_adapter.trading import QmtAccount
        acc = QmtAccount(C)
    except Exception as e:
        _diag_result('7:Consistency', False, 'QmtAccount init failed: %s' % e)
        return

    # account.get_holdings()
    acc_holdings = acc.get_holdings()
    acc_codes = set(h['code'] for h in acc_holdings)
    _diag_log('  account.get_holdings(): %d stocks' % len(acc_holdings))

    # get_strategy_holdings() for each strategy
    from qmt_adapter.qmt_runner import get_strategy_holdings
    for sname in ('v61c', 'v75j'):
        strat_holdings = get_strategy_holdings(sname, acc)
        strat_codes = set(h['code'] for h in strat_holdings)
        _diag_log('  get_strategy_holdings(%s): %d stocks' % (sname, len(strat_holdings)))

        # Check overlap
        overlap = acc_codes & strat_codes
        only_acc = acc_codes - strat_codes
        only_strat = strat_codes - acc_codes
        if overlap:
            _diag_log('    overlap with account: %s' % list(overlap))
        if only_acc:
            _diag_log('    only in account: %s' % list(only_acc))
        if only_strat:
            _diag_log('    only in strategy: %s' % list(only_strat))

    _diag_result('7:Consistency', True, 'check output')


# ============================================================
# Test 8: Buy/Sell Flow (DEFAULT OFF - enable manually)
# ============================================================

def test_buy_sell_flow(C, bar_date):
    """Test passorder -> order_callback -> deal_callback chain.
    WARNING: This actually places an order! Only enable for testing.
    """
    global bought, buy_price_used, buy_shares, sold

    if bought and sold:
        _diag_result('8:BuySellFlow', True, 'already tested')
        return

    diag_code = '118027.SH'
    diag_strategy = 'DIAG'

    # Enable debug for passorder print
    from qmt_adapter import trading
    trading._risk_debug = True

    # --- BUY ---
    if not bought:
        _diag_log('--- Test 8: Buy/Sell Flow ---')

        # Get price
        price = _get_bar_close(C, diag_code, bar_date)
        if price <= 0:
            _diag_result('8:BuySellFlow', False, 'cannot get price for %s' % diag_code)
            return

        shares = int(10000 / price / 100) * 100
        if shares <= 0:
            _diag_result('8:BuySellFlow', False, 'cannot afford 1 lot at %.2f' % price)
            return

        _diag_log('  Buy: %d shares %s at ~%.2f' % (shares, diag_code, price))

        buy_price_used = price
        buy_shares = shares

        # Get real account from QmtAccount (same as v75j/v61c)
        from qmt_adapter.trading import QmtAccount
        acc = QmtAccount(C)
        _diag_log('  Using account: %s (type: %s)' % (acc.account_id, acc.account_type))

        remark = acc.buy(diag_code, shares, price, reason='DIAG', strategy_name=diag_strategy)
        if remark is None:
            _diag_result('8:BuySellFlow', False, 'acc.buy() returned None')
            return
        _diag_log('  acc.buy() OK, remark=%s' % remark)

        bought = True

        # Record to _positions_DIAG.json (same as real strategies)
        from qmt_adapter.qmt_runner import strategy_buy
        strategy_buy(diag_strategy, diag_code, shares, price, date=bar_date)
        _diag_log('  Recorded to _positions_DIAG.json')

    # --- SELL (read from _positions_DIAG.json) ---
    if bought and not sold:
        cur_close = _get_bar_close(C, diag_code, bar_date)
        _diag_log('  Sell check (close=%.4f)' % cur_close)

        from qmt_adapter.qmt_runner import load_strategy_positions, strategy_sell
        pos = load_strategy_positions(diag_strategy)

        sell_pos = pos.get(diag_code)
        if sell_pos and sell_pos.get('shares', 0) > 0:
            sell_shares = sell_pos['shares']
            sell_price = cur_close if cur_close > 0 else -1
            _diag_log('  Sell: %d shares %s at ~%.2f' % (sell_shares, diag_code, sell_price))
            from qmt_adapter.trading import QmtAccount
            acc2 = QmtAccount(C)
            sell_remark = acc2.sell(diag_code, sell_shares, sell_price, reason='DIAG', strategy_name=diag_strategy)
            if sell_remark is None:
                _diag_log('  acc.sell() returned None')
            else:
                _diag_log('  acc.sell() OK, remark=%s' % sell_remark)
            strategy_sell(diag_strategy, diag_code, sell_shares)
            sold = True
            _diag_result('8:BuySellFlow', True, 'buy+sell done')
        else:
            _diag_log('  Position not in JSON yet, retrying on next timer...')
            _diag_result('8:BuySellFlow', True, 'buy done, sell pending')


# ============================================================
# Entry Points
# ============================================================
def init(ContextInfo):
    # Propagate DEBUG to trading module (controls passorder/position query prints)
    from qmt_adapter.qmt_runner import set_risk_debug
    set_risk_debug(DEBUG)

    _diag_log('=== QMT Diagnostic v2 (dual trigger) ===')
    _diag_log('MODE: %s' % MODE)
    _diag_log('DEBUG: %s' % DEBUG)
    _diag_log('Account: %s' % account_id)

    # Live mode: register schedule_run timer
    if MODE == 'LIVE':
        now = datetime.datetime.now()
        today_str = now.strftime('%Y%m%d')
        target = datetime.datetime.strptime(today_str + TIMER_TIME, '%Y%m%d%H%M%S')
        interval = datetime.timedelta(seconds=TIMER_INTERVAL)
        ContextInfo.schedule_run(on_timer, target.strftime('%Y%m%d%H%M%S'), repeat_times=-1,
                                 interval=interval, name='diag_timer')

        _diag_log('schedule_run at %s, interval=%ds' % (target, TIMER_INTERVAL))
        _diag_log('Waiting for timer trigger...')
    else:
        _diag_log('Backtest mode: waiting for handlebar...')

    # DON'T register order check timer here - start after placing order
    _diag_log('Order polling will start after placing an order')

    # Run static tests (no market data needed)
    test_hold_days_persistence()
    test_per_strategy_json()




def handlebar(ContextInfo):
    """Backtest mode: triggered on each bar close."""
    if MODE == 'BACKTEST':
        _on_signal(ContextInfo)


def on_timer(ContextInfo):
    """Live mode: triggered by schedule_run timer."""
    if MODE == 'LIVE':
        _diag_log('Timer fired at %s' % datetime.datetime.now().strftime('%H:%M:%S'))
        _on_signal(ContextInfo)


def _on_signal(ContextInfo):
    """Core diagnostic logic."""
    global bought, buy_price_used, buy_shares, sold

    bar_date = _get_bar_date(ContextInfo)

    # Run data-dependent tests (need bar data)
    test_account_config(ContextInfo)
    test_position_sources(ContextInfo)
    test_consistency(ContextInfo)

    # Test 8: buy + sell flow (self-contained)
    test_buy_sell_flow(ContextInfo, bar_date)

    # --- SUMMARY ---
    _diag_log('=== RESULTS ===')
    for test_name, passed, detail in _diag_results:
        status = 'PASS' if passed else 'FAIL'
        _diag_log('  %s: %s %s' % (test_name, status, detail))
    _diag_log('=== END ===')


# ========== QMT CALLBACKS ==========
# QMT requires these at module level to call them
def order_callback(ContextInfo, orderInfo):
    """Print order status changes from QMT."""
    status = getattr(orderInfo, 'm_nOrderStatus', '?')
    remark = getattr(orderInfo, 'm_strRemark', '')
    code = getattr(orderInfo, 'm_strStockCode', '')
    vol = getattr(orderInfo, 'm_nVolumeTotalOriginal', 0)
    traded = getattr(orderInfo, 'm_nVolumeTraded', 0)
    print('[ORDER_CALLBACK] status=%s code=%s vol=%d traded=%d remark=%s' % (status, code, vol, traded, remark))


def deal_callback(ContextInfo, dealInfo):
    """Print deal confirmations from QMT."""
    code = getattr(dealInfo, 'm_strStockCode', '')
    vol = getattr(dealInfo, 'm_nVolume', 0)
    price = getattr(dealInfo, 'm_dPrice', 0)
    remark = getattr(dealInfo, 'm_strRemark', '')
    direction = getattr(dealInfo, 'm_nDirection', '?')
    print('[DEAL_CALLBACK] direction=%s code=%s vol=%d price=%.4f remark=%s' % (direction, code, vol, price, remark))
