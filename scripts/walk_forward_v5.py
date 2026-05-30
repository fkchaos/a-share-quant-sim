#!/usr/bin/env python3
"""
Walk-Forward 稳健性检验（v4 参数）
====================================
用 v4 最优参数（rf=20, vt=0.20）在滚动窗口上验证策略稳定性。

设计：
  - 全量计算一次因子/评分（而非每个 fold 重复计算）
  - 评分矩阵按日期切片给各个 fold
  - 预热期自适应：min(120, 测试窗口长度//3)
  - 调仓频率在预热期后对齐

用法：
  python walk_forward_v5.py
  python walk_forward_v5.py --train-days 252 --test-days 63 --step-days 63
"""
import sys, os, time, json, argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

import numpy as np
import pandas as pd

from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.account import PortfolioState, buy, sell, check_stop_loss, portfolio_value
from core.config import DEFAULT_FACTOR_WEIGHTS


def load_panel():
    files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
    stock_data = {}
    for f in files:
        code = f.replace(".csv", "")
        df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
        if len(df) > 0:
            stock_data[code] = df

    valid = {}
    for code, df in stock_data.items():
        if df.index.min() <= pd.Timestamp('2021-01-01') + pd.Timedelta(days=60):
            valid[code] = df

    close_panel = pd.DataFrame({c: d['close'] for c, d in valid.items()})
    volume_panel = pd.DataFrame({c: d['volume'] for c, d in valid.items()})
    amount_panel = pd.DataFrame({c: d.get('amount', d['close'] * d['volume']) for c, d in valid.items()})

    common = close_panel.dropna(how='all').index
    common = common[(common >= pd.Timestamp('2021-01-01')) & (common <= pd.Timestamp('2026-05-29'))]

    return (
        close_panel.loc[common].sort_index(),
        volume_panel.loc[common].sort_index(),
        amount_panel.loc[common].sort_index()
    ), list(valid.keys())


def run_bt_single(close_panel, score, top_n=12, rebal_freq=20, stop_loss=0.20,
                  use_vol_scaling=True, vol_target=0.20, max_position=0.10):
    """单次回测（无择时），返回绩效 dict。预热期自适应。"""
    from core.config import config

    dates = close_panel.index
    n = len(dates)
    warmup = min(120, max(20, n // 3))

    if n < warmup + rebal_freq:
        return None

    state = PortfolioState(
        cash=config.costs.initial_capital,
        initial_capital=config.costs.initial_capital
    )
    nav_list = []

    for i, date in enumerate(dates):
        if i < warmup:
            nav_list.append(config.costs.initial_capital)
            continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else config.costs.initial_capital)
            continue

        try:
            price_data = close_panel.loc[date]
            state = check_stop_loss(state, date, price_data)
        except Exception:
            nav_list.append(nav_list[-1])
            continue

        if (i - warmup) % rebal_freq == 0 and date in score.index:
            try:
                day_score = score.loc[date].dropna()
            except KeyError:
                nav_list.append(nav_list[-1])
                continue
            valid_idx = day_score.index.isin(price_data.dropna().index)
            day_score = day_score[valid_idx]

            if use_vol_scaling:
                try:
                    returns = close_panel.pct_change()
                    stock_vol = returns.rolling(20).std().loc[date]
                    vol_scale = vol_target / (stock_vol * np.sqrt(252))
                    vol_scale = vol_scale.clip(0.1, 3.0)
                    day_score = day_score * vol_scale.reindex(day_score.index).fillna(1)
                except Exception:
                    pass

            top_stocks = day_score.nlargest(top_n).index.tolist()
            if top_stocks:
                current_pv = portfolio_value(state, date, price_data)
                for c in list(state.holdings.keys()):
                    if c not in top_stocks and c in price_data.index:
                        try:
                            p = float(price_data[c])
                            if not (np.isnan(p) or p <= 0):
                                state = sell(state, c, p, date, reason='SELL')
                        except (TypeError, ValueError):
                            pass

                weights = {c: 1.0 / len(top_stocks) for c in top_stocks}
                for c in top_stocks:
                    if c not in state.holdings and c in price_data.index:
                        try:
                            p = float(price_data[c])
                            if not (np.isnan(p) or p <= 0):
                                w = weights.get(c, 1.0 / len(top_stocks))
                                target_val = min(current_pv * w, current_pv * max_position)
                                adj_p = p * (1 + config.costs.slippage_rate)
                                shares = int(target_val / adj_p / 100) * 100
                                if shares > 0:
                                    state = buy(state, c, p, date, shares=shares)
                        except (TypeError, ValueError):
                            pass

        try:
            dv = portfolio_value(state, date, price_data)
        except Exception:
            dv = nav_list[-1] if nav_list else config.costs.initial_capital
        nav_list.append(dv)

    nav = pd.Series(nav_list, index=dates[:len(nav_list)])
    rets = nav.pct_change().dropna()
    if len(rets) < 5:
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
    calmar = ann_ret / max_dd if max_dd > 0 else 0

    trades_df = pd.DataFrame(state.trade_log)
    sl_count = len(trades_df[trades_df['action'] == 'STOP_LOSS']) if len(trades_df) > 0 else 0

    return {
        'annual_return': round(float(ann_ret), 6),
        'sharpe_ratio': round(float(sharpe), 4),
        'sortino_ratio': round(float(sortino), 4),
        'max_drawdown': round(float(max_dd), 6),
        'calmar_ratio': round(float(calmar), 4),
        'total_return': round(float(total_ret), 6),
        'total_trades': len(trades_df),
        'stop_loss_trades': int(sl_count),
    }


def walk_forward(close_panel, score, train_days=252, test_days=63, step_days=63,
                 top_n=12, rebal_freq=20, stop_loss=0.20,
                 use_vol_scaling=True, vol_target=0.20, label_prefix='wf'):
    """
    Walk-Forward 分析。
    全量因子/评分只计算一次，每个 fold 切片使用，避免重复计算。
    """
    dates = close_panel.index
    n = len(dates)
    folds = []
    train_end = train_days

    while train_end + test_days <= n:
        train_start = max(0, train_end - train_days)
        test_start_idx = train_end
        test_end_idx = min(n, train_end + test_days)
        folds.append((train_start, train_end, test_start_idx, test_end_idx))
        train_end += step_days

    if not folds:
        print("  ⚠️ 数据不足以构造任何 fold")
        return []

    print(f"  Walk-Forward: {len(folds)} folds（训练 {train_days}天 / 测试 {test_days}天 / 步长 {step_days}天）")

    fold_results = []
    for fold_idx, (ts, te, tss, tee) in enumerate(folds, 1):
        test_close = close_panel.iloc[tss:tee]
        test_dates = close_panel.index[tss:tee]

        # 切片评分矩阵
        test_score = score.loc[test_dates] if len(score.index.intersection(test_dates)) > 0 else pd.DataFrame()

        if test_score.empty or len(test_close) < 20:
            print(f"  Fold {fold_idx}: 数据不足，跳过")
            continue

        m = run_bt_single(
            test_close, test_score,
            top_n=top_n, rebal_freq=rebal_freq, stop_loss=stop_loss,
            use_vol_scaling=use_vol_scaling, vol_target=vol_target
        )

        if m is None:
            print(f"  Fold {fold_idx}: 回测失败，跳过")
            continue

        period = f"{test_dates[0].strftime('%Y-%m-%d')} ~ {test_dates[-1].strftime('%Y-%m-%d')}"
        fold_results.append({'fold': fold_idx, 'period': period, **m})

        status = "✅" if m['sharpe_ratio'] > 0 else "❌"
        print(f"  Fold {fold_idx:>2} [{period}]  {status} "
              f"Sharpe={m['sharpe_ratio']:.3f}  Ret={m['annual_return']:+.1%}  DD={m['max_drawdown']:.2%}")

    return fold_results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Walk-Forward 稳健性检验")
    parser.add_argument('--train-days', type=int, default=252, help='训练窗口天数（默认 252）')
    parser.add_argument('--test-days',  type=int, default=63,  help='测试窗口天数（默认 63）')
    parser.add_argument('--step-days',  type=int, default=63,  help='滚动步长天数（默认 63）')
    parser.add_argument('--top-n',      type=int, default=12,  help='持仓股票数（默认 12）')
    parser.add_argument('--rebal-freq', type=int, default=20,  help='调仓频率天数（默认 20）')
    parser.add_argument('--vt',         type=float, default=0.20, help='vol_target（默认 0.20）')
    args = parser.parse_args()

    t0 = time.time()
    print("=" * 65)
    print("Walk-Forward 稳健性检验（v4 参数）")
    print("=" * 65)

    print("\n加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  Panel: {len(close_panel)} 天 × {len(stocks)} 只股票")

    print("\n计算因子 + 评分...")
    factors = calc_factors_panel(close_panel, volume_panel, amount_panel)
    score = composite_score(factors, DEFAULT_FACTOR_WEIGHTS)
    print(f"  因子: {len(factors)} 个，评分矩阵: {score.shape}")

    # Walk-Forward
    fold_results = walk_forward(
        close_panel, score,
        train_days=args.train_days, test_days=args.test_days, step_days=args.step_days,
        top_n=args.top_n, rebal_freq=args.rebal_freq,
        use_vol_scaling=True, vol_target=args.vt
    )

    if not fold_results:
        print("\n没有有效的 fold")
        sys.exit(1)

    sharpes = [r['sharpe_ratio'] for r in fold_results]
    rets = [r['annual_return'] for r in fold_results]
    dds = [r['max_drawdown'] for r in fold_results]
    sorts = [r['sortino_ratio'] for r in fold_results]
    positive_folds = sum(1 for s in sharpes if s > 0)

    print(f"\n{'=' * 65}")
    print(f"{'Walk-Forward 汇总':^55}")
    print(f"{'─' * 65}")
    print(f"  Fold 数:       {len(fold_results)}")
    print(f"  正夏普 fold:   {positive_folds}/{len(fold_results)} ({positive_folds/len(fold_results)*100:.0f}%)")
    print(f"  平均夏普:      {np.mean(sharpes):.3f}")
    print(f"  夏普标准差:    {np.std(sharpes):.3f}")
    print(f"  最小/最大:     {np.min(sharpes):.3f} / {np.max(sharpes):.3f}")
    print(f"  平均年化:      {np.mean(rets):.2%}")
    print(f"  平均回撤:      {np.mean(dds):.2%}")
    print(f"  最大回撤:      {np.min(dds):.2%}")
    print(f"  平均 Sortino:  {np.mean(sorts):.3f}")

    passed = np.mean(sharpes) >= 0.5 and positive_folds / len(fold_results) >= 0.6
    print(f"\n  {'✅ Walk-Forward 检验通过' if passed else '❌ Walk-Forward 检验未通过'}")

    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")

    # 保存
    out = {
        'params': {
            'train_days': args.train_days, 'test_days': args.test_days,
            'step_days': args.step_days, 'top_n': args.top_n,
            'rebal_freq': args.rebal_freq, 'vol_target': args.vt,
        },
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
    out_path = os.path.join(DATA_DIR, "backtest_results", "walk_forward_v4.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"保存: {out_path}")
