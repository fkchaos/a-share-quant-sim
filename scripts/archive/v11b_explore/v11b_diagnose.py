#!/usr/bin/env python3
"""Diagnose v11b max drawdown."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['BACKTEST_DATA_DIR'] = '/root/data'

from core.config import config as core_config
from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.data import load_and_build_panel
from scripts.run_backtest import run_backtest
import pandas as pd, numpy as np

loaded, codes = load_and_build_panel('2021-01-01', None, need_open=False, need_hl=True, market_filter=core_config.market)
close_panel = loaded[0]
volume_panel = loaded[1]
amount_panel = loaded[2]
high_panel = loaded[4] if len(loaded) > 4 else None
low_panel = loaded[5] if len(loaded) > 5 else None
factors = calc_factors_panel(close_panel, volume_panel, amount_panel, high_panel=high_panel, low_panel=low_panel)

FACTOR_GROUPS = {
    'momentum': {'mom_20': 0.30, 'mom_10': 0.25, 'rsi_14': 0.25, 'high_low_range': 0.20},
    'volatility': {'vol_60': 0.30, 'vol_20': 0.25, 'vol_10': 0.25, 'boll_width_20': 0.20},
    'reversal': {'rev_10': 0.30, 'rev_5': 0.25, 'rsi_6': 0.25, 'boll_pos_10': 0.20},
}

template = factors[list(factors.keys())[0]]
dates = template.index
stocks = template.columns
selection_count = pd.DataFrame(0.0, index=dates, columns=stocks)
for gname, weights in FACTOR_GROUPS.items():
    gf = {k: v for k, v in factors.items() if k in weights}
    gs = composite_score(gf, weights)
    for date in dates:
        if date not in gs.index: continue
        ds = gs.loc[date].dropna()
        if len(ds) < 4: continue
        for s in ds.nlargest(4).index:
            if s in selection_count.columns:
                selection_count.loc[date, s] += 1.0

m, nav, trades = run_backtest(close_panel, selection_count, top_n=12, rebalance_freq=20, stop_loss=0.20, max_position=0.10, label='v11b')

nav_series = nav
cummax = nav_series.cummax()
dd = (cummax - nav_series) / cummax
max_dd_date = dd.idxmax()
print(f'Max DD date: {max_dd_date.date()}')
print(f'Max DD: {dd.max():.1%}')
print(f'NAV at max DD: {nav_series.loc[max_dd_date]:.0f}')

trades_df = trades
if len(trades_df) > 0:
    mask = (trades_df['date'] >= '2022-01-01') & (trades_df['date'] <= '2022-06-01')
    print(trades_df[mask].to_string())
