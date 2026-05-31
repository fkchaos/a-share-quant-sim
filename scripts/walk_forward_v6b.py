#!/usr/bin/env python3
"""
Walk-Forward 过拟合检测
=======================
对 v6b (8f正IC) 策略做 WF 验证
设计：
  - 训练窗口：252天（1年）
  - 测试窗口：63天（3个月）
  - 步进：63天
  - 因子权重由训练集 IC 决定（而非用全局固定权重）
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

PROFILE = STRATEGY_PROFILES["v6b_8f_pos_ic"]

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

def calc_ic_simple(factors, close_panel, forward=20):
    """快速 IC 计算，返回 {factor: ic_ir}"""
    future_ret = close_panel.pct_change(forward).shift(-forward)
    ic_results = {}
    import warnings
    warnings.filterwarnings('ignore')
    for fname, fdf in factors.items():
        common_idx = fdf.index.intersection(future_ret.index)
        if len(common_idx) < 30:
            continue
        ic_vals = []
        for date in common_idx[::5]:  # 每5天抽样加速
            f_row = fdf.loc[date].dropna()
            r_row = future_ret.loc[date].dropna()
            common_cols = f_row.index.intersection(r_row.index)
            if len(common_cols) < 10:
                continue
            f_vals = f_row[common_cols].values
            r_vals = r_row[common_cols].values
            if np.std(f_vals) < 1e-10 or np.std(r_vals) < 1e-10:
                continue
            corr = np.corrcoef(f_vals, r_vals)[0, 1]
            if not np.isnan(corr):
                ic_vals.append(corr)
        if len(ic_vals) > 5:
            ic_mean = np.mean(ic_vals)
            ic_std = np.std(ic_vals)
            ic_results[fname] = ic_mean / (ic_std + 1e-10) if ic_std > 0 else 0
    return ic_results

def build_weights_from_ic(ic_results, factor_names):
    """根据 IC_IR 构建权重"""
    weights = {}
    total = sum(abs(ic_results.get(f, 0)) for f in factor_names)
    if total == 0:
        return {f: 1.0 / len(factor_names) for f in factor_names}
    for f in factor_names:
        ir = ic_results.get(f, 0)
        if abs(ir) > 0.01:
            weights[f] = abs(ir) / total
    return weights if weights else {f: 1.0 / len(factor_names) for f in factor_names}

def run_fold(close_panel, score, dates, profile):
    """跑一个 fold 的回测"""
    state = PortfolioState(cash=200_000, initial_capital=200_000)
    nav_list = []

    for i, date in enumerate(dates):
        if i < 5:
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

        if (i - 5) % profile.rebalance_freq == 0 and date in score.index:
            day_score = score.loc[date].dropna()
            valid_idx = day_score.index.isin(price_data.dropna().index)
            day_score = day_score[valid_idx]

            if profile.use_vol_scaling:
                returns = close_panel.loc[dates[max(0,i-30):i+1]].pct_change()
                stock_vol = returns.rolling(20).std().loc[date] if date in returns.index else returns.std()
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
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    ann_ret = (1 + total_ret) ** (252 / max(len(rets), 1)) - 1
    ann_vol = rets.std() * np.sqrt(252) if len(rets) > 1 else 0
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    max_dd = ((nav.cummax() - nav) / nav.cummax()).max() if len(nav) > 1 else 0
    calmar = ann_ret / max_dd if max_dd > 0 else 0
    return {
        'total_return': round(float(total_ret), 4),
        'annual_return': round(float(ann_ret), 4),
        'sharpe': round(float(sharpe), 4),
        'max_dd': round(float(max_dd), 4),
        'calmar': round(float(calmar), 4),
        'n_days': len(rets),
    }

def main():
    TRAIN_DAYS = 252
    TEST_DAYS = 63
    STEP_DAYS = 63

    print("=" * 70)
    print(f"Walk-Forward 验证：v6b_8f_pos_ic")
    print(f"训练窗：{TRAIN_DAYS}天 | 测试窗：{TEST_DAYS}天 | 步进：{STEP_DAYS}天")
    print("=" * 70)

    print(f"\n[1/4] 加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    all_dates = close_panel.index
    print(f"  {len(all_dates)} 天 × {len(stocks)} 只股票")

    # 预先计算全量因子
    print(f"\n[2/4] 预计算全量因子...")
    all_factors = calc_factors_panel(close_panel, volume_panel, amount_panel)

    n = len(all_dates)
    fold = 0
    train_end = TRAIN_DAYS
    fold_results = []

    print(f"\n[3/4] Walk-Forward 回测...")
    while train_end + TEST_DAYS <= n:
        fold += 1
        train_start = max(0, train_end - TRAIN_DAYS)
        test_start = train_end
        test_end = min(n, train_end + TEST_DAYS)

        train_dates = all_dates[train_start:test_start]
        test_dates = all_dates[test_start:test_end]

        # 训练集：计算因子 → IC → 权重
        sub_close = close_panel.loc[train_dates]
        sub_vol = volume_panel.loc[train_dates]
        sub_amt = amount_panel.loc[train_dates]
        sub_factors = calc_factors_panel(sub_close, sub_vol, sub_amt)

        ic_results = calc_ic_simple(sub_factors, sub_close)
        factor_names = list(PROFILE.factor_weights.keys())
        weights = build_weights_from_ic(ic_results, factor_names)

        # 用训练集 IC 权重构建全量评分
        valid_factors = {k: v for k, v in all_factors.items() if k in weights}
        score = composite_score(valid_factors, weights)

        # 测试集回测
        test_close = close_panel.loc[test_dates]
        metrics = run_fold(test_close, score, test_dates, PROFILE)

        print(f"  Fold {fold:2d}: {test_dates[0].date()} ~ {test_dates[-1].date()} | "
              f"Return={metrics['annual_return']:.2%}  Sharpe={metrics['sharpe']:.2f}  "
              f"MaxDD={metrics['max_dd']:.2%}")

        fold_results.append({
            'fold': fold,
            'train_range': f"{train_dates[0].date()} ~ {train_dates[-1].date()}",
            'test_range': f"{test_dates[0].date()} ~ {test_dates[-1].date()}",
            **metrics,
        })

        train_end += STEP_DAYS

    # 汇总
    print(f"\n{'='*70}")
    print(f"汇总（{len(fold_results)} folds）")
    print(f"{'='*70}")

    avg_ret = np.mean([r['annual_return'] for r in fold_results])
    avg_sharpe = np.mean([r['sharpe'] for r in fold_results])
    avg_dd = np.mean([r['max_dd'] for r in fold_results])
    avg_calmar = np.mean([r['calmar'] for r in fold_results])
    pos_folds = sum(1 for r in fold_results if r['annual_return'] > 0)

    print(f"  平均年化收益: {avg_ret:.2%}")
    print(f"  平均 Sharpe: {avg_sharpe:.2f}")
    print(f"  平均最大回撤: {avg_dd:.2%}")
    print(f"  平均 Calmar: {avg_calmar:.2f}")
    print(f"  正收益 folds: {pos_folds}/{len(fold_results)}")

    # 保存
    out = {
        'strategy': 'v6b_8f_pos_ic',
        'params': {'train_days': TRAIN_DAYS, 'test_days': TEST_DAYS, 'step_days': STEP_DAYS},
        'summary': {
            'avg_annual_return': round(float(avg_ret), 4),
            'avg_sharpe': round(float(avg_sharpe), 4),
            'avg_max_dd': round(float(avg_dd), 4),
            'avg_calmar': round(float(avg_calmar), 4),
            'positive_folds': pos_folds,
            'total_folds': len(fold_results),
        },
        'folds': fold_results,
    }
    out_path = os.path.join(DATA_DIR, "backtest_results", "wf_v6b.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\n结果已保存: {out_path}")

if __name__ == "__main__":
    main()
