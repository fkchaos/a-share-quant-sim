#!/usr/bin/env python3
"""
验证负 IC 因子的反转效果
==========================
假设：负 IC 因子反转后（取负权重）应该能提升策略表现

对比：
  v6b: 8个正IC因子（当前）
  v8a: 8个因子 + 反转后的高|IC_IR|负IC因子
  v8b: 仅反转后的高|IC_IR|负IC因子
  v8c: 正IC + 反转负IC 混合
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
from core.config import STRATEGY_PROFILES

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

IC_DATA = {
    'mom_60': -0.2236, 'rel_strength_60': -0.2236, 'macd_12_26': -0.1979,
    'mom_120': -0.1902, 'macd_5_35': -0.1776, 'rsi_28': -0.1510,
    'vol_10': -0.1426, 'atr_14': -0.1392, 'vol_20': -0.1375,
    'vol_60': -0.1321, 'mom_20': -0.0985, 'rel_strength_20': -0.0985,
    'vol_ratio_20': 0.0957, 'skew_20': -0.0945, 'boll_width_20': -0.0897,
    'vol_change': -0.0704, 'amount_ratio': 0.0662, 'rsi_6': 0.0572,
    'vol_ratio_5': 0.0487, 'boll_pos_10': 0.0354, 'mom_10': -0.0177,
    'rev_10': 0.0177, 'boll_pos_20': 0.0157, 'rev_3': -0.0143,
    'vwap_mom': -0.0137, 'mom_5': 0.0039, 'rev_5': -0.0039,
    'kurt_20': 0.0031, 'rsi_14': -0.0021,
}

def build_weights_from_ic(ic_data, factor_names):
    """根据 IC_IR 构建权重，负 IC 因子取负权重"""
    weights = {}
    total = sum(abs(ic_data.get(f, 0)) for f in factor_names)
    for f in factor_names:
        ir = ic_data.get(f, 0)
        if abs(ir) > 0.02:
            # 负 IC → 负权重（反转），正 IC → 正权重
            weights[f] = ir / total if total > 0 else 0
    # 归一化使权重之和为正（因为我们选股是 nlargest）
    total_pos = sum(v for v in weights.values() if v > 0)
    total_neg = sum(v for v in weights.values() if v < 0)
    # 让正负权重的绝对值之和相等
    if total_pos > 0 and total_neg < 0:
        for f in weights:
            if weights[f] > 0:
                weights[f] /= total_pos
            elif weights[f] < 0:
                weights[f] /= abs(total_neg)
    return weights

def run_bt(close_panel, score, label='default'):
    profile = STRATEGY_PROFILES["v6b_8f_pos_ic"]
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

        if profile.use_take_profit and profile.tp_tiers:
            state = check_take_profit(state, date, price_data, profile.tp_tiers)

        if profile.use_holding_decay:
            state = apply_holding_decay(state, date, price_data, rebalance_freq=profile.rebalance_freq)

        if (i - 120) % profile.rebalance_freq == 0 and date in score.index:
            day_score = score.loc[date].dropna()
            valid_idx = day_score.index.isin(price_data.dropna().index)
            day_score = day_score[valid_idx]

            if profile.use_vol_scaling:
                returns = close_panel.pct_change()
                stock_vol = returns.rolling(20).std().loc[date]
                vol_scale = profile.vol_target / (stock_vol * np.sqrt(252))
                vol_scale = vol_scale.clip(0.1, 3.0)
                day_score = day_score * vol_scale.reindex(day_score.index).fillna(1)

            top_stocks = day_score.nlargest(profile.top_n).index.tolist()

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
                            target_val = min(current_pv * w, current_pv * profile.max_position)
                            adj_p = p * 1.001
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
    max_dd = ((nav.cummax() - nav) / nav.cummax()).max()
    calmar = ann_ret / max_dd if max_dd > 0 else 0
    win_rate = (rets > 0).sum() / len(rets) if len(rets) > 0 else 0
    trades_df = pd.DataFrame(state.trade_log)
    total_cost = float(trades_df['cost'].sum()) if len(trades_df) > 0 else 0

    return {
        'label': label,
        'annual_return': round(float(ann_ret), 4),
        'sharpe_ratio': round(float(sharpe), 4),
        'max_drawdown': round(float(max_dd), 4),
        'calmar_ratio': round(float(calmar), 4),
        'win_rate': round(float(win_rate), 4),
        'total_trades': len(trades_df),
        'total_cost': round(total_cost, 0),
    }

def main():
    print("=" * 70)
    print("负 IC 因子反转验证")
    print("=" * 70)

    print("\n[1/3] 加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只股票")

    print("\n[2/3] 计算因子...")
    all_factors = calc_factors_panel(close_panel, volume_panel, amount_panel)

    # 方案定义
    schemes = {}

    # v6b: 当前（8个正IC因子）
    v6b_factors = ['vol_ratio_20', 'amount_ratio', 'rsi_6', 'vol_ratio_5',
                   'boll_pos_10', 'mom_5', 'rev_10', 'boll_pos_20']
    v6b_weights = STRATEGY_PROFILES['v6b_8f_pos_ic'].factor_weights
    schemes['v6b_pos_ic_8f'] = (v6b_factors, v6b_weights)

    # v8a: 高|IC_IR|因子（含负IC，反转使用）
    # 选 |IC_IR| >= 0.05 的全部因子
    high_ir_factors = [f for f, ir in IC_DATA.items() if abs(ir) >= 0.05]
    # 去冗余：剔除与 mom 系列重复的
    deduped = []
    seen_groups = set()
    for f in high_ir_factors:
        group = f
        if 'mom' in f:
            group = 'mom'
        elif 'vol' in f and f.startswith('vol_'):
            group = 'vol_level'
        elif 'rsi' in f:
            group = 'rsi'
        elif 'macd' in f:
            group = 'macd'
        if group not in seen_groups:
            deduped.append(f)
            seen_groups.add(group)
    
    v8a_weights = build_weights_from_ic(IC_DATA, deduped)
    schemes[f'v8a_mixed_{len(deduped)}f'] = (deduped, v8a_weights)

    # v8b: 仅高|IC_IR|负IC因子（反转）
    neg_high_ir = [f for f, ir in IC_DATA.items() if ir <= -0.05 and f not in ('mom_10', 'mom_5')]
    neg_weights = build_weights_from_ic(IC_DATA, neg_high_ir)
    schemes[f'v8b_neg_ic_{len(neg_high_ir)}f'] = (neg_high_ir, neg_weights)

    # v8c: 正IC + 反转负IC 混合（去冗余后各取 top）
    pos_factors = [f for f, ir in sorted(IC_DATA.items(), key=lambda x: x[1], reverse=True) if ir > 0][:4]
    neg_factors = [f for f, ir in sorted(IC_DATA.items(), key=lambda x: x[1]) if ir < -0.05][:4]
    mixed = pos_factors + neg_factors
    mixed_weights = build_weights_from_ic(IC_DATA, mixed)
    schemes[f'v8c_hybrid_{len(mixed)}f'] = (mixed, mixed_weights)

    print("\n方案详情：")
    for name, (factors, weights) in schemes.items():
        print(f"\n  {name} ({len(factors)} 因子):")
        for f in sorted(factors, key=lambda x: abs(IC_DATA.get(x, 0)), reverse=True):
            ir = IC_DATA.get(f, 0)
            w = weights.get(f, 0)
            direction = '←反转' if ir < 0 else '正向'
            print(f'    {f:<20} IC_IR={ir:>+.4f}  权重={w:>+.4f}  {direction}')

    print("\n[3/3] 回测...")
    results = {}

    for name, (factor_list, weights) in schemes.items():
        valid_factors = {k: v for k, v in all_factors.items() if k in weights}
        score = composite_score(valid_factors, weights)

        print(f"\n  ▶ {name}...", end=" ", flush=True)
        t0 = time.time()
        metrics = run_bt(close_panel, score, label=name)
        elapsed = time.time() - t0
        print(f"({elapsed:.0f}s)  Return={metrics['annual_return']:.2%}  "
              f"Sharpe={metrics['sharpe_ratio']:.2f}  MaxDD={metrics['max_drawdown']:.2f}%")

        results[name] = metrics

    # 对比表
    labels = list(results.keys())
    print(f"\n{'':>20}", end='')
    for l in labels:
        print(f" {l:>14}", end='')
    print()
    print("─" * (20 + 15 * len(labels)))

    for key in ['annual_return', 'sharpe_ratio', 'max_drawdown', 'calmar_ratio', 'win_rate', 'total_cost']:
        print(f"{key:>20}", end='')
        for l in labels:
            v = results[l][key]
            if key in ('annual_return', 'max_drawdown'):
                print(f" {v:>13.2%}", end='')
            elif key == 'total_cost':
                print(f" ¥{v:>12,.0f}", end='')
            else:
                print(f" {v:>14.4f}", end='')
        print()

    out_path = os.path.join(DATA_DIR, "backtest_results", "neg_ic_test.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n结果已保存: {out_path}")

if __name__ == "__main__":
    main()
