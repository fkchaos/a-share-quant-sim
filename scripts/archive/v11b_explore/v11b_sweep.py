#!/usr/bin/env python3
"""v11b parameter sweep: GROUP_TOP_N = 3, 4, 5."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['BACKTEST_DATA_DIR'] = '/root/data'

from core.config import config as core_config
from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.data import load_and_build_panel
from scripts.run_backtest import run_backtest, walk_forward
import pandas as pd, numpy as np, time

FACTOR_GROUPS = {
    'momentum': {'mom_20': 0.30, 'mom_10': 0.25, 'rsi_14': 0.25, 'high_low_range': 0.20},
    'volatility': {'vol_60': 0.30, 'vol_20': 0.25, 'vol_10': 0.25, 'boll_width_20': 0.20},
    'reversal': {'rev_10': 0.30, 'rev_5': 0.25, 'rsi_6': 0.25, 'boll_pos_10': 0.20},
}

def build_union_score(factors, group_top_n):
    first_key = list(factors.keys())[0]
    template = factors[first_key]
    dates = template.index
    stocks = template.columns
    selection_count = pd.DataFrame(0.0, index=dates, columns=stocks)
    for gname, weights in FACTOR_GROUPS.items():
        gf = {k: v for k, v in factors.items() if k in weights}
        gs = composite_score(gf, weights)
        for date in dates:
            if date not in gs.index: continue
            ds = gs.loc[date].dropna()
            if len(ds) < group_top_n: continue
            for s in ds.nlargest(group_top_n).index:
                if s in selection_count.columns:
                    selection_count.loc[date, s] += 1.0
    return selection_count

def walk_forward_union(close_panel, volume_panel, amount_panel, high_panel, low_panel,
                       group_top_n, label):
    from core.factors import calc_factors_panel
    dates = close_panel.index
    n = len(dates)
    fold_results = []
    fold = 0

    def _slice_panel(panel, idx):
        if panel is not None:
            return panel.loc[idx]
        return None

    train_end = 252
    while train_end + 63 <= n:
        fold += 1
        train_start = max(0, train_end - 252)
        test_start = train_end
        test_end = min(n, train_end + 63)

        window_dates = dates[train_start:test_end]
        sub_close = close_panel.loc[window_dates]
        sub_volume = _slice_panel(volume_panel, window_dates)
        sub_amount = _slice_panel(amount_panel, window_dates)
        sub_high = _slice_panel(high_panel, window_dates)
        sub_low = _slice_panel(low_panel, window_dates)

        sub_factors = calc_factors_panel(sub_close, sub_volume, sub_amount,
                                         high_panel=sub_high, low_panel=sub_low)
        sub_score = build_union_score(sub_factors, group_top_n)

        test_dates = dates[test_start:test_end]
        _warmup = train_end - train_start
        m, nav, _ = run_backtest(sub_close, sub_score, top_n=12, rebalance_freq=20,
                                  stop_loss=0.20, label=f'{label}_fold{fold}',
                                  warmup_days=_warmup)

        fold_results.append({
            'fold': fold,
            'ann_return': m['annual_return'],
            'sharpe': m['sharpe_ratio'],
            'max_dd': m['max_drawdown'],
        })
        train_end += 63

    avg_ret = np.mean([r['ann_return'] for r in fold_results])
    avg_sharpe = np.mean([r['sharpe'] for r in fold_results])
    pos = sum(1 for r in fold_results if r['ann_return'] > 0)
    return fold_results, avg_ret, avg_sharpe, pos

# Load data
print("Loading data...")
loaded, codes = load_and_build_panel('2021-01-01', None, need_open=False, need_hl=True,
                                     market_filter=core_config.market)
close_panel = loaded[0]
volume_panel = loaded[1]
amount_panel = loaded[2]
high_panel = loaded[4] if len(loaded) > 4 else None
low_panel = loaded[5] if len(loaded) > 5 else None

for top_n in [3, 4, 5]:
    t0 = time.time()
    print(f"\n--- GROUP_TOP_N={top_n} ---")
    wf, avg_ret, avg_sharpe, pos = walk_forward_union(
        close_panel, volume_panel, amount_panel, high_panel, low_panel,
        group_top_n=top_n, label=f'v11b_top{top_n}'
    )
    elapsed = time.time() - t0
    print(f"  WF: {len(wf)} folds | Avg Ret={avg_ret:.1%} | Avg Sharpe={avg_sharpe:.2f} | Pos={pos}/{len(wf)} ({elapsed:.0f}s)")
