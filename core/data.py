"""
Panel data loader for backtesting.

Reads daily K-line CSVs and builds aligned panels (DataFrames) of shape (dates × stocks)
for each price/volume field required by factor calculation.

Used by run_backtest.py — NOT used by sim_daily (which uses sim_daily's own single-stock loading).
"""

import os
import sys
import numpy as np
import pandas as pd

# Default data directory: project-root/data unless overridden
_BASE_DIR = os.environ.get("BACKTEST_DATA_DIR", None)
if _BASE_DIR is None:
    _BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

DEFAULT_START = "2021-01-01"


def load_and_build_panel(
    start_date=None,
    end_date=None,
    need_open=False,
    need_hl=False,
    daily_dir=None,
):
    """Load daily K-line CSVs and build aligned panels.

    Parameters
    ----------
    start_date : str, optional — defaults to '2021-01-01'
    end_date   : str, optional — defaults to today
    need_open  : bool — include open_panel (for exec_timing='open')
    need_hl    : bool — include high_panel + low_panel (for short-term factors)
    daily_dir  : str, optional — override CSV directory

    Returns
    -------
    tuple — (close_panel, volume_panel, amount_panel, [open_panel], [high_panel], [low_panel])
            open/high/low appended only when requested
    list  — stock codes
    """
    sd = start_date or DEFAULT_START
    ed = end_date or pd.Timestamp.now().strftime("%Y-%m-%d")
    ddir = daily_dir or os.path.join(_BASE_DIR, "daily")

    files = [f for f in os.listdir(ddir) if f.endswith(".csv")]
    if not files:
        print(f"❌ {ddir} 下没有 CSV 文件，请先运行 update_daily_data.py")
        sys.exit(1)

    all_data = {}
    for f in files:
        code = f.replace(".csv", "")
        try:
            df = pd.read_csv(os.path.join(ddir, f), index_col='date', parse_dates=True)
        except Exception:
            continue
        df = df[(df.index >= sd) & (df.index <= ed)]
        if len(df) > 0:
            all_data[code] = df

    # Filter: require near-full coverage of the backtest period
    valid = {}
    for code, df in all_data.items():
        if df.index.min() <= pd.Timestamp(sd) + pd.Timedelta(days=30) and \
           df.index.max() >= pd.Timestamp(ed) - pd.Timedelta(days=30):
            valid[code] = df

    close_panel  = pd.DataFrame({c: d['close'] for c, d in valid.items()})
    volume_panel = pd.DataFrame({c: d['volume'] for c, d in valid.items()})
    amount_panel = pd.DataFrame({c: d.get('amount', d['close'] * d['volume']) for c, d in valid.items()})

    open_panel = high_panel = low_panel = None
    if need_open or need_hl:
        open_panel = pd.DataFrame({c: d['open'] for c, d in valid.items() if 'open' in d.columns})
    if need_hl:
        high_panel = pd.DataFrame({c: d['high'] for c, d in valid.items() if 'high' in d.columns})
        low_panel  = pd.DataFrame({c: d['low']  for c, d in valid.items() if 'low'  in d.columns})

    common_dates = close_panel.dropna(how='all').index
    common_dates = common_dates[(common_dates >= sd) & (common_dates <= ed)]

    result = (
        close_panel.loc[common_dates].sort_index(),
        volume_panel.loc[common_dates].sort_index(),
        amount_panel.loc[common_dates].sort_index(),
    )
    if need_open and open_panel is not None:
        result += (open_panel.loc[common_dates].sort_index(),)
    if need_hl:
        if high_panel is not None:
            result += (high_panel.loc[common_dates].sort_index(),)
        if low_panel is not None:
            result += (low_panel.loc[common_dates].sort_index(),)

    return result, list(valid.keys())
