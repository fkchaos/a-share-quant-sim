#!/usr/bin/env python3
"""
思路 B v4：保守约束版
======================
原则：
  1. 只删除 |IC_IR| < 0.005 的噪音因子（5个）
  2. 剩余因子权重 = 当前权重 + IC_IR 方向修正
  3. 修正幅度有上限：单因子权重变化不超过原权重的 50%
  4. 负权重因子可以向正方向修正，但不能超过 0（即最多归零）
  5. 正交化：翻转后的因子如果和已有因子高度相关，做合并而非同时保留
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

ZERO_IC = {'kurt_20', 'macd_12_26', 'macd_5_35', 'vol_ratio_5', 'mom_120'}


def build_conservative_weights(ic_results, zero_set, max_flip_pct=0.5):
    """
    保守权重调整：
    - 删除零 IC 因子
    - 每个因子权重 = 原权重 + delta
    - delta 方向 = IC 方向（同向增强，反向削弱）
    - |delta| <= |原权重| * max_flip_pct（默认 50%）
    - 负权重最多归零，不翻正
    - 翻转后的权重重新归一化
    """
    w = {k: v for k, v in DEFAULT_FACTOR_WEIGHTS.items() if k not in zero_set}

    deltas = {}
    for k in w:
        w_orig = w[k]
        ic_ir = ic_results.get(k, {}).get('ic_ir', 0)
        ic_mean = ic_results.get(k, {}).get('ic_mean', 0)
        if ic_ir == 0:
            deltas[k] = 0
            continue

        # IC 方向：ic_mean > 0 说明因子值越大收益越高 -> 权重应为正
        ic_sign = 1.0 if ic_mean >= 0 else -1.0

        # delta 方向：如果权重方向和 IC 方向一致则增强，不一致则削弱
        w_sign = 1.0 if w_orig >= 0 else -1.0
        aligned = (ic_sign == w_sign)

        max_delta = abs(w_orig) * max_flip_pct

        if aligned:
            # 同向：增强，但不超过上限
            delta = ic_sign * max_delta
        else:
            # 反向：削弱（向零靠近），最多归零
            delta = -w_sign * max_delta  # 向零方向移动
            # 确保不会跨零翻转
            if abs(delta) > abs(w_orig):
                delta = -w_orig  # 归零

        deltas[k] = delta

    # 应用 delta
    for k in w:
        w[k] += deltas[k]
        # 清理极小值（< 0.005 视为零）
        if abs(w[k]) < 0.005:
            w[k] = 0

    # 归一化：权重绝对值之和 = 1.35（和原来一致）
    total_abs = sum(abs(v) for v in w.values())
    target_total = 1.35
    scale = target_total / total_abs if total_abs > 0 else 1
    for k in w:
        if w[k] != 0:
            w[k] = round(w[k] * scale, 6)

    return w, deltas


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
    print("思路 B v4：保守约束版")
    print("=" * 65)

    # ── 构建保守权重 ─────────────────────────────────────────────
    w_conservative, deltas = build_conservative_weights(
        IC_RESULTS, ZERO_IC, max_flip_pct=0.5
    )

    # 打印权重调整
    print("\n权重调整详情（仅显示有变化的）:")
    print(f"{'factor':<20} {'current':>10} {'delta':>10} {'result':>10} {'ic_ir':>10}")
    print("-" * 65)
    for k in sorted(DEFAULT_FACTOR_WEIGHTS.keys()):
        if k in ZERO_IC:
            wc = DEFAULT_FACTOR_WEIGHTS[k]
            print(f"{k:<20} {wc:>+10.3f} {'→ 0 (del)':>10} {'':>10}")
            continue
        wc = DEFAULT_FACTOR_WEIGHTS[k]
        d = deltas.get(k, 0)
        wr = w_conservative.get(k, 0)
        ic = IC_RESULTS.get(k, {}).get('ic_ir', 0)
        if abs(d) > 0.001:
            print(f"{k:<20} {wc:>+10.3f} {d:>+10.4f} {wr:>+10.4f} {ic:>+10.4f} <--")
        else:
            print(f"{k:<20} {wc:>+10.3f} {'':>10} {wr:>+10.4f} {ic:>+10.4f}")

    abs_sum = sum(abs(v) for v in w_conservative.values() if v != 0)
    print(f"\n权重绝对值之和: {abs_sum:.3f} (目标 1.35)")
    print(f"非零因子数: {sum(1 for v in w_conservative.values() if v != 0)}")

    # ── 加载 + 计算 + 回测 ──────────────────────────────────────
    print(f"\n\n[1/3] 加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  Panel: {len(close_panel)} 天 × {len(stocks)} 只股票")

    print("\n[2/3] 计算因子...")
    factors = calc_factors_panel(close_panel, volume_panel, amount_panel)

    print("\n[3/3] 回测...")
    results = {}

    # current
    score_cur = composite_score(factors, DEFAULT_FACTOR_WEIGHTS)
    m_cur = run_bt(close_panel, score_cur, label='current')
    results['current'] = m_cur
    print(f"  current:    Ret={m_cur['annual_return']:.2%} Sharpe={m_cur['sharpe_ratio']:.3f} "
          f"DD={m_cur['max_drawdown']:.2%} SL={m_cur['stop_loss_trades']}")

    # conservative
    score_con = composite_score(factors, w_conservative)
    m_con = run_bt(close_panel, score_con, label='conservative')
    results['conservative'] = m_con
    print(f"  conservative: Ret={m_con['annual_return']:.2%} Sharpe={m_con['sharpe_ratio']:.3f} "
          f"DD={m_con['max_drawdown']:.2%} SL={m_con['stop_loss_trades']}")

    # ── 汇总表 ───────────────────────────────────────────────────
    print(f"\n{'=' * 65}")
    print(f"{'策略对比':^65}")
    print(f"{'─' * 65}")
    metrics = ['annual_return', 'sharpe_ratio', 'sortino_ratio', 'max_drawdown',
               'calmar_ratio', 'stop_loss_trades', 'total_trades', 'total_cost']

    def fmt(v, key):
        if key in ('annual_return', 'max_drawdown'):
            return f"{v:.2%}"
        elif key in ('stop_loss_trades', 'total_trades'):
            return str(v)
        elif key == 'total_cost':
            return f"¥{v:,.0f}"
        else:
            return f"{v:.4f}"

    def diff_str(v_old, v_new, key):
        d = v_new - v_old
        if key in ('annual_return', 'max_drawdown'):
            return f"{'↑' if d > 0 else '↓'}{abs(d)*100:.2f}%"
        elif key in ('stop_loss_trades', 'total_trades'):
            return f"{'↑' if d > 0 else '↓'}{abs(int(d))}"
        else:
            return f"{'↑' if d > 0 else '↓'}{abs(d):.4f}"

    header = f"{'':22} {'current':>14} {'conservative':>14} {'差异':>10}"
    print(header)
    print(f"{'─' * 65}")
    for key in metrics:
        vc = m_cur[key]
        vn = m_con[key]
        print(f"{key:<22} {fmt(vc, key):>14} {fmt(vn, key):>14} {diff_str(vc, vn, key):>10}")

    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")

    # 保存
    out = {
        'results': results,
        'weights': {
            'conservative': {k: v for k, v in w_conservative.items() if v != 0},
        },
        'deltas': {k: round(v, 6) for k, v in deltas.items() if abs(v) > 0.001},
        'removed': sorted(ZERO_IC),
    }
    out_path = os.path.join(DATA_DIR, "backtest_results", "tuneB_v4_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"结果已保存: {out_path}")
