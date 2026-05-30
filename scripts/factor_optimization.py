#!/usr/bin/env python3
"""
因子权重优化实验
================
对比两组权重：
  - current: 当前 config.py 默认权重（29 因子）
  - ic_ir:   IC_IR 加权（删除冗余因子后 22 因子）
"""
import sys
import os
import time
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

import numpy as np
import pandas as pd

from core.factors import calc_factors_panel
from core.scoring import composite_score, standardize, factor_correlation
from core.account import PortfolioState, buy, sell, check_stop_loss, portfolio_value
from core.config import config as core_config, DEFAULT_FACTOR_WEIGHTS

# ── IC 分析（已有数据，直接硬编码） ──────────────────────────────
IC_RESULTS = {
    'boll_width_20':  {'ic_mean': 0.0318, 'ic_std': 0.2166, 'ic_ir': 0.1470},
    'rsi_14':         {'ic_mean': 0.0159, 'ic_std': 0.1753, 'ic_ir': 0.0910},
    'vol_60':         {'ic_mean': 0.0223, 'ic_std': 0.2728, 'ic_ir': 0.0819},
    'boll_pos_20':    {'ic_mean': 0.0136, 'ic_std': 0.1703, 'ic_ir': 0.0800},
    'vol_20':         {'ic_mean': 0.0189, 'ic_std': 0.2546, 'ic_ir': 0.0741},
    'atr_14':         {'ic_mean': 0.0170, 'ic_std': 0.2495, 'ic_ir': 0.0682},
    'vol_10':         {'ic_mean': 0.0150, 'ic_std': 0.2323, 'ic_ir': 0.0646},
    'mom_20':         {'ic_mean': 0.0123, 'ic_std': 0.2215, 'ic_ir': 0.0557},
    'rsi_28':         {'ic_mean': 0.0109, 'ic_std': 0.1974, 'ic_ir': 0.0550},
    'vol_change':     {'ic_mean': 0.0070, 'ic_std': 0.1311, 'ic_ir': 0.0532},
    'vwap_mom':       {'ic_mean': 0.0102, 'ic_std': 0.1984, 'ic_ir': 0.0515},
    'mom_10':         {'ic_mean': 0.0079, 'ic_std': 0.1975, 'ic_ir': 0.0399},
    'rsi_6':          {'ic_mean': 0.0066, 'ic_std': 0.1659, 'ic_ir': 0.0395},
    'boll_pos_10':    {'ic_mean': 0.0056, 'ic_std': 0.1671, 'ic_ir': 0.0336},
    'vol_ratio_20':   {'ic_mean': 0.0049, 'ic_std': 0.1275, 'ic_ir': 0.0381},
    'amount_ratio':   {'ic_mean': 0.0044, 'ic_std': 0.1366, 'ic_ir': 0.0321},
    'mom_5':          {'ic_mean': 0.0043, 'ic_std': 0.1901, 'ic_ir': 0.0228},
    'mom_60':         {'ic_mean': 0.0046, 'ic_std': 0.2424, 'ic_ir': 0.0191},
    'skew_20':        {'ic_mean': -0.0011, 'ic_std': 0.1059, 'ic_ir': -0.0105},
    'mom_120':        {'ic_mean': -0.0012, 'ic_std': 0.2368, 'ic_ir': -0.0051},
    'macd_12_26':     {'ic_mean': -0.0007, 'ic_std': 0.1644, 'ic_ir': -0.0043},
    'vol_ratio_5':    {'ic_mean': 0.0000, 'ic_std': 0.1187, 'ic_ir': 0.0001},
}

# ── 要删除的冗余因子 ────────────────────────────────────────────
REMOVE_FACTORS = {'rev_3', 'rev_5', 'rev_10', 'rel_strength_20', 'rel_strength_60',
                  'macd_5_35', 'kurt_20'}

# ── 构建 IC_IR 权重 ──────────────────────────────────────────────
def build_ic_ir_weights(ic_results, remove_set):
    """用 |IC_IR| 归一化构建权重，保持原 IC 方向。"""
    valid = {k: v for k, v in ic_results.items() if k not in remove_set}
    total_abs_ir = sum(abs(v['ic_ir']) for v in valid.values())
    if total_abs_ir == 0:
        n = len(valid)
        return {k: 1.0 / n for k in valid}

    weights = {}
    for k, v in valid.items():
        # 符号由 IC 方向决定，幅度由 |IC_IR| 占比决定
        sign = 1.0 if v['ic_mean'] >= 0 else -1.0
        weights[k] = sign * abs(v['ic_ir']) / total_abs_ir
    return weights


# ── 数据加载 ──────────────────────────────────────────────────────
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


# ── 回测引擎（精简版，直接用 core.account） ───────────────────────
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
    win_rate = (rets > 0).sum() / len(rets) if len(rets) > 0 else 0

    trades_df = pd.DataFrame(state.trade_log)
    sl_count = len(trades_df[trades_df['action'] == 'STOP_LOSS']) if len(trades_df) > 0 else 0
    total_cost = float(trades_df['cost'].sum()) if len(trades_df) > 0 else 0

    return {
        'label': label,
        'total_return': round(float(total_ret), 6),
        'annual_return': round(float(ann_ret), 6),
        'annual_volatility': round(float(ann_vol), 6),
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


# ── 主流程 ────────────────────────────────────────────────────────
if __name__ == "__main__":
    t0 = time.time()
    print("=" * 60)
    print("因子权重优化实验")
    print("=" * 60)

    # 1. 加载数据
    print("\n[1/4] 加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  Panel: {len(close_panel)} 天 × {len(stocks)} 只股票")

    # 2. 计算因子
    print("\n[2/4] 计算因子...")
    factors_all = calc_factors_panel(close_panel, volume_panel, amount_panel)
    print(f"  共 {len(factors_all)} 个因子")

    # 3. 构建两组权重 + 评分
    print("\n[3/4] 构建评分...")

    # 当前权重（29因子）
    score_current = composite_score(factors_all, DEFAULT_FACTOR_WEIGHTS)
    print(f"  current: {len(DEFAULT_FACTOR_WEIGHTS)} 因子")

    # IC_IR 权重（22因子，删除冗余）
    ic_ir_weights = build_ic_ir_weights(IC_RESULTS, REMOVE_FACTORS)
    factors_reduced = {k: v for k, v in factors_all.items() if k not in REMOVE_FACTORS}
    score_ic_ir = composite_score(factors_reduced, ic_ir_weights)
    print(f"  ic_ir:   {len(ic_ir_weights)} 因子（删除 {len(REMOVE_FACTORS)} 个冗余）")

    # 打印 IC_IR 权重
    print("\n  IC_IR 权重（按绝对值降序）:")
    for k, v in sorted(ic_ir_weights.items(), key=lambda x: abs(x[1]), reverse=True):
        bar = '█' * int(abs(v) * 200)
        print(f"    {k:<20} {v:>+.4f}  {bar}")

    # 4. 回测对比
    print("\n[4/4] 回测对比...")
    m_current = run_bt(close_panel, score_current, label='current_29f')
    m_ic_ir = run_bt(close_panel, score_ic_ir, label='ic_ir_22f')

    print(f"\n{'=' * 60}")
    print(f"{'策略对比':^60}")
    print(f"{'─' * 60}")
    print(f"{'':20} {'current(29f)':>14} {'ic_ir(22f)':>14} {'差异':>10}")
    print(f"{'─' * 60}")
    for key in ['annual_return', 'sharpe_ratio', 'sortino_ratio', 'max_drawdown', 'calmar_ratio', 'total_trades', 'total_cost']:
        v1 = m_current[key]
        v2 = m_ic_ir[key]
        if key in ('annual_return', 'max_drawdown'):
            diff = f"{(v2-v1)*100:+.2f}%"
            v1s = f"{v1:.2%}"
            v2s = f"{v2:.2%}"
        elif key in ('total_trades',):
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
        label = key.replace('_', ' ')
        print(f"{label:<20} {v1s:>14} {v2s:>14} {diff:>10}")

    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")

    # 保存结果
    out = {
        'current_29f': m_current,
        'ic_ir_22f': m_ic_ir,
        'ic_ir_weights': {k: round(v, 6) for k, v in ic_ir_weights.items()},
        'removed_factors': sorted(REMOVE_FACTORS),
    }
    out_path = os.path.join(DATA_DIR, "backtest_results", "factor_optimization.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"结果已保存: {out_path}")
