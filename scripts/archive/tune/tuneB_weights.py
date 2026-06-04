#!/usr/bin/env python3
"""
思路 B：权重微调实验
====================
只调整 4 个因子的权重，其余不变：
  - 降为零：macd_12_26(0.08), macd_5_35(0.04), mom_60(0.08), mom_120(0.05), rev_5(0.08)
  - 释放的权重 0.33 分配给：boll_width_20, rsi_14, vol_60, vol_20, boll_pos_20
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


def build_tuned_weights():
    """基于当前权重做微调。"""
    w = dict(DEFAULT_FACTOR_WEIGHTS)

    # 释放的权重
    released = 0.0

    # 降为零的因子（IC_IR 接近零或为负）
    zero_out = {
        'macd_12_26': 0.08,
        'macd_5_35': 0.04,
        'mom_60': 0.08,
        'mom_120': 0.05,
        'rev_5': 0.08,
    }
    for k, v in zero_out.items():
        if k in w:
            released += w[k]
            w[k] = 0.0

    print(f"  释放权重: {released:.3f}")
    print(f"  降为零的因子: {list(zero_out.keys())}")

    # 分配给有效因子（按 IC_IR 比例分配）
    # 目标因子及其 IC_IR
    target_ic = {
        'boll_width_20': 0.147,
        'rsi_14': 0.091,
        'vol_60': 0.082,
        'boll_pos_20': 0.080,
        'vol_20': 0.074,
    }
    total_ic = sum(target_ic.values())
    for k, ic in target_ic.items():
        add = released * (ic / total_ic)
        old = w[k]
        w[k] = old + add
        print(f"  {k}: {old:.3f} → {w[k]:+.3f} (+{add:.3f})")

    return w


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
    amount_panel = pd.DataFrame({c: d['amount'] for c, d in valid.items()})
    common_dates = close_panel.dropna(how='all').index
    common_dates = common_dates[(common_dates >= '2021-01-01') & (common_dates <= '2026-05-29')]

    return (
        close_panel.loc[common_dates].sort_index(),
        volume_panel.loc[common_dates].sort_index(),
        amount_panel.loc[common_dates].sort_index()
    ), list(valid.keys())


def run_bt(close_panel, score, top_n=12, rebal_freq=20, stop_loss=0.20,
           use_vol_scaling=True, vol_target=0.20, max_position=0.10, label='default'):
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
    win_rate = (rets > 0).sum() / len(rets) if len(rets) > 0 else 0

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
        'win_rate': round(float(win_rate), 6),
        'total_trades': len(trades_df),
        'stop_loss_trades': int(sl_count),
        'total_cost': round(total_cost, 2),
        'final_value': round(float(nav.iloc[-1]), 2),
    }


if __name__ == "__main__":
    t0 = time.time()
    print("=" * 60)
    print("思路 B：权重微调实验")
    print("=" * 60)

    print("\n[1/4] 加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  Panel: {len(close_panel)} 天 × {len(stocks)} 只股票")

    print("\n[2/4] 计算因子...")
    factors = calc_factors_panel(close_panel, volume_panel, amount_panel)
    print(f"  共 {len(factors)} 个因子")

    print("\n[3/4] 构建评分...")
    # 当前权重
    score_current = composite_score(factors, DEFAULT_FACTOR_WEIGHTS)
    print(f"  current: 原始 29 因子权重")

    # 微调权重
    tuned_weights = build_tuned_weights()
    score_tuned = composite_score(factors, tuned_weights)
    print(f"  tuned:   微调后权重")

    print("\n[4/4] 回测对比...")
    m_current = run_bt(close_panel, score_current, label='current')
    m_tuned = run_bt(close_panel, score_tuned, label='tuned_B')

    print(f"\n{'=' * 60}")
    print(f"{'策略对比':^60}")
    print(f"{'─' * 60}")
    print(f"{'':20} {'current':>14} {'tuned_B':>14} {'差异':>10}")
    print(f"{'─' * 60}")
    for key in ['annual_return', 'sharpe_ratio', 'sortino_ratio', 'max_drawdown', 'calmar_ratio', 'stop_loss_trades', 'total_cost']:
        v1 = m_current[key]
        v2 = m_tuned[key]
        if key in ('annual_return', 'max_drawdown'):
            diff = f"{(v2-v1)*100:+.2f}%"
            v1s = f"{v1:.2%}"
            v2s = f"{v2:.2%}"
        elif key == 'total_cost':
            diff = f"¥{v2-v1:+,.0f}"
            v1s = f"¥{v1:,.0f}"
            v2s = f"¥{v2:,.0f}"
        else:
            diff = f"{v2-v1:+.4f}"
            v1s = f"{v1:.4f}"
            v2s = f"{v2:.4f}"
        print(f"{key:<20} {v1s:>14} {v2s:>14} {diff:>10}")

    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")

    # 保存
    out = {
        'current': m_current,
        'tuned_B': m_tuned,
        'tuned_weights': {k: round(v, 6) for k, v in tuned_weights.items()},
    }
    out_path = os.path.join(DATA_DIR, "backtest_results", "tuneB_results.json")
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"结果已保存: {out_path}")
