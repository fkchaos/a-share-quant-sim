#!/usr/bin/env python3
"""
风控参数网格扫描
=================
固定因子权重（current 29因子），扫描风控参数：
  - stop_loss: [0.12, 0.15, 0.18, 0.20, 0.25]
  - rebalance_freq: [15, 20, 25, 30]
  - vol_target: [0.15, 0.18, 0.20, 0.25]
  - max_industry_weight: [0.20, 0.25, 0.30]

指标优先级：
  1. 最大回撤 < -25% 直接淘汰
  2. 按夏普比率排序
"""
import sys, os, time, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

import numpy as np
import pandas as pd
from itertools import product

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
    print("=" * 70)
    print("风控参数网格扫描")
    print("=" * 70)

    print("\n加载数据 + 计算因子...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  Panel: {len(close_panel)} 天 × {len(stocks)} 只股票")

    factors = calc_factors_panel(close_panel, volume_panel, amount_panel)
    score = composite_score(factors, DEFAULT_FACTOR_WEIGHTS)

    # 参数网格
    grid = {
        'stop_loss':       [0.12, 0.15, 0.18, 0.20, 0.25],
        'rebalance_freq':  [15, 20, 25, 30],
        'vol_target':      [0.15, 0.18, 0.20, 0.25],
        'max_industry_wt': [0.20, 0.25, 0.30],
    }
    keys = list(grid.keys())
    values = list(grid.values())
    total = 1
    for v in values:
        total *= len(v)

    print(f"\n参数网格: {total} 组")
    print(f"  stop_loss: {grid['stop_loss']}")
    print(f"  rebalance_freq: {grid['rebalance_freq']}")
    print(f"  vol_target: {grid['vol_target']}")
    print(f"  max_industry_weight: {grid['max_industry_wt']}")
    print(f"\n筛选条件: max_drawdown >= -25%")

    results = []
    count = 0
    for combo in product(*values):
        params = dict(zip(keys, combo))
        count += 1
        m = run_bt(
            close_panel, score,
            top_n=12,
            stop_loss=params['stop_loss'],
            rebal_freq=params['rebalance_freq'],
            use_vol_scaling=True,
            vol_target=params['vol_target'],
            max_industry_weight=params['max_industry_wt'],
            label=f"sl{params['stop_loss']}_rf{params['rebalance_freq']}_vt{params['vol_target']}_iw{params['max_industry_wt']}"
        )
        m['params'] = params

        # 回撤筛选
        if m['max_drawdown'] <= -0.25:
            status = "REJECT(DD)"
        else:
            status = "OK"

        results.append((m['sharpe_ratio'], status, m))

        if count % 20 == 0 or count == total:
            print(f"  [{count}/{total}] 当前: Sharpe={m['sharpe_ratio']:.3f} DD={m['max_drawdown']:.2%} {status}")

    # 排序
    results.sort(key=lambda x: x[0], reverse=True)

    print(f"\n{'=' * 70}")
    print(f"{'风控参数扫描结果（按夏普排序）':^70}")
    print(f"{'─' * 70}")
    print(f"{'排名':>4} {'状态':>10} {'夏普':>7} {'年化':>8} {'回撤':>8} {'Sortino':>8} "
          f"{'止损':>5} {'调仓':>5} {'波动':>5} {'行业':>5}")
    print(f"{'─' * 70}")

    shown = 0
    for rank, (sharpe, status, m) in enumerate(results[:20], 1):
        p = m['params']
        print(f"{rank:>4} {status:>10} {m['sharpe_ratio']:>7.3f} {m['annual_return']:>+7.2%} "
              f"{m['max_drawdown']:>7.2%} {m['sortino_ratio']:>7.3f} "
              f"{p['stop_loss']:>5.2f} {p['rebalance_freq']:>5d} {p['vol_target']:>5.2f} {p['max_industry_wt']:>5.2f}")
        shown += 1

    # 最优参数
    best = None
    for _, status, m in results:
        if status == "OK":
            best = m
            break

    if best:
        p = best['params']
        print(f"\n★ 最优参数（回撤<25% 中夏普最高）:")
        print(f"  止损={p['stop_loss']}  调仓={p['rebalance_freq']}天  "
              f"vol_target={p['vol_target']}  行业上限={p['max_industry_wt']}")
        print(f"  年化={best['annual_return']:.2%}  夏普={best['sharpe_ratio']:.3f}  "
              f"回撤={best['max_drawdown']:.2%}  Sortino={best['sortino_ratio']:.3f}")
    else:
        print(f"\n⚠️ 没有满足 回撤<25% 的参数组合")

    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")

    # 保存
    out_path = os.path.join(DATA_DIR, "backtest_results", "param_scan_risk.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump({
            'results': [{'sharpe': s, 'status': st, 'metrics': m} for s, st, m in results],
            'best': best,
        }, f, indent=2, ensure_ascii=False)
    print(f"结果已保存: {out_path}")
