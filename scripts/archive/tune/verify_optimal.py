#!/usr/bin/env python3
"""验证最优参数组合：rebal_freq=10 + vol_target=0.10"""
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
        'label': label,
        'annual_return': round(float(ann_ret), 6),
        'sharpe_ratio': round(float(sharpe), 4),
        'sortino_ratio': round(float(sortino), 4),
        'max_drawdown': round(float(max_dd), 6),
        'calmar_ratio': round(float(calmar), 4),
        'total_trades': len(trades_df),
        'stop_loss_trades': int(sl_count),
        'total_cost': round(total_cost, 2),
        'final_value': round(float(nav.iloc[-1]), 2),
    }


if __name__ == "__main__":
    t0 = time.time()
    print("验证最优参数组合")
    print("=" * 50)

    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    factors = calc_factors_panel(close_panel, volume_panel, amount_panel)
    score = composite_score(factors, DEFAULT_FACTOR_WEIGHTS)
    print(f"Panel: {len(close_panel)} x {len(stocks)}")

    combos = [
        ('current',         dict(top_n=12, rebal_freq=20, stop_loss=0.20, vol_target=0.20)),
        ('rf10_vt010',      dict(top_n=12, rebal_freq=10, stop_loss=0.20, vol_target=0.10)),
        ('rf10_vt010_sl15', dict(top_n=12, rebal_freq=10, stop_loss=0.15, vol_target=0.10)),
    ]

    results = {}
    for label, params in combos:
        m = run_bt(close_panel, score, **params, label=label)
        results[label] = m
        print(f"\n{label}:")
        print(f"  Sharpe={m['sharpe_ratio']:.3f}  Ret={m['annual_return']:.2%}  DD={m['max_drawdown']:.2%}")
        print(f"  Cost=¥{m['total_cost']:,.0f}  Trades={m['total_trades']}  SL={m['stop_loss_trades']}")

    # 对比
    base = results['current']
    for label in ['rf10_vt010', 'rf10_vt010_sl15']:
        m = results[label]
        print(f"\n{label} vs current:")
        print(f"  Sharpe: {base['sharpe_ratio']:.3f} -> {m['sharpe_ratio']:.3f} ({m['sharpe_ratio']-base['sharpe_ratio']:+.3f})")
        print(f"  DD:     {base['max_drawdown']:.2%} -> {m['max_drawdown']:.2%} ({(m['max_drawdown']-base['max_drawdown'])*100:+.2f}%)")
        print(f"  Cost:   ¥{base['total_cost']:,.0f} -> ¥{m['total_cost']:,.0f} (¥{m['total_cost']-base['total_cost']:+,.0f})")

    out_path = os.path.join(DATA_DIR, "backtest_results", "optimal_combo.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n保存: {out_path}")
