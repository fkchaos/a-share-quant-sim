#!/usr/bin/env python3
"""
A股量化回测系统 - 统一回测工具
===============================

整合所有回测功能为单一命令行工具：

    python run_backtest.py                          # 默认：全策略回测对比
    python run_backtest.py --strategy v3            # 仅跑 v3 baseline
    python run_backtest.py --strategy v3 --param top_n=15 rebalance_freq=10  # 指定参数
    python run_backtest.py --scan                   # 参数网格扫描
    python run_backtest.py --compare-two v3 v5      # 对比两个策略
    python run_backtest.py --start 2023-01-01 --end 2024-12-31  # 指定回测区间
    python run_backtest.py --report-markdown > backtest_report.md   # 输出Markdown报告

输出：
    data/backtest_results/YYYYMMDD_HHMMSS/
        ├── summary.json          # 全部策略绩效指标
        ├── comparison.csv        # 策略对比表
        ├── nav_v3.csv            # 各策略净值曲线
        ├── trades_v3.csv         # 交易记录
        ├── param_scan.json       # 参数扫描结果（如有）
        └── report.md             # Markdown 回测报告

策略：
    v3_baseline     – FACTOR_WEIGHTS 加权（29因子, top_n=20, rebal=5, stop=15%）
    v3_optimized    – FACTOR_WEIGHTS 加权 + vol_scaling（29因子, top_n=12, rebal=20, stop=20%）
    ic_ir_weighted  – IC-IR 因子加权（用 IC 信息比率给因子赋权）
    ic_selected     – IC-IR 加权 + 仅保留有效因子（|IC_IR|>=0.03）
    markowitz       – 等权因子 + Markowitz 组合权重优化

架构：
    所有因子计算、评分、交易逻辑均委托给 core/ 引擎。
    core/factors.py   → calc_factors_panel() 计算 29 个因子
    core/scoring.py   → composite_score() / composite_score_equal() 合成评分
    core/config.py    → DEFAULT_FACTOR_WEIGHTS 29 因子权重（权威来源）
    core/account.py   → PortfolioState + buy/sell/check_stop_loss 交易引擎

依赖：
    - Python 3.11+, pandas, numpy, scipy
    - data/daily/*.csv  (日 K 线数据，由 update_daily_data.py 维护)
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

# Ensure repo root is on sys.path so `core` package is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from itertools import product

import numpy as np
import pandas as pd

# ── Core engine (single source of truth) ────────────────────────────
from core.config import config as core_config
from core.factors import calc_factors_panel
from core.scoring import composite_score, composite_score_equal, standardize
from core.account import PortfolioState, buy, sell, check_stop_loss, portfolio_value

# ============================================================
# 配置
# ============================================================
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")

# Module-level constants (override via config.yaml or CLI)
START_DATE = "2021-01-01"
END_DATE = datetime.now().strftime("%Y-%m-%d")

FACTOR_WEIGHTS = core_config.factor_weights  # 权威权重，来自 core/config.py


# ============================================================
# 数据加载
# ============================================================
def load_and_build_panel(start_date=None, end_date=None):
    """加载日 K 线数据并构建 panel。"""
    sd = start_date or START_DATE
    ed = end_date or END_DATE

    files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
    if not files:
        print(f"❌ {DAILY_DIR} 下没有 CSV 文件，请先运行 update_daily_data.py")
        sys.exit(1)

    all_data = {}
    for f in files:
        code = f.replace(".csv", "")
        df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
        df = df[(df.index >= sd) & (df.index <= ed)]
        if len(df) > 0:
            all_data[code] = df

    # 过滤覆盖度不足的股票
    valid = {}
    for code, df in all_data.items():
        if df.index.min() <= pd.Timestamp(sd) + pd.Timedelta(days=30) and \
           df.index.max() >= pd.Timestamp(ed) - pd.Timedelta(days=30):
            valid[code] = df

    close_panel = pd.DataFrame({c: d['close'] for c, d in valid.items()})
    volume_panel = pd.DataFrame({c: d['volume'] for c, d in valid.items()})
    amount_panel = pd.DataFrame({c: d['amount'] for c, d in valid.items()})

    common_dates = close_panel.dropna(how='all').index
    common_dates = common_dates[(common_dates >= sd) & (common_dates <= ed)]

    return (
        close_panel.loc[common_dates].sort_index(),
        volume_panel.loc[common_dates].sort_index(),
        amount_panel.loc[common_dates].sort_index()
    ), list(valid.keys())


# ============================================================
# IC 分析
# ============================================================
def calc_ic_series(factor_df, forward_returns):
    """计算因子 IC 序列。"""
    common_idx = factor_df.index.intersection(forward_returns.index)
    ic_values = pd.Series(index=common_idx, dtype=float)

    for date in common_idx:
        f_vals = factor_df.loc[date]
        r_vals = forward_returns.loc[date]
        valid = f_vals.dropna().index.intersection(r_vals.dropna().index)
        if len(valid) < 10:
            continue
        f = f_vals[valid].values
        r = r_vals[valid].values
        corr = np.corrcoef(f, r)[0, 1]
        ic_values.loc[date] = corr if not np.isnan(corr) else 0

    return ic_values.dropna()


def run_ic_analysis(factors, close_panel, forward_period=5):
    """全面 IC 分析，返回各因子 IC 统计。"""
    forward_returns = close_panel.pct_change(forward_period).shift(-forward_period)
    results = {}

    for name, factor_df in factors.items():
        ic = calc_ic_series(factor_df, forward_returns)
        if len(ic) < 10:
            continue
        ic_mean = ic.mean()
        ic_std = ic.std()
        ir = ic_mean / ic_std if ic_std > 0 else 0
        results[name] = {
            'ic_mean': ic_mean, 'ic_std': ic_std, 'ic_ir': ir,
            'ic_positive_rate': (ic > 0).mean()
        }

    return results


def select_factors_ic(ic_results, min_abs_ir=0.03, min_positive_rate=0.50):
    """根据 IC 选择有效因子。"""
    selected = {}
    discarded = []
    for name, stats in ic_results.items():
        if abs(stats['ic_ir']) >= min_abs_ir and stats['ic_positive_rate'] >= min_positive_rate:
            selected[name] = stats
        else:
            discarded.append(name)
    return selected, discarded


# ============================================================
# Markowitz 组合优化
# ============================================================
def markowitz_optimize(expected_returns, cov_matrix, max_weight=0.15):
    """简化的 Markowitz 均值-方差优化。"""
    from scipy.optimize import minimize
    n = len(expected_returns)
    if n == 0:
        return {}

    mu = expected_returns.values
    sigma = cov_matrix.values + np.eye(n) * 1e-6

    def objective(w):
        return -(w @ mu - 0.5 * w @ sigma @ w)

    constraints = [{'type': 'eq', 'fun': lambda w: np.sum(w) - 1}]
    bounds = [(0, max_weight)] * n
    w0 = np.ones(n) / n

    try:
        result = minimize(objective, w0, method='SLSQP', bounds=bounds,
                         constraints=constraints, options={'maxiter': 1000, 'ftol': 1e-9})
        if result.success:
            weights = np.maximum(result.x, 0)
            weights /= weights.sum()
            return dict(zip(expected_returns.index, weights))
    except Exception:
        pass

    return dict(zip(expected_returns.index, np.ones(n) / n))


# ============================================================
# 回测引擎（使用 core.account 交易逻辑 → 与模拟盘一致）
# ============================================================
def run_backtest(close_panel, score, top_n=12, rebalance_freq=20, stop_loss=0.20,
                 max_position=0.10, use_vol_scaling=True, vol_target=0.20,
                 weight_method='equal', label='default',
                 initial_capital=None,
                 max_industry_weight=0.25, max_daily_turnover=0,
                 stock_names=None,
                 use_atr_stop=False, atr_k=2.0,
                 use_take_profit=False, tp_tiers=None,
                 use_holding_decay=False):
    """
    完整回测引擎。

    交易逻辑全部委托给 core.account（buy/sell/check_stop_loss），
    与 sim_daily.py 使用同一套代码，保证一致性。

    weight_method: 'equal' | 'markowitz'
    """
    from core.account import PortfolioState, buy, sell, check_stop_loss, portfolio_value
    from core.account import check_take_profit, apply_holding_decay
    from core.config import config

    icap = initial_capital or config.costs.initial_capital
    dates = close_panel.index

    # 初始化 core.account 状态
    state = PortfolioState(
        cash=icap,
        initial_capital=icap,
    )
    nav_list = []
    rebal_count = 0

    # ── Diagnostic: print key config to detect silent misconfiguration ──
    if label == 'default' or os.environ.get('BACKTEST_DEBUG'):
        _industry_enabled = bool(stock_names) and max_industry_weight and max_industry_weight > 0
        print(f"  📋 backtest[{label}]: top_n={top_n} rf={rebal_freq} sl={stop_loss} "
              f"vol_scale={use_vol_scaling} vt={vol_target} "
              f"industry_cap={max_industry_weight if _industry_enabled else 'OFF'} "
              f"tp={use_take_profit} decay={use_holding_decay} atr={use_atr_stop}")

    # ── 预计算 ATR 面板（用于 ATR 自适应止损）──
    # 使用 close-to-close range 近似 ATR（与 core/factors.py 一致）
    atr_panel = None
    if use_atr_stop:
        ct = close_panel.rolling(2).max() - close_panel.rolling(2).min()
        atr_norm = ct.rolling(14).mean() / (close_panel + 1e-10)
        atr_panel = atr_norm * close_panel  # absolute ATR value

    for i, date in enumerate(dates):
        if i < 120:
            nav_list.append(icap)
            continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else icap)
            continue

        price_data = close_panel.loc[date]

        # ── 1. 止损（委托 core.account.check_stop_loss）──
        atr_day = None
        if atr_panel is not None and date in atr_panel.index:
            atr_day = atr_panel.loc[date]
        state = check_stop_loss(state, date, price_data, atr_data=atr_day)

        # ── 1b. 分级止盈（委托 core.account.check_take_profit）──
        if use_take_profit:
            state = check_take_profit(state, date, price_data, tiers=tp_tiers)

        # ── 1c. 持有期 decay（委托 core.account.apply_holding_decay）──
        if use_holding_decay:
            state = apply_holding_decay(state, date, price_data,
                                        rebalance_freq=rebalance_freq)

        # ── 2. 调仓 ──────────────────────────────────────────
        if (i - 120) % rebalance_freq == 0 and date in score.index:
            rebal_count += 1
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

                # 卖出不在目标中的（对每只股票逐个 sell）
                for c in list(state.holdings.keys()):
                    if c not in top_stocks and c in price_data.index:
                        p = price_data[c]
                        if not pd.isna(p) and p > 0:
                            state = sell(state, c, p, date, reason='SELL')

                # 权重分配
                if weight_method == 'markowitz':
                    top_scores = day_score[top_stocks].fillna(0)
                    expected_ret = top_scores / (top_scores.sum() + 1e-10)
                    ret_mat = close_panel[top_stocks].pct_change()
                    cov = ret_mat.iloc[max(0, i-60):i].cov() if i >= 60 else ret_mat.cov()
                    if cov is not None and not cov.empty:
                        weights = markowitz_optimize(expected_ret, cov, max_weight=max_position)
                    else:
                        from core.account import allocate_weights
                        weights = allocate_weights(top_stocks, price_data, method='equal',
                                                   close_panel=close_panel, max_position=max_position)
                else:
                    # Delegate to core.account.allocate_weights (equal / vol_inverse / markowitz)
                    from core.account import allocate_weights
                    _method_map = {'weighted': 'equal', 'equal': 'equal', 'vol_inverse': 'vol_inverse'}
                    _method = _method_map.get(weight_method, 'equal')
                    weights = allocate_weights(top_stocks, price_data, method=_method,
                                               close_panel=close_panel, max_position=max_position)

                # ── 2a. 行业仓位上限 ──────────────────────────────────
                if max_industry_weight and max_industry_weight > 0 and stock_names:
                    from industry import get_industry, cap_industry_weights
                    code_industry_map = {
                        c: get_industry(c, stock_names.get(c, "")) for c in top_stocks
                    }
                    weights, _ = cap_industry_weights(
                        weights, code_industry_map, max_industry_weight
                    )

                # ── 2b. 换手率限制 ────────────────────────────────────
                if max_daily_turnover and max_daily_turnover > 0:
                    from portfolio_controls import cap_daily_turnover
                    price_dict = {c: price_data[c] for c in top_stocks
                                  if c in price_data.index and not pd.isna(price_data[c]) and price_data[c] > 0}
                    # 加入当前持仓中不在 top_stocks 的（换手率计算需要）
                    for c in state.holdings:
                        if c not in price_dict and c in price_data.index and not pd.isna(price_data[c]) and price_data[c] > 0:
                            price_dict[c] = price_data[c]
                    weights, _ = cap_daily_turnover(
                        account=None, target_weights=weights,
                        prices=price_dict, max_turnover=max_daily_turnover,
                        current_state=state,
                    )

                # 对目标持仓买入（对每只股票调用 core.account.buy）
                for c in top_stocks:
                    if c not in state.holdings and c in price_data.index and not pd.isna(price_data[c]):
                        p = price_data[c]
                        if p > 0:
                            w = weights.get(c, 1.0 / len(top_stocks))
                            target_val = min(current_pv * w, current_pv * max_position)
                            # 计算股数 (委托 buy 自动计算)
                            # 但 buy 是"尽可能买"模式，需要传入 shares
                            from core.account import compute_buy_shares
                            adj_p = p * (1 + config.costs.slippage_rate)
                            shares = int(target_val / adj_p / 100) * 100
                            if shares > 0:
                                state = buy(state, c, p, date, shares=shares)

        # ── 记录净值 ──
        dv = portfolio_value(state, date, price_data)
        nav_list.append(dv)

    # 绩效指标
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
    # Sortino ratio: 只计下行波动
    downside = rets[rets < 0]
    downside_vol = downside.std() * np.sqrt(252) if len(downside) > 1 else 0
    sortino = ann_ret / downside_vol if downside_vol > 0 else 0

    trades_df = pd.DataFrame(state.trade_log)
    sl_count = len(trades_df[trades_df['action'] == 'STOP_LOSS']) if len(trades_df) > 0 else 0
    total_cost = float(trades_df['cost'].sum()) if len(trades_df) > 0 else 0

    # ── Self-check: warn if result looks suspicious ──
    _warnings = []
    if ann_ret < 0 and total_ret > -0.3:
        _warnings.append(f"⚠️  Negative annual return ({ann_ret:.1%}) — check score/volume data")
    if max_dd > 0.50:
        _warnings.append(f"⚠️  Extreme max drawdown ({max_dd:.1%}) — possible data/configuration issue")
    if sl_count == 0 and stop_loss > 0:
        _warnings.append(f"⚠️  Zero stop-loss triggers despite sl={stop_loss} — check price data quality")
    if _warnings:
        for _w in _warnings:
            print(f"  {_w}")

    metrics = {
        'label': label,
        'total_return': round(float(total_ret), 6),
        'annual_return': round(float(ann_ret), 6),
        'annual_volatility': round(float(ann_vol), 6),
        'sharpe_ratio': round(float(sharpe), 4),
        'max_drawdown': round(float(max_dd), 6),
        'calmar_ratio': round(float(calmar), 4),
        'sortino_ratio': round(float(sortino), 4),
        'win_rate': round(float(win_rate), 6),
        'total_trades': len(trades_df),
        'stop_loss_trades': int(sl_count),
        'total_cost': round(total_cost, 2),
        'rebalance_count': rebal_count,
        'final_value': round(float(nav.iloc[-1]), 2),
        'params': {
            'top_n': top_n,
            'rebalance_freq': rebalance_freq,
            'stop_loss': stop_loss,
            'max_position': max_position,
            'use_vol_scaling': use_vol_scaling,
        }
    }

    return metrics, nav, trades_df


# ============================================================
# 参数扫描
# ============================================================
def param_scan(close_panel, score, param_grid=None):
    """参数网格扫描。"""
    if param_grid is None:
        param_grid = {
            'top_n': [10, 15, 20, 30],
            'rebalance_freq': [5, 10, 20],
            'stop_loss': [0.10, 0.15, 0.20],
        }

    keys = list(param_grid.keys())
    values = list(param_grid.values())
    total = 1
    for v in values:
        total *= len(v)

    print(f"\n参数扫描: {total} 组参数...")
    results = []
    for count, combo in enumerate(product(*values), 1):
        params = dict(zip(keys, combo))
        metrics, _, _ = run_backtest(close_panel, score, **params,
                                      label=f"scan_{count}")
        metrics['params'] = {**params}
        results.append(metrics)

        if count % 10 == 0 or count == total:
            print(f"  [{count}/{total}] 已完成")

    # 按夏普排序
    results.sort(key=lambda x: x['sharpe_ratio'], reverse=True)
    return results


# ============================================================
# Walk-Forward 分析
# ============================================================
def walk_forward(close_panel, train_days=252, test_days=63,
                 step_days=63, top_n=12, rebalance_freq=20,
                 stop_loss=0.20, **kwargs):
    """Walk-Forward 过拟合检测。

    将时间轴上滑动窗口：训练期(约1年) → 测试期(约1季度)
    每一轮回测独立进行，最终拼接样本外净值曲线。
    """
    dates = close_panel.index
    n = len(dates)
    fold_results = []
    fold_navs = []
    fold = 0

    train_end = train_days
    while train_end + test_days <= n:
        fold += 1
        train_start = max(0, train_end - train_days)
        test_start = train_end
        test_end = min(n, train_end + test_days)

        # 重新计算该窗口内的因子和评分
        sub_close = close_panel.loc[dates[train_start:test_end]]
        from core.factors import calc_factors_panel
        from core.config import config as cfg
        sub_factors = calc_factors_panel(sub_close)
        sub_score = composite_score(sub_factors)

        # 截取测试期评分
        test_dates = dates[test_start:test_end]
        sub_score_test = sub_score.loc[test_dates]
        sub_close_test = sub_close.loc[test_dates]

        m, nav, _ = run_backtest(
            sub_close_test, sub_score_test,
            top_n=top_n, rebalance_freq=rebalance_freq,
            stop_loss=stop_loss, label=f'wf_fold{fold}',
        )

        fold_results.append({
            'fold': fold,
            'train': f"{dates[train_start].date()}~{dates[test_start-1].date()}",
            'test': f"{dates[test_start].date()}~{dates[test_end-1].date()}",
            'ann_return': m['annual_return'],
            'sharpe': m['sharpe_ratio'],
            'max_dd': m['max_drawdown'],
            'sortino': m['sortino_ratio'],
            'trades': m['total_trades'],
        })
        fold_navs.append(nav)

        print(f"  WF Fold {fold}: {fold_results[-1]['test']} | "
              f"Ret={m['annual_return']:.1%} Sharpe={m['sharpe_ratio']:.2f} "
              f"DD={m['max_dd']:.1%}")

        train_end += step_days

    # 拼接样本外净值
    if fold_navs:
        # 归一化每个 fold 的起始净值为1，然后连乘
        combined_nav = fold_navs[0] / fold_navs[0].iloc[0]
        for fnav in fold_navs[1:]:
            combined_nav = combined_nav * (fnav / fnav.iloc[0])
    else:
        combined_nav = None

    return fold_results, combined_nav


# ============================================================
# 结果保存
# ============================================================
def save_results(output_dir, metrics_list, nav_dict, trades_dict,
                 scan_results=None, wf_results=None):
    """保存回测结果到目录。"""
    os.makedirs(output_dir, exist_ok=True)

    # 汇总
    summary = {m['label']: {k: v for k, v in m.items() if k not in ['nav', 'trades']}
               for m in metrics_list}
    with open(os.path.join(output_dir, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # 对比 CSV
    rows = []
    for m in metrics_list:
        rows.append({
            'strategy': m['label'],
            'annual_return': f"{m['annual_return']:.2%}",
            'sharpe': m['sharpe_ratio'],
            'max_dd': f"{m['max_drawdown']:.2%}",
            'calmar': m['calmar_ratio'],
            'sortino': m['sortino_ratio'],
            'win_rate': f"{m['win_rate']:.2%}",
            'trades': m['total_trades'],
            'stop_loss': m['stop_loss_trades'],
            'cost': m['total_cost'],
            'final_value': m['final_value'],
        })
    pd.DataFrame(rows).to_csv(os.path.join(output_dir, "comparison.csv"), index=False)

    # 各策略净值 + 交易记录
    for label, nav in nav_dict.items():
        nav.to_csv(os.path.join(output_dir, f"nav_{label}.csv"))
        # 月度收益表
        monthly = nav.resample('ME').last().pct_change().dropna()
        monthly_df = pd.DataFrame({
            'month': monthly.index.strftime('%Y-%m'),
            'monthly_return': monthly.values.round(6),
            'cumulative_return': (nav.iloc[-1] / nav.iloc[0] - 1) if len(nav) > 0 else 0,
        })
        # 月度收益透视表（按年×月）
        monthly_returns = nav.pct_change().resample('ME').sum()
        years = sorted(set(monthly_returns.index.year))
        months = list(range(1, 13))
        pivot_data = {}
        for y in years:
            pivot_data[y] = []
            for m in months:
                mask = (monthly_returns.index.year == y) & (monthly_returns.index.month == m)
                val = monthly_returns[mask].values[0] if mask.any() else np.nan
                pivot_data[y].append(round(float(val), 4) if not np.isnan(val) else '-')
        pivot_df = pd.DataFrame(pivot_data, index=[f'{m}月' for m in months])
        pivot_df.to_csv(os.path.join(output_dir, f"monthly_returns_{label}.csv"))

    for label, trades in trades_dict.items():
        if len(trades) > 0:
            trades.to_csv(os.path.join(output_dir, f"trades_{label}.csv"), index=False)

    # 参数扫描结果
    if scan_results:
        scan_summary = []
        for r in scan_results:
            scan_summary.append({
                'rank': len(scan_summary) + 1,
                'top_n': r['params'].get('top_n', '-'),
                'rebalance_freq': r['params'].get('rebalance_freq', '-'),
                'stop_loss': r['params'].get('stop_loss', '-'),
                'sharpe': r['sharpe_ratio'],
                'annual_return': f"{r['annual_return']:.2%}",
                'max_dd': f"{r['max_drawdown']:.2%}",
                'calmar': r['calmar_ratio'],
            })
        with open(os.path.join(output_dir, "param_scan.json"), "w") as f:
            json.dump(scan_summary[:20], f, indent=2, ensure_ascii=False)
        pd.DataFrame(scan_summary[:20]).to_csv(
            os.path.join(output_dir, "param_scan.csv"), index=False)

    # Walk-Forward 结果
    if wf_results:
        wf_df = pd.DataFrame(wf_results)
        wf_df.to_csv(os.path.join(output_dir, "walk_forward.csv"), index=False)
        with open(os.path.join(output_dir, "walk_forward.json"), "w") as f:
            json.dump(wf_results, f, indent=2, ensure_ascii=False)

    # Markdown 报告
    report_md = generate_report(metrics_list, scan_results, wf_results)
    with open(os.path.join(output_dir, "report.md"), "w") as f:
        f.write(report_md)

    return output_dir


def generate_report(metrics_list, scan_results=None, wf_results=None):
    """生成 Markdown 回测报告。"""
    lines = ["# 回测报告\n"]
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 策略对比表
    lines.append("## 策略对比\n")
    lines.append("| 策略 | 年化收益 | 夏普 | Sortino | 最大回撤 | Calmar | 胜率 | 交易次数 | 止损次数 |")
    lines.append("|------|---------|------|---------|---------|--------|------|---------|---------|")
    for m in metrics_list:
        lines.append(
            f"| {m['label']} | {m['annual_return']:.2%} | {m['sharpe_ratio']:.2f} | "
            f"{m['sortino_ratio']:.2f} | "
            f"{m['max_drawdown']:.2%} | {m['calmar_ratio']:.2f} | "
            f"{m['win_rate']:.2%} | {m['total_trades']} | {m['stop_loss_trades']} |"
        )

    lines.append("")
    if scan_results:
        lines.append("## 参数扫描 Top 10\n")
        lines.append("| 排名 | top_n | 调仓频率 | 止损 | 夏普 | 年化收益 | 最大回撤 |")
        lines.append("|------|-------|---------|------|------|---------|---------|")
        for r in scan_results[:10]:
            lines.append(
                f"| {r.get('rank', '-')} | {r.get('top_n', '-')} | "
                f"{r.get('rebalance_freq', '-')} | {r.get('stop_loss', '-')} | "
                f"{r.get('sharpe', '-')} | {r.get('annual_return', '-')} | "
                f"{r.get('max_dd', '-')} |"
            )

    if wf_results:
        lines.append("\n## Walk-Forward 分析\n")
        lines.append("| Fold | 训练期 | 测试期 | 年化收益 | 夏普 | Sortino | 最大回撤 | 交易次数 |")
        lines.append("|------|--------|--------|---------|------|---------|---------|---------|")
        for r in wf_results:
            lines.append(
                f"| {r['fold']} | {r['train']} | {r['test']} | "
                f"{r['ann_return']:.1%} | {r['sharpe']:.2f} | {r['sortino']:.2f} | "
                f"{r['max_dd']:.1%} | {r['trades']} |"
            )
        avg_sharpe = np.mean([r['sharpe'] for r in wf_results])
        avg_ret = np.mean([r['ann_return'] for r in wf_results])
        pos = sum(1 for r in wf_results if r['ann_return'] > 0)
        lines.append(f"\n**汇总**: {len(wf_results)} folds | 平均年化 {avg_ret:.1%} | "
                      f"平均夏普 {avg_sharpe:.2f} | 正收益 {pos}/{len(wf_results)}")

    return "\n".join(lines) + "\n"


def _load_stock_names() -> dict:
    """Load stock name mapping from HS300 constituents CSV.

    Returns {code: name} dict, or empty dict if file not found.
    When empty, industry classification is silently skipped.
    """
    import pandas as pd
    hs300_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "hs300_constituents.csv"
    )
    if not os.path.exists(hs300_path):
        hs300_path = "/root/hs300_constituents.csv"
    try:
        hs300 = pd.read_csv(hs300_path)
        return dict(zip(
            hs300['品种代码'].astype(str).str.zfill(6),
            hs300['品种名称']
        ))
    except Exception as e:
        print(f"  ⚠️  Could not load stock names: {e}")
        return {}


# ============================================================
# 主流程
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="A股量化回测系统")
    parser.add_argument("--strategy", nargs="+", default=["all"],
                        help="策略列表: v3_baseline | v3_optimized | ic_ir_weighted | "
                             "ic_selected | markowitz | all")
    parser.add_argument("--start", default=None, help="回测起始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="回测结束日期 (YYYY-MM-DD)")
    parser.add_argument("--top-n", type=int, default=None, help="持仓数量")
    parser.add_argument("--rebalance-freq", type=int, default=None, help="调仓频率(日)")
    parser.add_argument("--stop-loss", type=float, default=None, help="止损比例 (如 0.15)")
    parser.add_argument("--max-position", type=float, default=0.10, help="单只最大仓位")
    parser.add_argument("--scan", action="store_true", help="启用参数网格扫描")
    parser.add_argument("--walk-forward", action="store_true", help="启用 Walk-Forward 过拟合检测")
    parser.add_argument("--output-dir", default=None, help="结果输出目录")
    parser.add_argument("--report-markdown", action="store_true",
                        help="输出 Markdown 报告到 stdout")
    parser.add_argument("--ic-analysis", action="store_true",
                        help="运行 IC 因子分析")
    parser.add_argument("--log", action="store_true",
                        help="自动追加结果到 docs/RESULTS_LOG.md")
    args = parser.parse_args()

    # Load stock names for industry classification
    stock_names = _load_stock_names()

    print("=" * 60)
    print("A股量化回测系统  |  core/engine: factors+scoring+account")
    print("=" * 60)
    t0 = time.time()

    # 1. 加载数据
    print(f"\n[1/5] 加载数据...")
    (close_panel, volume_panel, amount_panel), codes = load_and_build_panel(
        args.start, args.end)
    print(f"  Panel: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只股票")
    print(f"  区间: {close_panel.index[0].date()} ~ {close_panel.index[-1].date()}")

    # 2. 因子计算（委托 core.factors.calc_factors_panel）
    print(f"\n[2/5] 计算因子...")
    factors = calc_factors_panel(close_panel, volume_panel, amount_panel)
    print(f"  共 {len(factors)} 个因子")

    # 3. IC 分析（可选）
    ic_results = None
    if args.ic_analysis:
        print(f"\n[3/5] IC 分析...")
        ic_results = run_ic_analysis(factors, close_panel)

    # 4. 构建评分（委托 core.scoring）
    print(f"\n[3/5] 构建评分矩阵...")
    score_equal = composite_score_equal(factors)
    if ic_results:
        # IC 分析输出
        selected, discarded = select_factors_ic(ic_results)
        print(f"  有效因子: {len(selected)} / {len(factors)}")
        if discarded:
            print(f"  淘汰因子: {discarded}")
        ic_table = pd.DataFrame({
            name: {k: round(v, 4) for k, v in stats.items()}
            for name, stats in sorted(ic_results.items(), key=lambda x: abs(x[1]['ic_ir']), reverse=True)
        }).T
        print(f"\n  IC 统计（按 |IC_IR| 降序）:\n{ic_table.to_string()}")

        selected_weights = {name: abs(stats['ic_ir'])
                            for name, stats in ic_results.items()
                            if abs(stats['ic_ir']) >= 0.03 and stats['ic_positive_rate'] >= 0.50}
        total_w = sum(selected_weights.values())
        if total_w > 0:
            selected_weights = {k: v / total_w for k, v in selected_weights.items()}
        score_ic = composite_score(factors, selected_weights)
    else:
        score_ic = None

    # 4b. 因子相关性分析（检测冗余因子）
    if ic_results:
        from core.scoring import factor_correlation
        corr_matrix, redundant = factor_correlation(factors)
        if redundant:
            print(f"\n  ⚠️ 高相关因子对 (|ρ| > 0.8): {len(redundant)} 对")
            for fa, fb, c in redundant[:10]:
                print(f"    {fa} ↔ {fb}: {c:+.4f}")
            print("  → 建议：考虑删除其中一个或合并")
        else:
            print(f"\n  ✅ 因子间无严重冗余 (所有 |ρ| ≤ 0.8)")

    # 5. 回测（委托 core.account）
    print(f"\n[4/5] 运行回测...")
    strategies = args.strategy
    run_all = "all" in strategies

    # 从 core.config 加载预定义策略 profiles
    from core.config import STRATEGY_PROFILES

    metrics_list = []
    nav_dict = {}
    trades_dict = {}

    # 解析命令行通用参数覆盖
    top_n = args.top_n
    rebal_freq = args.rebalance_freq
    sl = args.stop_loss

    score_equal = composite_score(factors)  # FACTOR_WEIGHTS 加权

    configs = []

    def _build_cfg(profile, score, extra_kwargs=None):
        """从 StrategyProfile 构建运行 config，支持命令行参数覆盖"""
        kw = dict(
            top_n=top_n or profile.top_n,
            rebalance_freq=rebal_freq or profile.rebalance_freq,
            stop_loss=sl or profile.stop_loss,
            max_industry_weight=profile.max_industry_weight,
            max_daily_turnover=profile.max_daily_turnover,
            weight_method=profile.weight_method,
            stock_names=stock_names,
            use_take_profit=profile.use_take_profit,
            tp_tiers=profile.tp_tiers,
            use_holding_decay=profile.use_holding_decay,
        )
        if extra_kwargs:
            kw.update(extra_kwargs)
        return {'label': profile.label, 'score': score, 'kwargs': kw}

    if run_all or "v3_baseline" in strategies:
        configs.append(_build_cfg(STRATEGY_PROFILES["v4_baseline"], score_equal,
                                  extra_kwargs=dict(top_n=top_n or 20, rebalance_freq=rebal_freq or 5,
                                                     stop_loss=sl or 0.15, max_industry_weight=0.25,
                                                     max_daily_turnover=0.30)))
    if run_all or "v3_optimized" in strategies:
        configs.append(_build_cfg(STRATEGY_PROFILES["v4_baseline"], score_equal))
    if run_all or "v4_industry_cap" in strategies:
        configs.append(_build_cfg(STRATEGY_PROFILES["v4_industry_cap"], score_equal))
    if run_all or "v5_tp_decay" in strategies:
        configs.append(_build_cfg(STRATEGY_PROFILES["v5_tp_decay"], score_equal))
    if (run_all or "ic_ir_weighted" in strategies) and ic_results:
        configs.append(_build_cfg(STRATEGY_PROFILES["v4_baseline"], score_equal,
                                  extra_kwargs=dict(top_n=top_n or 10)))
    if (run_all or "ic_selected" in strategies) and score_ic is not None:
        configs.append(_build_cfg(STRATEGY_PROFILES["v4_baseline"], score_ic,
                                  extra_kwargs=dict(top_n=top_n or 10)))
    if (run_all or "markowitz" in strategies):
        configs.append(_build_cfg(STRATEGY_PROFILES["v4_baseline"], score_equal,
                                  extra_kwargs=dict(weight_method='markowitz')))

    # 如果没匹配到任何已知策略，默认跑 v4_baseline
    if not configs:
        configs.append(_build_cfg(STRATEGY_PROFILES["v4_baseline"], score_equal))

    # IC 策略降级处理
    if not ic_results and not any(c['label'] in ('v3_baseline', 'v4_baseline', 'v4_industry_cap', 'v5_tp_decay') for c in configs):
        print("  ⚠️ 未启用 --ic-analysis，IC 相关策略不可用，用 v4_baseline 替代")
        configs = [_build_cfg(STRATEGY_PROFILES["v4_baseline"], score_equal)]

    for cfg in configs:
        print(f"\n  ▶ {cfg['label']}: top_n={cfg['kwargs'].get('top_n')}, "
              f"freq={cfg['kwargs'].get('rebalance_freq')}, "
              f"sl={cfg['kwargs'].get('stop_loss')}")
        metrics, nav, trades = run_backtest(
            close_panel, cfg['score'], label=cfg['label'], **cfg['kwargs'])
        metrics_list.append(metrics)
        nav_dict[cfg['label']] = nav
        trades_dict[cfg['label']] = trades

        print(f"    Return={metrics['annual_return']:.2%}, "
              f"Sharpe={metrics['sharpe_ratio']:.2f}, "
              f"MaxDD={metrics['max_drawdown']:.2%}")

    # 6. 参数扫描（可选）
    scan_results = None
    if args.scan:
        print(f"\n[5/5] 参数扫描...")
        scan_results = param_scan(close_panel, score_equal)

    # 6b. Walk-Forward 分析（可选）
    wf_results = None
    if args.walk_forward:
        print(f"\n[5/5] Walk-Forward 分析...")
        top_n = args.top_n or 12
        rebal_freq = args.rebalance_freq or 20
        sl = args.stop_loss or 0.20
        wf_results, wf_nav = walk_forward(
            close_panel,
            top_n=top_n, rebalance_freq=rebal_freq, stop_loss=sl,
        )
        if wf_results:
            print(f"\n  Walk-Forward 汇总 ({len(wf_results)} folds):")
            avg_sharpe = np.mean([r['sharpe'] for r in wf_results])
            avg_ret = np.mean([r['ann_return'] for r in wf_results])
            print(f"    平均年化: {avg_ret:.1%} | 平均夏普: {avg_sharpe:.2f}")
            positive_folds = sum(1 for r in wf_results if r['ann_return'] > 0)
            print(f"    正收益fold: {positive_folds}/{len(wf_results)}")

    # 7. 输出
    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"回测完成 ({elapsed:.1f}s)")
    print(f"{'=' * 60}")

    # 打印对比表
    print(f"\n{'策略对比汇总':^60}")
    print(f"{'─' * 60}")
    print(f"{'策略':<20} {'年化收益':>10} {'夏普':>7} {'Sortino':>8} {'最大回撤':>10} {'Calmar':>7} {'交易':>5}")
    print(f"{'─' * 60}")
    for m in metrics_list:
        print(f"{m['label']:<20} {m['annual_return']:>9.2%} "
              f"{m['sharpe_ratio']:>7.2f} {m['sortino_ratio']:>7.2f} "
              f"{m['max_drawdown']:>9.2%} "
              f"{m['calmar_ratio']:>7.2f} {m['total_trades']:>5}")

    # 保存
    if args.output_dir:
        out_dir = args.output_dir
    else:
        out_dir = os.path.join(REPORT_DIR,
                               datetime.now().strftime("%Y%m%d_%H%M%S"))

    saved = save_results(out_dir, metrics_list, nav_dict, trades_dict, scan_results, wf_results)

    # 保存 config 副本供复现
    try:
        import shutil
        shutil.copy2(params.get("_config_path", "config.yaml"),
                     os.path.join(os.path.dirname(out_dir) if os.path.dirname(out_dir) else ".", "config.yaml"))
    except Exception:
        pass

    print(f"\n结果已保存: {saved}/")

    # 自动记录到 RESULTS_LOG
    if getattr(args, 'log', False):
        try:
            parts = [f"top{args.top_n or 12}", f"rf{args.rebalance_freq or 20}",
                     f"sl{args.stop_loss or 0.20}"]
            if args.max_industry_weight and args.max_industry_weight > 0:
                parts.append(f"ind{int(args.max_industry_weight*100)}%")
            params_str = ",".join(parts)
            from scripts.log_backtest_result import append_row
            for m in metrics_list:
                append_row(
                    label=m['label'],
                    params=params_str,
                    metrics={
                        'return': m.get('annual_return', 0),
                        'sharpe': m.get('sharpe_ratio', 0),
                        'maxdd': m.get('max_drawdown', 0),
                        'calmar': m.get('calmar_ratio', 0),
                        'trades': m.get('total_trades', 0),
                    },
                    notes="auto-logged by --log"
                )
        except Exception as e:
            print(f"  ⚠️ RESULTS_LOG 记录失败: {e}")

    if args.report_markdown:
        report = generate_report(metrics_list, scan_results)
        print(f"\n\n{report}")

    return metrics_list


if __name__ == "__main__":
    main()
