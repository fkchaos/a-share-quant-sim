#!/usr/bin/env python3
"""
v8 最终优化：正IC因子 + 行业限制 + TP/Decay
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
from core.config import config as core_config, DEFAULT_FACTOR_WEIGHTS

# ── 权重方案 ──────────────────────────────────────────────────────
# v8: 正 IC 因子，按 IC_IR 幅度归一化
V8_WEIGHTS = {
    'vol_ratio_20':  0.22,
    'amount_ratio':  0.18,
    'rsi_6':         0.17,
    'vol_ratio_5':   0.12,
    'boll_pos_10':   0.12,
    'mom_5':         0.08,
    'rev_10':        0.06,
    'boll_pos_20':   0.05,
}

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
    amount_panel = pd.DataFrame({c: d.get('amount', d['close'] * d['volume']) for c, d in valid.items()})
    common_dates = close_panel.dropna(how='all').index
    common_dates = common_dates[(common_dates >= '2021-01-01') & (common_dates <= '2026-05-29')]
    return (
        close_panel.loc[common_dates].sort_index(),
        volume_panel.loc[common_dates].sort_index(),
        amount_panel.loc[common_dates].sort_index()
    ), list(valid.keys())

# ── 回测引擎 ──────────────────────────────────────────────────────
def run_bt(close_panel, score, top_n=12, rebal_freq=20, stop_loss=0.20,
           use_vol_scaling=True, vol_target=0.20, max_position=0.10,
           use_tp=False, tp_tiers=None, use_decay=False, label='default'):
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

if __name__ == "__main__":
    t0 = time.time()
    print("=" * 70)
    print("v8 最终优化回测")
    print("=" * 70)
    
    print("\n[1/3] 加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  Panel: {len(close_panel)} 天 × {len(stocks)} 只股票")
    
    print("\n[2/3] 计算因子...")
    factors_all = calc_factors_panel(close_panel, volume_panel, amount_panel)
    
    # 构建评分
    f_v4 = factors_all
    f_v8 = {k: v for k, v in factors_all.items() if k in V8_WEIGHTS}
    
    score_v4 = composite_score(f_v4, DEFAULT_FACTOR_WEIGHTS)
    score_v8 = composite_score(f_v8, V8_WEIGHTS)
    print(f"  v4: 29 因子等权")
    print(f"  v8: {len(V8_WEIGHTS)} 因子正IC优化权重")
    
    print("\n[3/3] 回测...")
    results = {}
    
    results['v4_baseline'] = run_bt(close_panel, score_v4, label='v4_29f')
    results['v8_base'] = run_bt(close_panel, score_v8, label='v8_8f')
    results['v8_industry'] = run_bt(close_panel, score_v8, 
                                     max_industry_weight=0.20, label='v8_ind20')
    results['v8_tp'] = run_bt(close_panel, score_v8,
                               use_tp=True, tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
                               label='v8_tp')
    results['v8_decay'] = run_bt(close_panel, score_v8,
                                  use_decay=True, label='v8_decay')
    results['v8_tp_decay'] = run_bt(close_panel, score_v8,
                                     use_tp=True, tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
                                     use_decay=True, label='v8_tp_decay')
    results['v8_ind_tp_decay'] = run_bt(close_panel, score_v8,
                                         max_industry_weight=0.20,
                                         use_tp=True, tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
                                         use_decay=True, label='v8_ind20_tp_decay')
    
    # 对比
    labels = list(results.keys())
    print(f"\n{'':>20}", end='')
    for l in labels:
        print(f" {l:>14}", end='')
    print()
    print("─" * (20 + 15 * len(labels)))
    for key in ['annual_return', 'sharpe_ratio', 'sortino_ratio', 'max_drawdown', 
                'calmar_ratio', 'win_rate', 'total_trades', 'total_cost']:
        print(f"{key:>20}", end='')
        for l in labels:
            v = results[l][key]
            if key in ('annual_return', 'max_drawdown'):
                print(f" {v:>13.2%}", end='')
            elif key == 'total_cost':
                print(f" ¥{v:>12,.0f}", end='')
            elif key == 'total_trades':
                print(f" {int(v):>14d}", end='')
            else:
                print(f" {v:>14.4f}", end='')
        print()
    
    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")
    
    out_path = os.path.join(DATA_DIR, "backtest_results", "v8_final.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"结果已保存: {out_path}")
