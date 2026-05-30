#!/usr/bin/env python3
"""
Walk-Forward 稳健性检验
========================
用 v5 参数（rf=10, vt=0.10）在滚动窗口上验证策略稳定性。
窗口：训练期 252 天（1年）→ 测试期 63 天（1季度）→ 滚动 63 天
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


def run_bt(close_panel, score, top_n=12, rebal_freq=10, stop_loss=0.20,
           use_vol_scaling=True, vol_target=0.10, max_position=0.10,
           label='default'):
    from core.config import config
    state = PortfolioState(cash=config.costs.initial_capital,
                           initial_capital=config.costs.initial_capital)
    dates = close_panel.index
    warmup = min(120, max(1, len(dates) // 3))
    nav_list = []

    for i, date in enumerate(dates):
        if i < warmup:
            nav_list.append(config.costs.initial_capital)
            continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1])
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
    if len(rets) < 10:
        return None
    years = max(len(nav) / 252, 0.01)
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = rets.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    downside = rets[rets < 0]
    downside_vol = downside.std() * np.sqrt(252) if len(downside) > 1 else 0
    sortino = ann_ret / downside_vol if downside_vol > 0 else 0
    max_dd = ((nav.cummax() - nav) / nav.cummax()).max()

    return {
        'annual_return': round(float(ann_ret), 6),
        'sharpe_ratio': round(float(sharpe), 4),
        'sortino_ratio': round(float(sortino), 4),
        'max_drawdown': round(float(max_dd), 6),
        'total_return': round(float(total_ret), 6),
    }


if __name__ == "__main__":
    from core.factors import calc_factors_panel

    t0 = time.time()
    print("=" * 65)
    print("Walk-Forward 稳健性检验（v5 参数：rf=10, vt=0.10）")
    print("=" * 65)

    # 加载全量数据
    print("\n加载数据...")
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
        if df.index.min() <= pd.Timestamp('2021-01-01') + pd.Timedelta(days=30):
            valid_[code] = df

    close_full = pd.DataFrame({c: d['close'] for c, d in valid_.items()})
    vol_full   = pd.DataFrame({c: d['volume'] for c, d in valid_.items()})
    amt_full   = pd.DataFrame({c: d.get('amount', d['close'] * d['volume']) for c, d in valid_.items()})

    common = close_full.dropna(how='all').index
    common = common[(common >= '2021-01-01') & (common <= '2026-05-29')]
    close_full = close_full.loc[common].sort_index()
    vol_full   = vol_full.loc[common].sort_index()
    amt_full   = amt_full.loc[common].sort_index()

    print(f"Panel: {len(close_full)} 天 × {len(valid_)} 只股票")

    # Walk-Forward 参数
    train_days = 252   # 训练窗口：1年
    test_days  = 63    # 测试窗口：1季度
    step_days  = 63    # 滚动步长：1季度

    dates = close_full.index
    n = len(dates)
    folds = []
    train_end = train_days

    while train_end + test_days <= n:
        train_start = max(0, train_end - train_days)
        test_start = train_end
        test_end = min(n, train_end + test_days)
        folds.append((train_start, train_end, test_start, test_end))
        train_end += step_days

    print(f"\nWalk-Forward 设置: {len(folds)} folds")
    print(f"  训练窗口: {train_days}天  测试窗口: {test_days}天  步长: {step_days}天")

    fold_results = []
    combined_navs = []

    for fold_idx, (ts, te, tss, tee) in enumerate(folds, 1):
        fold_close = close_full.iloc[ts:tee]
        fold_vol   = vol_full.iloc[ts:tee]
        fold_amt   = amt_full.iloc[ts:tee]

        test_close = fold_close.iloc[(tss - ts):(tee - ts)]
        test_dates = fold_close.index[(tss - ts):(tee - ts)]

        # 在训练+测试窗口上计算因子
        factors = calc_factors_panel(fold_close, fold_vol, fold_amt)
        score = composite_score(factors, DEFAULT_FACTOR_WEIGHTS)

        # 只取测试期评分
        test_score = score.loc[test_dates]

        m = run_bt(test_close, test_score, label=f'wf_fold{fold_idx}')

        if m is None:
            print(f"  Fold {fold_idx}: 数据不足，跳过")
            continue

        period = f"{test_dates[0].strftime('%Y-%m-%d')} ~ {test_dates[-1].strftime('%Y-%m-%d')}"
        fold_results.append({
            'fold': fold_idx,
            'period': period,
            **m
        })

        status = "✅" if m['sharpe_ratio'] > 0 else "❌"
        print(f"  Fold {fold_idx:>2} [{period}]  {status} "
              f"Sharpe={m['sharpe_ratio']:.3f}  Ret={m['annual_return']:+.1%}  DD={m['max_drawdown']:.2%}")

    # 汇总
    if not fold_results:
        print("\n没有有效的 fold")
        sys.exit(1)

    sharpes = [r['sharpe_ratio'] for r in fold_results]
    rets    = [r['annual_return'] for r in fold_results]
    dds     = [r['max_drawdown'] for r in fold_results]
    sorts   = [r['sortino_ratio'] for r in fold_results]

    positive_folds = sum(1 for s in sharpes if s > 0)

    print(f"\n{'=' * 65}")
    print(f"{'Walk-Forward 汇总':^65}")
    print(f"{'─' * 65}")
    print(f"  Fold 数:       {len(fold_results)}")
    print(f"  正夏普 fold:   {positive_folds}/{len(fold_results)} ({positive_folds/len(fold_results)*100:.0f}%)")
    print(f"  平均夏普:      {np.mean(sharpes):.3f}")
    print(f"  夏普标准差:    {np.std(sharpes):.3f}")
    print(f"  最小夏普:      {np.min(sharpes):.3f}")
    print(f"  最大夏普:      {np.max(sharpes):.3f}")
    print(f"  平均年化:      {np.mean(rets):.2%}")
    print(f"  平均回撤:      {np.mean(dds):.2%}")
    print(f"  最大回撤:      {np.min(dds):.2%}")
    print(f"  平均 Sortino:  {np.mean(sorts):.3f}")

    # 通过标准
    passed = True
    if np.mean(sharpes) < 0.5:
        print(f"\n  ❌ 平均夏普 < 0.5，稳健性不足")
        passed = False
    if positive_folds / len(fold_results) < 0.6:
        print(f"\n  ❌ 正夏普 fold < 60%，稳定性不足")
        passed = False
    if passed:
        print(f"\n  ✅ Walk-Forward 检验通过")

    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")

    # 保存
    out = {
        'params': {'rebal_freq': 10, 'vol_target': 0.10, 'top_n': 12, 'stop_loss': 0.20},
        'summary': {
            'num_folds': len(fold_results),
            'positive_folds': positive_folds,
            'avg_sharpe': round(float(np.mean(sharpes)), 4),
            'std_sharpe': round(float(np.std(sharpes)), 4),
            'avg_return': round(float(np.mean(rets)), 6),
            'avg_drawdown': round(float(np.mean(dds)), 6),
            'passed': passed,
        },
        'folds': fold_results,
    }
    out_path = os.path.join(DATA_DIR, "backtest_results", "walk_forward_v5.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"保存: {out_path}")
