#!/usr/bin/env python3
"""
思路 B v2：权重微调实验（修复版）
====================================
修复：负权重因子保持符号，只调整幅度。

调整规则：
  1. 降为零（IC_IR≈0）：macd_12_26, macd_5_35, mom_60, mom_120, rev_5
  2. 释放的权重按比例分配给正 IC_IR 因子（保持原有符号）
  3. 负权重因子（vol_10/20/60, atr_14 等）保持不变
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


def build_tuned_weights_v2():
    """基于当前权重做微调，正确处理负权重。"""
    w = dict(DEFAULT_FACTOR_WEIGHTS)

    # ── 1. 降为零的因子 ──────────────────────────────────────────
    zero_out = ['macd_12_26', 'macd_5_35', 'mom_60', 'mom_120', 'rev_5']
    released = sum(w[k] for k in zero_out if k in w)
    for k in zero_out:
        w[k] = 0.0

    print(f"  释放权重: {released:.4f}")
    print(f"  降为零: {zero_out}")

    # ── 2. 目标因子：只选正 IC_IR 且当前权重为正的因子 ──────────
    #    负权重因子（vol/atr 系列）保持不变
    # IC_IR 数据（只取正 IC_IR 的正权重因子）
    target_ic = {
        'boll_width_20':  0.147,  # 原权重 -0.02，但 IC 为正 → 提升到 +0.02 以上
        'rsi_14':         0.091,  # 原权重 +0.05
        'boll_pos_20':    0.080,  # 原权重 +0.03
        'rsi_28':         0.055,  # 原权重 +0.02
        'vol_change':     0.053,  # 原权重 +0.03
        'vwap_mom':       0.052,  # 原权重 +0.03
        'mom_20':         0.056,  # 原权重 +0.10，已经较高
        'mom_10':         0.040,  # 原权重 +0.10，已经较高
    }

    # 特殊处理 boll_width_20：原权重 -0.02，IC_IR 为正
    # 说明原方向设反了！boll_width 越大（波动越大）→ 收益越高（正向）
    # 将其从 -0.02 改为 +0.02（先翻转再分配）
    bw_old = w['boll_width_20']
    w['boll_width_20'] = abs(bw_old)  # 翻转为正
    released += abs(bw_old)  # 翻转产生的增量从释放池出
    print(f"  boll_width_20: {bw_old:+.3f} → {w['boll_width_20']:+.3f} (翻转方向)")

    # 按 IC_IR 比例分配释放的权重
    total_ic = sum(target_ic.values())
    print(f"\n  权重分配（按 |IC_IR| 比例，总IC={total_ic:.3f}）:")
    for k, ic in target_ic.items():
        add = released * (ic / total_ic)
        old = w[k]
        w[k] = old + add
        print(f"    {k:<20} {old:+.4f} → {w[k]:+.4f} (+{add:.4f})")

    # 负权重因子保持不变
    print(f"\n  负权重因子（保持不变）:")
    for k in ['vol_10', 'vol_20', 'vol_60', 'atr_14', 'boll_width_20']:
        if k != 'boll_width_20':  # 已处理
            print(f"    {k:<20} {w[k]:+.4f}")

    return w, zero_out


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
    print("思路 B v2：权重微调实验（修复版）")
    print("=" * 60)

    # 打印权重调整方案
    print("\n权重调整方案:")
    tuned_weights, removed = build_tuned_weights_v2()

    print(f"\n最终权重（非零因子）:")
    for k, v in sorted(tuned_weights.items(), key=lambda x: abs(x[1]), reverse=True):
        if v != 0:
            orig = DEFAULT_FACTOR_WEIGHTS.get(k, 0)
            marker = " ← 调整" if abs(v - orig) > 0.001 else ""
            print(f"  {k:<20} {orig:+.3f} → {v:+.4f}{marker}")

    print(f"\n[1/3] 加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  Panel: {len(close_panel)} 天 × {len(stocks)} 只股票")

    print("\n[2/3] 计算因子...")
    factors = calc_factors_panel(close_panel, volume_panel, amount_panel)

    print("\n[3/3] 回测对比...")
    score_current = composite_score(factors, DEFAULT_FACTOR_WEIGHTS)
    score_tuned = composite_score(factors, tuned_weights)

    m_current = run_bt(close_panel, score_current, label='current')
    m_tuned = run_bt(close_panel, score_tuned, label='tuned_B_v2')

    print(f"\n{'=' * 60}")
    print(f"{'策略对比':^60}")
    print(f"{'─' * 60}")
    print(f"{'':20} {'current':>14} {'tuned_B_v2':>14} {'差异':>10}")
    print(f"{'─' * 60}")
    for key in ['annual_return', 'sharpe_ratio', 'sortino_ratio', 'max_drawdown',
                'calmar_ratio', 'stop_loss_trades', 'total_trades', 'total_cost']:
        v1 = m_current[key]
        v2 = m_tuned[key]
        if key in ('annual_return', 'max_drawdown'):
            diff = f"{(v2-v1)*100:+.2f}%"
            v1s = f"{v1:.2%}"
            v2s = f"{v2:.2%}"
        elif key in ('total_trades', 'stop_loss_trades'):
            diff = f"{int(v2-v1):+d}"
            v1s = str(v1)
            v2s = str(v2)
        elif key == 'total_cost':
            diff = f"¥{v2-v1:+,.0f}"
            v1s = f"¥{v1:,.0f}"
            v2s = f"¥{v2:,.0f}"
        else:
            diff = f"{v2-v1:+.4f}"
            v1s = f"{v1:.4f}"
            v2s = f"{v2:.4f}"
        print(f"{key:<20} {v1s:>14} {v2s:{'>' + str(14)}} {diff:>10}")

    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")

    # 保存
    out = {
        'current': m_current,
        'tuned_B_v2': m_tuned,
        'tuned_weights': {k: round(v, 6) for k, v in tuned_weights.items() if v != 0},
        'removed_factors': removed,
    }
    out_path = os.path.join(DATA_DIR, "backtest_results", "tuneB_v2_results.json")
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"结果已保存: {out_path}")
