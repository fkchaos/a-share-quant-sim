#!/usr/bin/env python3
"""
公平对比：控制其他变量，只改因子权重
======================================
所有策略共用：top_n=12, rebal_freq=20, stop_loss=0.20, vol_scaling, max_position=0.10
对比：
  v4:  29因子等权（baseline）
  v5:  29因子等权 + 行业25% + TP + Decay（当前最优）
  v6a: 12因子IC_IR加权 + 行业25% + TP + Decay
  v6b: 8因子正IC等权 + 行业25% + TP + Decay
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

# ── 因子权重方案 ──────────────────────────────────────────────────
# v4/v5: 29 因子等权
W_V5 = dict(DEFAULT_FACTOR_WEIGHTS)  # 29 因子

# v6a: 12 因子 |IC_IR| 加权（取绝对值，统一正向）
IC_12 = {
    'mom_60': 0.2236, 'macd_12_26': 0.1979, 'mom_120': 0.1902,
    'rsi_28': 0.1510, 'vol_10': 0.1426, 'atr_14': 0.1392,
    'vol_20': 0.1375, 'vol_60': 0.1321, 'mom_20': 0.0985,
    'vol_ratio_20': 0.0957, 'skew_20': 0.0945, 'boll_width_20': 0.0897,
}
total_ir = sum(IC_12.values())
W_V6A = {k: round(v / total_ir, 4) for k, v in IC_12.items()}

# v6b: 8 因子正 IC 等权
W_V6B = {
    'vol_ratio_20': 0.20, 'amount_ratio': 0.15, 'rsi_6': 0.15,
    'vol_ratio_5': 0.12, 'boll_pos_10': 0.12, 'mom_5': 0.10,
    'rev_10': 0.08, 'boll_pos_20': 0.08,
}

# v8: 18 因子 IC_IR 加权（去冗余）
W_V8 = {
    'illiquidity': +0.1806, 'boll_width_20': +0.1113, 'amplitude': +0.0749,
    'turnover_skew': -0.0715, 'mom_120': -0.0666, 'vol_20': +0.0647,
    'turnover_change': +0.0575, 'vol_ratio_20': +0.0536, 'rev_3': -0.0522,
    'boll_pos_20': +0.0459, 'amount_ratio': +0.0395, 'price_impact': +0.0384,
    'macd_12_26': -0.0294, 'mom_20': +0.0290, 'pv_corr': -0.0259,
    'chip_kurt': -0.0205, 'obv_slope': -0.0199, 'kurt_20': -0.0184,
}

# v9: v8 + log_mv（市值因子，IC_IR=+0.301）
# 先不加 log_mv，因为需要外部数据
# v9 在 config.py 的 STRATEGY_PROFILES 中配置

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

# ── 回测引擎（v5 完整逻辑） ───────────────────────────────────────
def run_bt(close_panel, score, weights_label,
           top_n=12, rebal_freq=20, stop_loss=0.20,
           use_vol_scaling=True, vol_target=0.20, max_position=0.10,
           max_industry_weight=0.25,
           use_tp=False, tp_tiers=None, use_decay=False):
    
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
        'weights': weights_label,
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

# ── 主流程 ────────────────────────────────────────────────────────
if __name__ == "__main__":
    t0 = time.time()
    print("=" * 70)
    print("因子权重公平对比（统一：行业25% + TP + Decay）")
    print("=" * 70)
    
    print("\n[1/3] 加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  Panel: {len(close_panel)} 天 × {len(stocks)} 只股票")
    
    print("\n[2/3] 计算因子...")
    factors_all = calc_factors_panel(close_panel, volume_panel, amount_panel)
    
    # 构建评分
    score_v5 = composite_score(factors_all, W_V5)
    factors_v6a = {k: v for k, v in factors_all.items() if k in W_V6A}
    score_v6a = composite_score(factors_v6a, W_V6A)
    factors_v6b = {k: v for k, v in factors_all.items() if k in W_V6B}
    score_v6b = composite_score(factors_v6b, W_V6B)
    factors_v8 = {k: v for k, v in factors_all.items() if k in W_V8}
    score_v8 = composite_score(factors_v8, W_V8)
    
    print(f"  v5:  {len(W_V5)} 因子等权")
    print(f"  v6a: {len(W_V6A)} 因子 |IC_IR| 加权")
    print(f"  v6b: {len(W_V6B)} 因子正IC等权")
    print(f"  v8:  {len(W_V8)} 因子 IC_IR加权")
    
    # 统一参数
    common = dict(
        max_industry_weight=0.25,
        use_tp=True,
        tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
        use_decay=True,
    )
    
    print("\n[3/3] 回测（统一参数：行业25% + TP + Decay）...")
    results = {}
    
    print("  v5 (29f等权)...")
    results['v5_29f'] = run_bt(close_panel, score_v5, '29f_equal', **common)
    
    print("  v6a (12f_|IC_IR|)...")
    results['v6a_12f'] = run_bt(close_panel, score_v6a, '12f_icir', **common)
    
    print("  v6b (8f_正IC)...")
    results['v6b_8f'] = run_bt(close_panel, score_v6b, '8f_pos_ic', **common)
    
    print("  v8 (18f_IC_IR)...")
    results['v8_18f'] = run_bt(close_panel, score_v8, '18f_icir', **common)
    
    # 对比
    labels = ['v5_29f', 'v6a_12f', 'v6b_8f', 'v8_18f']
    print(f"\n{'='*75}")
    print(f"{'策略对比（统一：行业25% + TP + Decay）':^75}")
    print(f"{'='*75}")
    header = f"{'':22}"
    for l in labels:
        header += f" {l:>14}"
    print(header)
    print("─" * (22 + 15 * len(labels)))
    for key in ['annual_return', 'sharpe_ratio', 'sortino_ratio', 'max_drawdown',
                'calmar_ratio', 'win_rate', 'total_trades', 'stop_loss_trades',
                'take_profit_trades', 'total_cost']:
        row = f"{key:<22}"
        for l in labels:
            v = results[l][key]
            if key in ('annual_return', 'max_drawdown'):
                row += f" {v:>13.2%}"
            elif key == 'total_cost':
                row += f" ¥{v:>12,.0f}"
            elif key in ('total_trades', 'stop_loss_trades', 'take_profit_trades'):
                row += f" {int(v):>14d}"
            else:
                row += f" {v:>14.4f}"
        print(row)
    
    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")
    
    # 保存
    out = {
        'results': results,
        'weights': {
            'v5_29f': {k: round(v, 4) for k, v in list(W_V5.items())[:5]},
            'v6a_12f': W_V6A,
            'v6b_8f': W_V6B,
        },
        'common_params': {k: str(v) for k, v in common.items()},
    }
    out_path = os.path.join(DATA_DIR, "backtest_results", "factor_fair_compare.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"结果已保存: {out_path}")
