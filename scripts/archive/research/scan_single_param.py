#!/usr/bin/env python3
"""
单参数扫描：找到对回撤最敏感的参数
====================================
每次只变一个参数，其余固定为 current 最优值。
"""
import sys, os, time, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

import numpy as np
import pandas as pd

from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.account import PortfolioState, buy, sell, check_stop_loss, portfolio_value
from core.config import config as core_config, DEFAULT_FACTOR_WEIGHTS

# current 最优参数
BASE_PARAMS = {
    'top_n': 12,
    'rebal_freq': 20,
    'stop_loss': 0.20,
    'use_vol_scaling': True,
    'vol_target': 0.20,
    'max_position': 0.10,
    'max_industry_weight': 0.25,
}

# 各参数的扫描范围
SCAN_RANGES = {
    'stop_loss':       [0.10, 0.12, 0.15, 0.18, 0.20, 0.22, 0.25, 0.30],
    'rebal_freq':      [5, 10, 15, 20, 25, 30, 40],
    'vol_target':      [0.10, 0.12, 0.15, 0.18, 0.20, 0.22, 0.25, 0.30],
    'max_industry_weight': [0.15, 0.20, 0.25, 0.30, 0.35, 0.40],
    'top_n':           [8, 10, 12, 15, 18, 20],
    'max_position':    [0.05, 0.08, 0.10, 0.12, 0.15],
}


def load_panel():
    files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
    all_data = {}
    for f in files:
        code = f.replace(".csv", "")
        df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
        df = df[(df.index >= '2021-01-01')]
        if len(df) > 0:
            all_data[code] = df
    valid_ = {}
    for code, df in all_data.items():
        if df.index.min() <= pd.Timestamp('2021-01-01') + pd.Timedelta(days=30) and \
           df.index.max() >= pd.Timestamp('2026-05-29') - pd.Timedelta(days=30):
            valid_[code] = df
    close_panel = pd.DataFrame({c: d['close'] for c, d in valid_.items()})
    volume_panel = pd.DataFrame({c: d['volume'] for c, d in valid_.items()})
    amount_panel = pd.DataFrame({c: d['amount'] for c, d in valid_.items()})
    common_dates = close_panel.dropna(how='all').index
    common_dates = common_dates[(common_dates >= '2021-01-01') & (common_dates <= '2026-05-29')]
    return (
        close_panel.loc[common_dates].sort_index(),
        volume_panel.loc[common_dates].sort_index(),
        amount_panel.loc[common_dates].sort_index()
    ), list(valid_.keys())


def run_bt(close_panel, score, top_n=12, rebal_freq=20, stop_loss=0.20,
           use_vol_scaling=True, vol_target=0.20, max_position=0.10,
           max_industry_weight=0.25, label='default'):
    from core.config import config
    state = PortfolioState(cash=config.costs.initial_capital,
                           initial_capital=config.costs.initial_capital)
    dates = close_panel.index
    nav_list = []

    for i, date in enumerate(dates):
        if i < 120:
            nav_list.append(config.costs.initial_capital)
            continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else config.costs.initial_capital)
            continue

        price_data = close_panel.loc[date]
        state = check_stop_loss(state, date, price_data)

        if (i - 120) % rebal_freq == 0 and date in score.index:
            day_score = score.loc[date].dropna()
            valid_idx = day_score.index.isin(price_data.dropna().index)
            day_score = day_score[valid_idx]

            if use_vol_scaling:
                returns = close_panel.pct_change()
                stock_vol = returns.rolling(20).std().loc[date]
                vol_scale = vol_target / (stock_vol * np.sqrt(252))
                vol_scale = vol_scale.clip(0.1, 3.0)
                day_score = day_score * vol_scale.reindex(day_score.index).fillna(1)

            top_stocks = day_score.nlargest(top_n).index.tolist()
            if top_stocks:
                current_pv = portfolio_value(state, date, price_data)
                for c in list(state.holdings.keys()):
                    if c not in top_stocks and c in price_data.index:
                        p = price_data[c]
                        if not pd.isna(p) and p > 0:
                            state = sell(state, c, p, date, reason='SELL')

                weights = {c: 1.0 / len(top_stocks) for c in top_stocks}
                for c in top_stocks:
                    if c not in state.holdings and c in price_data.index:
                        p = price_data[c]
                        if not pd.isna(p) and p > 0:
                            w = weights.get(c, 1.0 / len(top_stocks))
                            target_val = min(current_pv * w, current_pv * max_position)
                            adj_p = p * (1 + config.costs.slippage_rate)
                            shares = int(target_val / adj_p / 100) * 100
                            if shares > 0:
                                state = buy(state, c, p, date, shares=shares)

        dv = portfolio_value(state, date, price_data)
        nav_list.append(dv)

    nav = pd.Series(nav_list, index=dates[:len(nav_list)])
    rets = nav.pct_change().dropna()
    years = max(len(nav) / 252, 0.01)
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = rets.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    downside = rets[rets < 0]
    downside_vol = downside.std() * np.sqrt(252) if len(downside) > 1 else 0
    sortino = ann_ret / downside_vol if downside_vol > 0 else 0
    max_dd = ((nav.cummax() - nav) / nav.cummax()).max()
    calmar = ann_ret / max_dd if max_dd > 0 else 0

    trades_df = pd.DataFrame(state.trade_log)
    sl_count = len(trades_df[trades_df['action'] == 'STOP_LOSS']) if len(trades_df) > 0 else 0
    total_cost = float(trades_df['cost'].sum()) if len(trades_df) > 0 else 0

    return {
        'annual_return': round(float(ann_ret), 6),
        'sharpe_ratio': round(float(sharpe), 4),
        'sortino_ratio': round(float(sortino), 4),
        'max_drawdown': round(float(max_dd), 6),
        'calmar_ratio': round(float(calmar), 4),
        'total_trades': len(trades_df),
        'stop_loss_trades': int(sl_count),
        'total_cost': round(total_cost, 2),
    }


if __name__ == "__main__":
    t0 = time.time()
    print("=" * 70)
    print("单参数敏感性扫描")
    print("=" * 70)

    print("\n加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    factors = calc_factors_panel(close_panel, volume_panel, amount_panel)
    score = composite_score(factors, DEFAULT_FACTOR_WEIGHTS)
    print(f"Panel: {len(close_panel)} x {len(stocks)}, 因子: {len(factors)}")

    all_results = {}

    for param_name, values in SCAN_RANGES.items():
        print(f"\n{'─' * 50}")
        print(f"扫描参数: {param_name} ({len(values)} 个值)")
        print(f"{'─' * 50}")

        results = []
        for v in values:
            p = dict(BASE_PARAMS)
            p[param_name] = v

            m = run_bt(close_panel, score, **p, label=f"{param_name}={v}")
            results.append((v, m))

            print(f"  {param_name}={v:>5}: Sharpe={m['sharpe_ratio']:.3f}  "
                  f"Ret={m['annual_return']:+.2%}  DD={m['max_drawdown']:.2%}  "
                  f"SL={m['stop_loss_trades']:>3d}  Cost=¥{m['total_cost']:>10,.0f}")

        all_results[param_name] = results

        # 找出这个参数的最优值（夏普最高）
        best = max(results, key=lambda x: x[1]['sharpe_ratio'])
        print(f"  → 最优: {param_name}={best[0]}  Sharpe={best[1]['sharpe_ratio']:.3f}")

    # 汇总
    print(f"\n{'=' * 70}")
    print(f"{'参数敏感性汇总':^70}")
    print(f"{'─' * 70}")
    print(f"{'参数':<22} {'当前值':>8} {'最优值':>8} {'当前夏普':>10} {'最优夏普':>10} {'提升':>8}")
    print(f"{'─' * 70}")

    for param_name, results in all_results.items():
        cur_val = BASE_PARAMS[param_name]
        cur_sharpe = None
        for v, m in results:
            if v == cur_val:
                cur_sharpe = m['sharpe_ratio']
                break
        if cur_sharpe is None:
            cur_sharpe = results[0][1]['sharpe_ratio']

        best_val, best_m = max(results, key=lambda x: x[1]['sharpe_ratio'])
        best_sharpe = best_m['sharpe_ratio']
        improvement = best_sharpe - cur_sharpe

        fmt_val = f"{cur_val}" if isinstance(cur_val, int) else f"{cur_val:.2f}"
        fmt_best = f"{best_val}" if isinstance(best_val, int) else f"{best_val:.2f}"
        marker = " ***" if improvement > 0.05 else " *" if improvement > 0.02 else ""
        print(f"{param_name:<22} {fmt_val:>8} {fmt_best:>8} {cur_sharpe:>10.4f} {best_sharpe:>10.4f} {improvement:>+8.4f}{marker}")

    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")

    # 保存
    out_path = os.path.join(DATA_DIR, "backtest_results", "param_sensitivity.json")
    with open(out_path, 'w') as f:
        json.dump({k: [{'value': v, **m} for v, m in results]
                   for k, results in all_results.items()}, f, indent=2, ensure_ascii=False)
    print(f"结果已保存: {out_path}")
