#coding:gbk
"""
QMT Cost Price Diagnostic Strategy

Experiment: Buy 1 stock, check m_dOpenPrice vs expected price.
Run on FRESH SIMTEST account (reset before testing).

Purpose: Verify how QMT calculates m_dOpenPrice for prType=14.
"""
import datetime

account_id = 'SIMTEST'
bought = False
buy_price_used = 0
buy_shares = 0

def init(ContextInfo):
    print('[DIAG] === QMT Cost Price Diagnostic ===')
    print('[DIAG] Purpose: verify m_dOpenPrice calculation')
    print('[DIAG] Account: %s' % account_id)
    print('[DIAG] Waiting for first bar to buy...')

def handlebar(ContextInfo):
    global bought, buy_price_used, buy_shares

    if bought:
        # After buy: check position cost
        # get_trade_detail_data is also a global function
        # In backtest, accID might differ from our account_id
        acc = getattr(ContextInfo, 'accID', account_id)
        positions = get_trade_detail_data(acc, 'stock', 'POSITION')
        if not positions:
            # Try with our account_id directly
            positions = get_trade_detail_data(account_id, 'stock', 'POSITION')
        print('[DIAG] === Position Check ===')
        print('[DIAG] Position count: %d' % len(positions))

        # Also check account balance
        acc = getattr(ContextInfo, 'accID', account_id)
        try:
            accounts = get_trade_detail_data(acc, 'stock', 'ACCOUNT')
            for a in accounts:
                print('[DIAG] Balance: %.2f  Available: %.2f' % (
                    getattr(a, 'm_dBalance', 0), getattr(a, 'm_dAvailable', 0)))
        except Exception as e:
            print('[DIAG] Account query failed: %s' % str(e))

        for p in positions:
            code = p.m_strInstrumentID + '.' + p.m_strExchangeID
            vol = p.m_nVolume
            open_price = p.m_dOpenPrice
            settlement = p.m_dSettlementPrice
            profit = p.m_dPositionProfit
            print('[DIAG] %s:' % code)
            print('[DIAG]   m_dOpenPrice    = %.4f (QMT cost)' % open_price)
            print('[DIAG]   m_dSettlement   = %.4f' % settlement)
            print('[DIAG]   m_nVolume       = %d' % vol)
            print('[DIAG]   m_dPositionPnL  = %.4f' % profit)
            print('[DIAG]   Our buy price   = %.4f' % buy_price_used)
            print('[DIAG]   Price diff      = %.4f (%.2f%%)' % (
                open_price - buy_price_used,
                (open_price - buy_price_used) / buy_price_used * 100 if buy_price_used > 0 else 0))
        return

    # First bar: buy 1 stock
    code = '600584.SH'  # Pick a known stock
    print('[DIAG] === Attempting Buy ===')
    print('[DIAG] Stock: %s' % code)

    # Get current price
    data = ContextInfo.get_market_data_ex(['close', 'volume', 'amount'], [code], count=1)
    if code in data and len(data[code]) > 0:
        df = data[code]
        price = df['close'].iloc[-1]
        vol = df['volume'].iloc[-1]
        print('[DIAG] Current price: %.2f' % price)
        print('[DIAG] Current volume: %.0f' % vol)
    else:
        print('[DIAG] ERROR: cannot get price for %s' % code)
        return

    # Calculate shares (10% position = 10000 CNY)
    target_value = 10000
    shares = int(target_value / price / 100) * 100
    if shares <= 0:
        print('[DIAG] ERROR: cannot afford 1 lot at %.2f' % price)
        return

    print('[DIAG] Buy: %d shares at ~%.2f (target value=%.0f)' % (shares, price, target_value))

    # Record our expected price
    buy_price_used = price
    buy_shares = shares

    # Execute buy - passorder is a global function, not a ContextInfo method
    now = datetime.datetime.now()
    remark = 'DIAG-%s' % now.strftime('%H%M%S')
    passorder(
        23,                     # opType: buy
        1101,                   # orderType: single
        account_id,
        code,
        14,                     # prType: counterparty
        -1,                     # price: -1 for prType=14
        shares,
        'DIAG',
        1,                      # quickTrade
        remark,
        ContextInfo
    )
    print('[DIAG] passorder sent, remark=%s' % remark)
    print('[DIAG] Waiting for execution...')
    bought = True