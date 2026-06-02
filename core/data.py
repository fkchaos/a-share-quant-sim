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
from datetime import datetime, timedelta

# Default data directory: project-root/data unless overridden
_BASE_DIR = os.environ.get("BACKTEST_DATA_DIR", None)
if _BASE_DIR is None:
    _BASE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

DEFAULT_START = "2021-01-01"


def filter_stocks(codes, market_filter=None, daily_dir=None, as_of_date=None):
    """根据市场过滤规则筛选股票代码。

    Parameters
    ----------
    codes       : list[str] — 原始股票代码列表
    market_filter : MarketFilter — 市场过滤配置（None=不过滤）
    daily_dir   : str — CSV 目录路径（退市检测需要读文件）
    as_of_date  : datetime/date — 退市判断基准日期（默认今天）

    Returns
    -------
    list[str] — 过滤后的股票代码
    """
    if market_filter is None:
        return codes

    filtered = list(codes)

    # ── 1. 包含前缀白名单 ──
    if market_filter.include_prefixes:
        filtered = [c for c in filtered if any(c.startswith(p) for p in market_filter.include_prefixes)]

    # ── 2. 排除前缀黑名单（优先级高于白名单）──
    if market_filter.exclude_prefixes:
        filtered = [c for c in filtered if not any(c.startswith(p) for p in market_filter.exclude_prefixes)]

    # ── 3. 退市/长期停牌排除 ──
    if market_filter.exclude_delisted and daily_dir:
        if as_of_date is None:
            as_of_date = datetime.now().date()
        elif isinstance(as_of_date, str):
            as_of_date = pd.Timestamp(as_of_date).date()

        max_gap = market_filter.delist_max_gap
        active = []
        for code in filtered:
            csv_path = os.path.join(daily_dir, f"{code}.csv")
            if not os.path.exists(csv_path):
                continue
            try:
                df = pd.read_csv(csv_path, index_col='date', parse_dates=True)
                if len(df) == 0:
                    continue
                last_date = df.index[-1].date()
                gap = (as_of_date - last_date).days
                if gap <= max_gap:
                    active.append(code)
            except Exception:
                active.append(code)  # 读文件失败时保守纳入
        filtered = active

    return filtered


def load_and_build_panel(
    start_date=None,
    end_date=None,
    need_open=False,
    need_hl=False,
    daily_dir=None,
    market_filter=None,
):
    """Load daily K-line CSVs and build aligned panels.

    Parameters
    ----------
    start_date   : str, optional — defaults to '2021-01-01'
    end_date     : str, optional — defaults to today
    need_open    : bool — include open_panel (for exec_timing='open')
    need_hl      : bool — include high_panel + low_panel (for short-term factors)
    daily_dir    : str, optional — override CSV directory
    market_filter: MarketFilter, optional — stock market filter config

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

    # ── 1. 读取所有 CSV ──
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

    # ── 2. 市场过滤（板块 + 退市）──
    if market_filter is not None:
        before_count = len(all_data)
        codes = list(all_data.keys())
        filtered_codes = filter_stocks(
            codes, market_filter=market_filter, daily_dir=ddir, as_of_date=ed,
        )
        filtered_set = set(filtered_codes)
        all_data = {c: d for c, d in all_data.items() if c in filtered_set}
        after_count = len(all_data)
        if after_count != before_count:
            print(f"  市场过滤: {before_count} → {after_count} 只 (排除 {before_count - after_count} 只)")

    # ── 3. 完整度过滤 ──
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
    common_dates = common_dates[(common_dates >= pd.Timestamp(sd)) & (common_dates <= pd.Timestamp(ed))]

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
