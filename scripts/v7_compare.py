#!/usr/bin/env python3
"""
快速策略对比（轻量回测循环，~45s/策略）
参数来源：core.config.STRATEGY_PROFILES
"""
import sys, os, time, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

import numpy as np
import pandas as pd

from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.account import (PortfolioState, buy, sell, check_stop_loss,
                          check_take_profit, apply_holding_decay, portfolio_value)
from core.config import STRATEGY_PROFILES, DEFAULT_FACTOR_WEIGHTS

def load_panel():
    files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
    all_data = {}
    for f in files:
        code = f.replace(".csv", "")
        df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
        df = df[(df.index >= '2021-01-01')]
        if len(df) > 0:
            all_data[code] = df
    valid = {}
    for code, df in all_data.items():
        if df.index.min() <= pd.Timestamp('2021-01-01') + pd.Timedelta(days=30) and \
           df.index.max() >= pd.Timestamp('2026-05-29') - pd.Timedelta(days=30):
            valid[code] = df
    close_panel = pd.DataFrame({c: d['close'] for c, d in valid.items()})
    volume_panel = pd.DataFrame({c: d['volume'] for c, d in valid.items()})
    amount_panel = pd.DataFrame({c: d.get('amount', d['close'] * d['volume']) for c, d in valid.items()})
    common_dates = close_panel.dropna(how='all').index
    common_dates = common_dates[(common_dates >= '2021-01-01') & (common_dates <= '2026-05-29')]
    return (
        close_panel.loc[common_dates].sort_index(),
        volume_panel.loc[common_dates].sort_index(),
        amount_panel.loc[common_dates].sort_index()
    ), list(valid.keys())

def build_score(factors, profile):
    if profile.factor_weights:
        use_weights = profile.factor_weights
    else:
        use_weights = DEFAULT_FACTOR_WEIGHTS
    valid_factors = {k: v for k, v in factors.items() if k in use_weights}
    return composite_score(valid_factors, use_weights)

def run_bt(close_panel, score, profile, label='default'):
    """轻量回测循环"""
    top_n = profile.top_n
    rebal_freq = profile.rebalance_freq
    stop_loss = profile.stop_loss
    max_position = profile.max_position
    use_vol_scaling = profile.use_vol_scaling
    vol_target = profile.vol_target
    use_tp = profile.use_take_profit
    tp_tiers = profile.tp_tiers
    use_decay = profile.use_holding_decay

    state = PortfolioState(cash=200_000, initial_capital=200_000)
    dates = close_panel.index
    nav_list = []

    for i, date in enumerate(dates):
        if i < 120:
            nav_list.append(200_000)
            continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else 200_000)
            continue

        price_data = close_panel.loc[date]
        state = check_stop_loss(state, date, price_data)

        if use_tp and tp_tiers:
            state = check_take_profit(state, date, price_data, tp_tiers)

        if use_decay:
            state = apply_holding_decay(state, date, price_data, rebalance_freq=rebal_freq)

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
                            adj_p = p * (1 + 0.001)  # slippage
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
    win_rate = (rets > 0).sum() / len(rets) if len(rets) > 0 else 0
    trades_df = pd.DataFrame(state.trade_log)
    sl_count = len(trades_df[trades_df['action'] == 'STOP_LOSS']) if len(trades_df) > 0 else 0
    tp_count = len(trades_df[trades_df['action'] == 'TAKE_PROFIT']) if len(trades_df) > 0 else 0
    total_cost = float(trades_df['cost'].sum()) if len(trades_df) > 0 else 0

    return {
        'label': label,
        'annual_return': round(float(ann_ret), 4),
        'sharpe_ratio': round(float(sharpe), 4),
        'sortino_ratio': round(float(sortino), 4),
        'max_drawdown': round(float(max_dd), 4),
        'calmar_ratio': round(float(calmar), 4),
        'win_rate': round(float(win_rate), 4),
        'total_trades': len(trades_df),
        'stop_loss_trades': int(sl_count),
        'take_profit_trades': int(tp_count),
        'total_cost': round(total_cost, 0),
        'final_value': round(float(nav.iloc[-1]), 2),
    }

def main():
    profiles_to_run = [
        "v5_tp_decay",
        "v6a_12f_icir",
        "v6b_8f_pos_ic",
        "v7a_8f_ind40",
        "v7b_8f_ind50",
        "v7c_8f_no_ind",
    ]

    print("=" * 70)
    print("策略对比（资金量：200,000 | 参数来源：STRATEGY_PROFILES）")
    print("=" * 70)

    for name in profiles_to_run:
        p = STRATEGY_PROFILES[name]
        fw = p.factor_weights or {}
        print(f"  {name}: {len(fw) if fw else 29}因子, top_n={p.top_n}, "
              f"rebal={p.rebalance_freq}, sl={p.stop_loss}, "
              f"ind={p.max_industry_weight}, tp={p.use_take_profit}, decay={p.use_holding_decay}")

    print(f"\n[1/3] 加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只股票")

    print(f"\n[2/3] 计算因子...")
    factors = calc_factors_panel(close_panel, volume_panel, amount_panel)

    print(f"\n[3/3] 回测...")
    results = {}

    for name in profiles_to_run:
        profile = STRATEGY_PROFILES[name]
        score = build_score(factors, profile)

        print(f"  ▶ {name}...", end=" ", flush=True)
        t0 = time.time()
        metrics = run_bt(close_panel, score, profile, label=name)
        elapsed = time.time() - t0
        print(f"({elapsed:.0f}s)  Return={metrics['annual_return']:.2%}  "
              f"Sharpe={metrics['sharpe_ratio']:.2f}  MaxDD={metrics['max_drawdown']:.2f}%")

        results[name] = metrics

    # 对比表
    print(f"\n{'':>20}", end='')
    for name in profiles_to_run:
        print(f" {name:>12}", end='')
    print()
    print("─" * (20 + 13 * len(profiles_to_run)))

    for key in ['annual_return', 'sharpe_ratio', 'sortino_ratio', 'max_drawdown',
                'calmar_ratio', 'win_rate', 'total_trades', 'total_cost']:
        print(f"{key:>20}", end='')
        for name in profiles_to_run:
            v = results[name][key]
            if key in ('annual_return', 'max_drawdown'):
                print(f" {v:>11.2%}", end='')
            elif key == 'total_cost':
                print(f" ¥{v:>10,.0f}", end='')
            elif key == 'total_trades':
                print(f" {int(v):>12d}", end='')
            else:
                print(f" {v:>12.4f}", end='')
        print()

    out_path = os.path.join(DATA_DIR, "backtest_results", "v7_compare.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n结果已保存: {out_path}")

if __name__ == "__main__":
    main()
