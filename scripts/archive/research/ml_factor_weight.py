#!/usr/bin/env python3
"""
ML Factor Weight Prediction — Walk-Forward 回测 (v2)
=====================================================

ML 预测每个因子在下一周期的 IC → 用预测 IC 作为 composite_score 权重。

简化版：不做复杂的 IC 面板预计算，而是：
  1. 在训练窗口内，滚动计算每个因子的 IC 序列
  2. 用过去 N 天的 IC 统计量（均值、std、动量）作为特征
  3. 标签 = 未来 forward_period 天的 IC
  4. 训练 LightGBM 预测各因子 IC
  5. 用预测 IC 加权 composite_score → 选股 → 风控

用法：
    python ml_factor_weight.py --start 2021-01-01 --end 2025-12-31
"""

import argparse, json, os, sys, time
from datetime import datetime
import numpy as np, pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import config as core_config, STRATEGY_PROFILES
from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.data import load_and_build_panel

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(_BASE_DIR, "data"))
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")

LGB_PARAMS = {
    'objective': 'regression_l1', 'metric': 'mae',
    'learning_rate': 0.05, 'num_leaves': 31, 'min_data_in_leaf': 10,
    'feature_fraction': 0.8, 'bagging_fraction': 0.8, 'bagging_freq': 5,
    'lambda_l1': 0.1, 'lambda_l2': 1.0, 'verbose': -1,
}


def calc_ic(date, factors, close_panel, fwd):
    """计算单日期的各因子 IC"""
    if date not in close_panel.index:
        return None
    idx = close_panel.index.get_loc(date)
    fwd_idx = idx + fwd
    if fwd_idx >= len(close_panel):
        return None
    ret = close_panel.iloc[fwd_idx] / close_panel.iloc[idx] - 1
    ics = {}
    for fn, fdf in factors.items():
        if not isinstance(fdf, pd.DataFrame) or date not in fdf.index:
            continue
        common = fdf.loc[date].dropna().index.intersection(ret.dropna().index)
        if len(common) < 20:
            ics[fn] = 0.0; continue
        c = np.corrcoef(fdf.loc[date, common].values, ret[common].values)[0, 1]
        ics[fn] = c if not np.isnan(c) else 0.0
    return ics


def build_weight_features(ic_series_dict, lookback):
    """从 IC 序列构建特征：均值、std、动量"""
    features = {}
    for fn, vals in ic_series_dict.items():
        if len(vals) < lookback:
            return None
        recent = vals[-lookback:]
        features[f'{fn}_ic_mean'] = np.mean(recent)
        features[f'{fn}_ic_std'] = np.std(recent)
        features[f'{fn}_ic_mom'] = np.mean(recent[-5:]) - np.mean(recent[-10:]) if len(recent) >= 10 else 0
        third = max(1, len(recent) // 3)
        features[f'{fn}_ic_decay'] = np.mean(recent[-third:]) / (np.mean(recent[:third]) + 1e-6)
    return features


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default=None)
    parser.add_argument("--exec-timing", choices=["close", "open"], default="close")
    parser.add_argument("--top-n", type=int, default=12)
    parser.add_argument("--fwd", type=int, default=5)
    parser.add_argument("--lookback", type=int, default=20)
    parser.add_argument("--train-days", type=int, default=126)
    parser.add_argument("--test-days", type=int, default=63)
    parser.add_argument("--strategy", nargs="+", default=[])
    parser.add_argument("--no-v6b", action="store_true")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    print("=" * 60)
    print("ML Factor Weight Prediction v2")
    print("=" * 60)
    t0 = time.time()

    # 加载数据
    need_open = (args.exec_timing == "open")
    loaded, codes = load_and_build_panel(args.start, args.end, need_open=need_open,
                                         need_hl=True, market_filter=core_config.market)
    close_panel = loaded[0]
    open_panel = loaded[3] if len(loaded) > 3 and need_open else None
    high_panel = loaded[4] if len(loaded) > 4 else None
    low_panel = loaded[5] if len(loaded) > 5 else None
    factors = calc_factors_panel(close_panel, loaded[1], loaded[2],
                                  open_panel=open_panel, high_panel=high_panel, low_panel=low_panel)
    factor_names = [n for n, f in factors.items() if isinstance(f, pd.DataFrame)]
    print(f"  {close_panel.shape[0]}d × {close_panel.shape[1]}s, {len(factor_names)} factors")

    dates = close_panel.index
    n = len(dates)
    fwd = args.fwd
    lookback = args.lookback
    train_days = args.train_days
    test_days = args.test_days

    # =========================================================
    # Walk-Forward: 对每个 fold，训练 IC 预测模型
    # =========================================================
    score_panel = pd.DataFrame(index=dates, columns=close_panel.columns, dtype=float)
    import lightgbm as lgb

    fold = 0
    train_end = train_days + lookback

    while train_end + test_days <= n:
        fold += 1
        train_start = max(0, train_end - train_days)
        test_start = train_end
        test_end = min(n, train_end + test_days)
        test_dates = dates[test_start:test_end]

        # 在训练窗口内收集 IC 序列
        ic_hist = {fn: [] for fn in factor_names}
        sample_X, sample_y, sample_dates_list = [], [], []
        for ti in range(train_start, train_end - fwd):
            d = dates[ti]
            ics = calc_ic(d, factors, close_panel, fwd)
            if ics is None:
                # 更新序列（用 0 填充缺失）
                for fn in factor_names:
                    ic_hist[fn].append(ics.get(fn, 0.0) if ics else 0.0)
                continue
            for fn in factor_names:
                ic_hist[fn].append(ics.get(fn, 0.0))
            # 构建特征（需要至少 lookback 天的历史）
            feat = build_weight_features(ic_hist, lookback)
            if feat is None:
                continue
            # 标签：下一期的 IC
            label_ic = calc_ic(dates[ti + fwd], factors, close_panel, fwd)
            if label_ic is None:
                continue
            sample_X.append(feat)
            sample_y.append(label_ic)
            sample_dates_list.append(dates[ti])

        if len(sample_X) < 30:
            train_end += test_days
            continue

        X_train = pd.DataFrame(sample_X)
        y_train = pd.DataFrame(sample_y)[factor_names].fillna(0)

        # 预测测试期的 IC（用训练窗口末尾的 IC 历史构建测试特征）
        test_feat = build_weight_features(ic_hist, lookback)
        if test_feat is None:
            train_end += test_days
            continue
        X_test = pd.DataFrame([test_feat])

        # 为每个因子训练模型
        pred_ics = {}
        for fn in factor_names:
            y_j = y_train[fn]
            if y_j.abs().sum() < 1e-10:
                pred_ics[fn] = 0.0
                continue
            try:
                model = lgb.train(LGB_PARAMS, lgb.Dataset(X_train, label=y_j),
                                  num_boost_round=200, callbacks=[lgb.log_evaluation(period=0)])
                pred_ics[fn] = model.predict(X_test)[0]
            except Exception:
                pred_ics[fn] = 0.0

        # 预测 IC → 权重
        weights = {}
        for fn in factor_names:
            w = np.clip(pred_ics.get(fn, 0), -0.25, 0.25)
            weights[fn] = w
        total = sum(abs(v) for v in weights.values())
        if total > 0:
            weights = {k: v / total for k, v in weights.items()}

        # composite_score with predicted weights
        common = {k: v for k, v in factors.items() if k in weights and abs(weights[k]) > 1e-6}
        if not common:
            train_end += test_days
            continue

        score = composite_score(common, weights)

        # 填充测试期评分面板
        for td in test_dates:
            if td in score.index and td in score_panel.index:
                cols = score_panel.columns.intersection(score.loc[td].dropna().index)
                score_panel.loc[td, cols] = score.loc[td, cols]

        if fold % 5 == 0:
            print(f"  fold {fold}: {test_dates[0].date()}~{test_dates[-1].date()} "
                  f"| top3: {sorted(weights, key=lambda x: abs(weights[x]), reverse=True)[:3]}")

        train_end += test_days

    score_panel = score_panel.fillna(0)

    # =========================================================
    # 回测
    # =========================================================
    from scripts.run_backtest import run_backtest
    bt_kwargs = dict(
        top_n=args.top_n, rebalance_freq=fwd, stop_loss=0.20,
        max_position=0.10, max_industry_weight=0.25,
        max_daily_turnover=0, weight_method='equal',
        stock_names=None, exec_timing=args.exec_timing,
        use_vol_scaling=True, vol_target=0.20,
    )
    if need_open:
        bt_kwargs['open_panel'] = open_panel

    ml_m, ml_nav, ml_tr = run_backtest(close_panel, score_panel, label='ml_ic_weight', **bt_kwargs)
    metrics_list = [ml_m]
    nav_dict = {'ml_ic_weight': ml_nav}
    print(f"\n  ml_ic_weight: {ml_m['annual_return']:.2%} / {ml_m['sharpe_ratio']:.2f} / {ml_m['max_drawdown']:.2%}")

    # 基准
    if not args.no_v6b and args.strategy:
        for sn in args.strategy:
            if sn not in STRATEGY_PROFILES:
                continue
            p = STRATEGY_PROFILES[sn]
            sc = composite_score({k: v for k, v in factors.items() if k in p.factor_weights}, p.factor_weights) \
                if p.factor_weights else composite_score(factors)
            m, nav, tr = run_backtest(close_panel, sc, label=sn, **bt_kwargs)
            metrics_list.append(m)
            nav_dict[sn] = nav

    # 输出
    total_time = time.time() - t0
    print(f"\n{'=' * 60}\n完成 ({total_time:.1f}s)")
    for m in metrics_list:
        print(f"  {m['label']:<25} {m['annual_return']:>8.2%} {m['sharpe_ratio']:>7.2f} {m['max_drawdown']:>9.2%}")

    out_dir = args.output_dir or os.path.join(REPORT_DIR, datetime.now().strftime("%Y%m%d_%H%M%S_ml_ic"))
    os.makedirs(out_dir, exist_ok=True)
    for l, nav in nav_dict.items():
        nav.to_csv(os.path.join(out_dir, f"nav_{l}.csv"))
    print(f"\n结果已保存: {out_dir}/")


if __name__ == "__main__":
    main()
