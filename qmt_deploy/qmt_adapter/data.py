#coding:gbk
"""
data.py - QMT Market Data -> Our Format

Converts QMT K-line data to our standard format.
NOTE: Runs in QMT built-in Python 3.6, must be 3.6.8 compatible.
"""
import pandas as pd
import numpy as np
from datetime import datetime


def qmt_to_our_format(klines, period='1d'):
    """
    Convert QMT K-line data to our standard format.
    
    QMT returns: [open, high, low, close, volume, amount, ...
    Our format: DataFrame with columns [open, high, low, close, vol, amount]
                index = date
    """
    if not klines:
        return pd.DataFrame()
    
    data = []
    for k in klines:
        try:
            if hasattr(k, 'time'):
                dt = datetime.fromtimestamp(k.time)
            else:
                dt = datetime.now()
            
            row = {
                'open': k.open if hasattr(k, 'open') else 0,
                'high': k.high if hasattr(k, 'high') else 0,
                'low': k.low if hasattr(k, 'low') else 0,
                'close': k.close if hasattr(k, 'close') else 0,
                'vol': k.volume if hasattr(k, 'volume') else 0,
                'amount': k.amount if hasattr(k, 'amount') else 0,
            }
            data.append((dt, row))
        except Exception:
            continue
    
    if not data:
        return pd.DataFrame()
    
    df = pd.DataFrame([r for _, r in data], index=[d for d, _ in data])
    df.index.name = 'date'
    return df


def load_kline(C, stock_list, days=120):
    """Load K-line data for multiple stocks from QMT."""
    from .config import MARKET_CONFIG
    period = MARKET_CONFIG.get('period', '1d')
    dividend_type = MARKET_CONFIG.get('dividend_type', 'front')
    count = MARKET_CONFIG.get('count', -1)
    
    subscribe = MARKET_CONFIG.get('subscribe', True)
    if subscribe:
        from .trading import _get_qmt_func
        _get_qmt_func()
        from .trading import get_trade_detail_data
        for code in stock_list:
            try:
                C.subscribe_quote(code, period=period, count=-1)
            except Exception:
                pass
    
    result = {}
    for code in stock_list:
        try:
            klines = C.get_market_data_ex(
                ['open', 'high', 'low', 'close', 'volume', 'amount'], [code], period=period, count=count,
                subscribe=False
            )
            if code in klines and len(klines[code]) > 0:
                result[code] = qmt_to_our_format(klines[code], period)
        except Exception:
            continue
    
    return result


def get_close_price(C, code):
    """Get latest close price for a stock."""
    try:
        data = C.get_market_data_ex(
            ['close'], [code], period='1d', count=1,
            subscribe=False
        )
        if code in data and len(data[code]) > 0:
            return data[code]['close'].iloc[-1]
    except Exception:
        pass
    return 0.0


def get_close_prices_batch(C, stock_list):
    """Get close prices for multiple stocks."""
    try:
        data = C.get_market_data_ex(
            ['close'], stock_list, period='1d', count=1,
            subscribe=False
        )
        result = {}
        for code in stock_list:
            if code in data and len(data[code]) > 0:
                result[code] = data[code]['close'].iloc[-1]
        return result
    except Exception:
        return {}


def get_kline_data_multi(C, stock_list, count=10):
    """Get multi-day K-line data for multiple stocks.

    Returns dict: {code: DataFrame(index=date, columns=[close,volume,amount,high,low])}
    Used for turnover calculation (v61c) and liquidity/volume ratio (v75j).
    """
    from .config import MARKET_CONFIG
    period = MARKET_CONFIG.get('period', '1d')
    dividend_type = MARKET_CONFIG.get('dividend_type', 'front')

    fields = ['open', 'high', 'low', 'close', 'volume', 'amount']
    result = {}

    # Batch subscribe for efficiency
    for code in stock_list:
        try:
            C.subscribe_quote(code, period=period, count=count)
        except Exception:
            pass

    # Batch fetch
    try:
        data = C.get_market_data_ex(
            fields, stock_list, period=period, count=count,
            subscribe=True
        )
        _dbg_count = 0
        for code in stock_list:
            if code in data and len(data[code]) > 0:
                df = data[code]
                result[code] = df
                # Debug: show raw data for first 3 stocks
                if _dbg_count < 3:
                    _dbg_count += 1
                    last_close = df["close"].iloc[-1] if "close" in df.columns else 0
                    last_vol = df["volume"].iloc[-1] if "volume" in df.columns else 0
                    last_amount = df["amount"].iloc[-1] if "amount" in df.columns else 0
                    print("  [KLINE] %s: close=%.2f vol=%.0f amount=%.0f rows=%d" % (
                        code, last_close, last_vol, last_amount, len(df)))
    except Exception:
        # Fallback: fetch one by one
        for code in stock_list:
            try:
                klines = C.get_market_data_ex(
                    fields, [code], period=period, count=count,
                    subscribe=False
                )
                if code in klines and len(klines[code]) > 0:
                    result[code] = klines[code]
            except Exception:
                continue

    return result
