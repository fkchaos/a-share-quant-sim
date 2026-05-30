#!/usr/bin/env python3
"""
思路 B v3：方向对齐 + IC_IR 重加权
=====================================
核心：权重符号必须和 IC 方向一致。

步骤：
  1. 计算每个因子的 IC 方向（IC > 0 则权重为正，IC < 0 则权重为负）
  2. 幅度用 |IC_IR| 归一化
  3. 删除 IC_IR 接近零的因子（|kurt_20| < 0.01, macd 系列, mom_120, vol_ratio_5）
  4. 权重上限裁剪：单个因子不超过 cap（如 0.06）
  5. 对比：current vs aligned vs aligned+cap
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


# ── IC 分析结果（从回测中提取） ──────────────────────────────────
IC_RESULTS = {
    'boll_width_20':  {'ic_mean': 0.0318, 'ic_ir': 0.1470},
    'rsi_14':         {'ic_mean': 0.0159, 'ic_ir': 0.0910},
    'vol_60':         {'ic_mean': 0.0223, 'ic_ir': 0.0819},
    'boll_pos_20':    {'ic_mean': 0.0136, 'ic_ir': 0.0800},
    'vol_20':         {'ic_mean': 0.0189, 'ic_ir': 0.0741},
    'atr_14':         {'ic_mean': 0.0170, 'ic_ir': 0.0682},
    'vol_10':         {'ic_mean': 0.0150, 'ic_ir': 0.0646},
    'mom_20':         {'ic_mean': 0.0123, 'ic_ir': 0.0557},
    'rel_strength_20':{'ic_mean': 0.0123, 'ic_ir': 0.0557},
    'rsi_28':         {'ic_mean': 0.0109, 'ic_ir': 0.0550},
    'vol_change':     {'ic_mean': 0.0070, 'ic_ir': 0.0532},
    'vwap_mom':       {'ic_mean': 0.0102, 'ic_ir': 0.0515},
    'mom_10':         {'ic_mean': 0.0079, 'ic_ir': 0.0399},
    'rev_10':         {'ic_mean':-0.0079, 'ic_ir':-0.0399},
    'rsi_6':          {'ic_mean': 0.0066, 'ic_ir': 0.0395},
    'vol_ratio_20':   {'ic_mean': 0.0049, 'ic_ir': 0.0381},
    'boll_pos_10':    {'ic_mean': 0.0056, 'ic_ir': 0.0315},
    'amount_ratio':   {'ic_mean': 0.0044, 'ic_ir': 0.0321},
    'mom_5':          {'ic_mean': 0.0043, 'ic_ir': 0.0228},
    'rev_5':          {'ic_mean':-0.0043, 'ic_ir':-0.0228},
    'mom_60':         {'ic_mean': 0.0046, 'ic_ir': 0.0191},
    'rel_strength_60':{'ic_mean': 0.0046, 'ic_ir': 0.0191},
    'rev_3':          {'ic_mean':-0.0023, 'ic_ir':-0.0122},
    'skew_20':        {'ic_mean':-0.0011, 'ic_ir':-0.0105},
    'mom_120':        {'ic_mean':-0.0012, 'ic_ir':-0.0051},
    'macd_12_26':     {'ic_mean':-0.0007, 'ic_ir':-0.0043},
    'macd_5_35':      {'ic_mean':-0.0006, 'ic_ir':-0.0036},
    'kurt_20':        {'ic_mean':-0.0003, 'ic_ir':-0.0030},
    'vol_ratio_5':    {'ic_mean': 0.0000, 'ic_ir': 0.0001},
}

# 要删除的因子（|IC_IR| < 0.005，几乎零贡献）
ZERO_IC = {'kurt_20', 'macd_12_26', 'macd_5_35', 'vol_ratio_5', 'mom_120'}


def build_aligned_weights(ic_results, zero_set, cap=None):
    """
    构建方向对齐的权重。
    - 符号由 IC 方向决定（IC>0 → 正权重，IC<0 → 负权重）
    - 幅度由 |IC_IR| 归一化
    - cap: 单个因子权重上限（绝对值），None 表示不限制
    """
    # 过滤零 IC 因子
    valid = {k: v for k, v in ic_results.items() if k not in zero_set and abs(v['ic_ir']) >= 0.005}
    total_abs_ir = sum(abs(v['ic_ir']) for v in valid.values())

    weights = {}
    for k, v in valid.items():
        sign = 1.0 if v['ic_mean'] >= 0 else -1.0
        w = sign * abs(v['ic_ir']) / total_abs_ir
        weights[k] = w

    # 上限裁剪
    if cap is not None:
        excess = 0.0
        capped = {}
        for k, w in weights.items():
            if abs(w) > cap:
                excess += abs(w) - cap
                capped[k] = cap if w > 0 else -cap
            else:
                capped[k] = w

        # 把超出部分按比例分配给未达上限的因子
        if excess > 0:
            uncapped = {k: v for k, v in capped.items() if abs(v) < cap}
            if uncapped:
                total_uncapped = sum(abs(v) for v in uncapped.values())
                for k in uncapped:
                    sign = 1.0 if capped[k] >= 0 else -1.0
                    add = sign * excess * (abs(capped[k]) / total_uncapped)
                    capped[k] += add
                weights = capped

    return weights


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
    print("=" * 65)
    print("思路 B v3：方向对齐 + IC_IR 重加权")
    print("=" * 65)

    # ── 构建三组权重 ─────────────────────────────────────────────
    w_current = dict(DEFAULT_FACTOR_WEIGHTS)
    w_aligned = build_aligned_weights(IC_RESULTS, ZERO_IC, cap=None)
    w_capped  = build_aligned_weights(IC_RESULTS, ZERO_IC, cap=0.06)

    # 打印权重对比
    print("\n权重对比（仅显示有变化的）:")
    print(f"{'factor':<20} {'current':>10} {'aligned':>10} {'capped6%':>10}")
    print("-" * 55)
    all_factors = sorted(set(list(w_current.keys()) + list(w_aligned.keys())))
    for k in all_factors:
        wc = w_current.get(k, 0)
        wa = w_aligned.get(k, 0)
        w6 = w_capped.get(k, 0)
        changed = abs(wa - wc) > 0.005 or abs(w6 - wc) > 0.005
        marker = " <--" if changed else ""
        if k in ZERO_IC:
            print(f"{k:<20} {wc:>+10.3f} {'→ 0 (del)':>10} {'→ 0 (del)':>10}")
        else:
            print(f"{k:<20} {wc:>+10.3f} {wa:>+10.4f} {w6:>+10.4f}{marker}")

    print(f"\n删除因子: {sorted(ZERO_IC)}")
    print(f"剩余因子: {len(w_aligned)} (aligned) / {len(w_capped)} (capped)")
    print(f"最大权重(aligned): {max(abs(v) for v in w_aligned.values()):.4f}")
    print(f"最大权重(capped):  {max(abs(v) for v in w_capped.values()):.4f}")

    # ── 加载数据 ─────────────────────────────────────────────────
    print(f"\n\n[1/3] 加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  Panel: {len(close_panel)} 天 × {len(stocks)} 只股票")

    # ── 计算因子 ─────────────────────────────────────────────────
    print("\n[2/3] 计算因子...")
    factors = calc_factors_panel(close_panel, volume_panel, amount_panel)

    # ── 回测三组 ─────────────────────────────────────────────────
    print("\n[3/3] 回测对比...")
    results = {}
    for label, weights in [('current', w_current), ('aligned', w_aligned), ('capped_6pct', w_capped)]:
        score = composite_score(factors, weights)
        m = run_bt(close_panel, score, label=label)
        results[label] = m
        print(f"  {label:>12}: Ret={m['annual_return']:.2%} Sharpe={m['sharpe_ratio']:.3f} "
              f"DD={m['max_drawdown']:.2%} SL={m['stop_loss_trades']}")

    # ── 汇总表 ───────────────────────────────────────────────────
    print(f"\n{'=' * 65}")
    print(f"{'策略对比':^65}")
    print(f"{'─' * 65}")
    metrics = ['annual_return', 'sharpe_ratio', 'sortino_ratio', 'max_drawdown',
               'calmar_ratio', 'stop_loss_trades', 'total_trades', 'total_cost']
    fmt_map = {
        'annual_return': ('{:.2%}', '{:.2%}', '{:+.2%}'),
        'max_drawdown':  ('{:.2%}', '{:.2%}', '{:+.2%}'),
        'stop_loss_trades': ('{}', '{}', '{:+d}'),
        'total_trades': ('{}', '{}', '{:+d}'),
        'total_cost': ('¥{:,.0f}', '¥{:,.0f}', '¥{:+,.0f}'),
    }
    header = f"{'':20} {'current':>14} {'aligned':>14} {'capped_6pct':>14}"
    print(header)
    print(f"{'─' * 65}")
    for key in metrics:
        v_cur = results['current'][key]
        v_al  = results['aligned'][key]
        v_cap = results['capped_6pct'][key]
        if key in fmt_map:
            df, d1, d2 = fmt_map[key]
            vs = df.format(v_cur)
            va = d1.format(v_al)
            vc = d2.format(v_al - v_cur)
        else:
            vs = f"{v_cur:.4f}"
            va = f"{v_al:.4f}"
            vc = f"{v_al - v_cur:+.4f}"
        print(f"{key:<20} {vs:>14} {va:>14} {vc:>14}")

    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")

    # 保存
    out = {
        'results': results,
        'weights': {
            'current': {k: round(v, 6) for k, v in w_current.items()},
            'aligned': {k: round(v, 6) for k, v in w_aligned.items()},
            'capped_6pct': {k: round(v, 6) for k, v in w_capped.items()},
        },
        'removed': sorted(ZERO_IC),
    }
    out_path = os.path.join(DATA_DIR, "backtest_results", "tuneB_v3_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"结果已保存: {out_path}")
