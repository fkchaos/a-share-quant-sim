#coding:gbk
"""
config.py - QMT Common Configuration

All configurable parameters for QMT strategies.
NOTE: Runs in QMT built-in Python 3.6, must be 3.6.8 compatible.
"""

# ========== ACCOUNTS ==========
# Account-level config: which strategy runs on which account.
ACCOUNTS = {
    1: {'strategy': 'v61c'},
    2: {'strategy': 'v75j'},
}

# ========== STRATEGIES ==========
# Strategy-level config: all parameters grouped by strategy name.
STRATEGIES = {
    'v61c': {
        # Risk control
        'stop_loss': -0.08,
        'take_profit': 0.25,
        'hold_days_max': 5,
        # Position management (per-strategy capital, not account total)
        'capital': 100000,
        'max_holdings': 5,
        'max_per_stock': 0.20,
        # Rebalance
        'rebalance_days': 5,
        'sell_out_of': 15,
        # Daily buy limit
        'max_daily_buy': 5,
    },
    'v75j': {
        # Risk control
        'stop_loss': -0.08,
        'take_profit': 0.25,
        'hold_days_max': 20,
        # Position management (per-strategy capital, not account total)
        'capital': 100000,
        'max_holdings': 3,
        'max_per_stock': 0.33,
        # Breadth filter
        'breadth_high': 0.50,
        'breadth_low': 0.30,
        # Daily buy limit
        'max_daily_buy': 3,
    },
}


# ========== MODE ==========
# Per-strategy position tracking (temporary: separates positions per strategy)
PER_STRATEGY_POSITIONS = True

# ========== MARKET DATA ==========
MARKET_CONFIG = {
    'period': '1d',              # K-line period: 1d/1w/1mon
    'dividend_type': 'front',    # Dividend type: front/back/none
    'count': -1,                 # K-line count, -1=all available
    'subscribe': True,           # Auto-download data from QMT server
}

# ========== BACKWARD COMPAT ==========
# Legacy aliases (deprecated, prefer STRATEGIES[name] instead)
ACCOUNT_CONFIG = {
    'account_id': 'SIMTEST',
    'account_type': 'STOCK',
}

RISK_CONFIG = STRATEGIES['v75j']
V61C_RISK_CONFIG = STRATEGIES['v61c']
REBALANCE_CONFIG = {
    'rebalance_days': 5,
    'max_daily_buy': 5,
}
SELL_OUT_OF_CONFIG = {
    'sell_out_of': 15,
}


def get_strategy_params(name):
    """Get strategy parameters by name. Returns copy to prevent mutation."""
    if name not in STRATEGIES:
        raise ValueError("Unknown strategy: %s (available: %s)" % (
            name, ', '.join(STRATEGIES.keys())))
    return dict(STRATEGIES[name])
