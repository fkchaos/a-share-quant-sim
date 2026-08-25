#coding:gbk
"""
config.py - QMT Common Configuration

All configurable parameters for QMT strategies.
NOTE: Runs in QMT built-in Python 3.6, must be 3.6.8 compatible.
"""

# Account config
ACCOUNT_CONFIG = {
    'account_id': 'SIMTEST',     # Account ID
    'account_type': 'STOCK',     # Account type
}

# Market data config
MARKET_CONFIG = {
    'period': '1d',              # K-line period: 1d/1w/1mon
    'dividend_type': 'front',    # Dividend type: front/back/none
    'count': -1,                 # K-line count, -1=all available
    'subscribe': True,           # Auto-download data from QMT server
}

# Risk control config
RISK_CONFIG = {
    'stop_loss': -0.08,          # Stop loss threshold
    'take_profit': 0.25,         # Take profit threshold
    'hold_days_max': 20,         # Max hold days
}

# Rebalance config
REBALANCE_CONFIG = {
    'rebalance_days': 5,         # Rebalance every N days
    'max_daily_buy': 5,          # Max new buys per day
}
