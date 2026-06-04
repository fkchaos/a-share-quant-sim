#!/usr/bin/env python3
"""
v6 因子优化回测
===============
基于 factor_analysis.py 的 IC 分析结果：
- 删除 17 个冗余/低 IC 因子
- 保留 12 个高 IC 因子
- 用 IC_IR 加权替代等权
- 对比 v4(29因子等权) vs v6(12因子IC_IR加权)
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
from core.config import config as core_config, DEFAULT_FACTOR_WEIGHTS, StrategyConfig, STRATEGY_PROFILES

# ── v6 优化权重（基于 IC_IR） ──────────────────────────────────────
V6_FACTOR_WEIGHTS = {
    'mom_60':       -0.1513,
    'macd_12_26':   -0.1339,
    'mom_120':      -0.1287,
    'rsi_28':       -0.1022,
    'vol_10':       -0.0965,
    'mom_20':       -0.0667,
    'vol_ratio_20': +0.0648,
    'skew_20':      -0.0640,
    'boll_width_20':-0.0607,
    'vol_change':   -0.0476,
    'amount_ratio': +0.0448,
    'rsi_6':        +0.0387,
}

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

# 回测引擎
def run_bt(close_panel, score, top_n=12, rebal_freq=20, stop_loss=0.20,
           use_vol_scaling=True, vol_target=0.20, max_position=0.10,
           max_industry_weight=0.25, label='default'):
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
        'n_factors': len(V6_FACTOR_WEIGHTS),
    }

if __name__ == "__main__":
    t0 = time.time()
    print("=" * 70)
    print("v6 因子优化回测")
    print("=" * 70)
    
    print("\n[1/3] 加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  Panel: {len(close_panel)} 天 × {len(stocks)} 只股票")
    
    print("\n[2/3] 计算因子...")
    factors_all = calc_factors_panel(close_panel, volume_panel, amount_panel)
    print(f"  共 {len(factors_all)} 个因子")
    
    # v4: 29 因子等权（用默认权重）
    print("\n[3/3] 构建评分 + 回测...")
    score_v4 = composite_score(factors_all, DEFAULT_FACTOR_WEIGHTS)
    print(f"  v4: {len(DEFAULT_FACTOR_WEIGHTS)} 因子，等权")
    
    # v6: 12 因子 IC_IR 加权
    factors_v6 = {k: v for k, v in factors_all.items() if k in V6_FACTOR_WEIGHTS}
    score_v6 = composite_score(factors_v6, V6_FACTOR_WEIGHTS)
    print(f"  v6: {len(V6_FACTOR_WEIGHTS)} 因子，IC_IR 加权")
    
    # 回测
    print("\n  运行 v4 回测...")
    m_v4 = run_bt(close_panel, score_v4, label='v4_29f_equal')
    print(f"  运行 v6 回测...")
    m_v6 = run_bt(close_panel, score_v6, label='v6_12f_icir')
    
    # 对比
    print(f"\n{'='*70}")
    print(f"{'策略对比':^70}")
    print(f"{'─'*70}")
    print(f"{'':25} {'v4(29f等权)':>14} {'v6(12f_ICIR)':>14} {'差异':>12}")
    print(f"{'─'*70}")
    for key in ['annual_return', 'sharpe_ratio', 'sortino_ratio', 'max_drawdown', 
                'calmar_ratio', 'win_rate', 'total_trades', 'total_cost']:
        v1 = m_v4[key]
        v2 = m_v6[key]
        if key in ('annual_return', 'max_drawdown', 'win_rate'):
            diff = f"{(v2-v1)*100:+.2f}%"
            v1s = f"{v1:.2%}"
            v2s = f"{v2:.2%}"
        elif key == 'total_trades':
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
        print(f"{label:<25} {v1s:>14} {v2s:>14} {diff:>12}")
    
    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")
    
    # 保存结果
    out = {
        'v4_29f_equal': m_v4,
        'v6_12f_icir': m_v6,
        'v6_weights': {k: round(v, 4) for k, v in V6_FACTOR_WEIGHTS.items()},
    }
    out_path = os.path.join(DATA_DIR, "backtest_results", "v6_factor_opt.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"结果已保存: {out_path}")
