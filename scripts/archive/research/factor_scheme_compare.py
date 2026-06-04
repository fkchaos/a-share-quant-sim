#!/usr/bin/env python3
"""
v7 方案：混合策略
- 保留正 IC 因子（IC_IR > 0），等权
- 对比：v4(29f) vs v6(12f_ICIR负权重) vs v7(正IC因子等权)
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

# ── 三种权重方案 ──────────────────────────────────────────────────
# v4: 29 因子当前等权（config 默认）
V4_WEIGHTS = dict(DEFAULT_FACTOR_WEIGHTS)

# v6: 12 因子 IC_IR 加权（含负权重）
V6_WEIGHTS = {
    'mom_60': -0.1513, 'macd_12_26': -0.1339, 'mom_120': -0.1287,
    'rsi_28': -0.1022, 'vol_10': -0.0965, 'mom_20': -0.0667,
    'vol_ratio_20': 0.0648, 'skew_20': -0.0640, 'boll_width_20': -0.0607,
    'vol_change': -0.0476, 'amount_ratio': 0.0448, 'rsi_6': 0.0387,
}

# v7: 仅正 IC 因子等权
V7_WEIGHTS = {
    'vol_ratio_20': 0.20,
    'vol_ratio_5': 0.15,
    'rsi_6': 0.15,
    'amount_ratio': 0.15,
    'boll_pos_10': 0.10,
    'mom_5': 0.10,
    'boll_pos_20': 0.10,
    'rev_10': 0.05,
}

# v8: 取 |IC_IR| 最高的 12 个因子，按 |IC_IR| 加权，统一用负号（因为多数是反转效应）
# 即：大的因子值 → 低评分（反向选股）
V8_WEIGHTS = {}

# 从 factor_analysis 读 |IC_IR|
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

# 方案 A: 全部 29 因子，|IC_IR| 加权（负 IC 因子取负权重）→ 等同于等权
ALL_IR_WEIGHTS = {}
total = sum(abs(v) for v in IC_DATA.values())
for f, ic in IC_DATA.items():
    if abs(ic) >= 0.02:  # 只保留 |IC_IR| >= 0.02 的
        ALL_IR_WEIGHTS[f] = round(ic / total, 4)

# 数据加载
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

def run_bt(close_panel, score, top_n=12, rebal_freq=20, stop_loss=0.20,
           use_vol_scaling=True, vol_target=0.20, max_position=0.10,
           label='default'):
    state = PortfolioState(cash=core_config.costs.initial_capital,
                           initial_capital=core_config.costs.initial_capital)
    dates = close_panel.index
    nav_list = []
    for i, date in enumerate(dates):
        if i < 120:
            nav_list.append(core_config.costs.initial_capital)
            continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else core_config.costs.initial_capital)
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
                            adj_p = p * (1 + core_config.costs.slippage_rate)
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

if __name__ == "__main__":
    t0 = time.time()
    print("=" * 70)
    print("因子权重方案对比")
    print("=" * 70)
    
    print("\n[1/3] 加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  Panel: {len(close_panel)} 天 × {len(stocks)} 只股票")
    
    print("\n[2/3] 计算因子...")
    factors_all = calc_factors_panel(close_panel, volume_panel, amount_panel)
    
    # 构建评分
    schemes = {
        'v4_29f_equal': V4_WEIGHTS,
        'v6_12f_icir_signed': V6_WEIGHTS,
        'v7_pos_ic_only': V7_WEIGHTS,
        'v8_all_ir_weighted': ALL_IR_WEIGHTS,
    }
    scores = {}
    for label, weights in schemes.items():
        valid_factors = {k: v for k, v in factors_all.items() if k in weights}
        scores[label] = composite_score(valid_factors, weights)
        print(f"  {label}: {len(weights)} 因子")
    
    print("\n[3/3] 回测...")
    results = {}
    for label, score in scores.items():
        print(f"  运行 {label}...")
        results[label] = run_bt(close_panel, score, label=label)
    
    # 对比
    print(f"\n{'='*70}")
    labels = list(results.keys())
    header = f"{'':20}"
    for l in labels:
        header += f" {l:>14}"
    print(header)
    print("─" * (20 + 15 * len(labels)))
    for key in ['annual_return', 'sharpe_ratio', 'max_drawdown', 'calmar_ratio', 'win_rate', 'total_trades', 'total_cost']:
        row = f"{key:<20}"
        for l in labels:
            v = results[l][key]
            if key in ('annual_return', 'max_drawdown'):
                row += f" {v:>13.2%}"
            elif key == 'total_cost':
                row += f" ¥{v:>12,.0f}"
            elif key == 'total_trades':
                row += f" {int(v):>14d}"
            else:
                row += f" {v:>14.4f}"
        print(row)
    
    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")
    
    # 保存
    out_path = os.path.join(DATA_DIR, "backtest_results", "factor_scheme_compare.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"结果已保存: {out_path}")
