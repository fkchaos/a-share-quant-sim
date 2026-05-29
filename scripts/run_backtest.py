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
    v3_baseline     – 原始 v3 策略（等权31因子, top_n=20, rebal=5, stop=15%）
    v3_optimized    – v3 风控优化参数（top_n=20, rebal=5, stop=15%, vol_scaling）
    ic_ir_weighted  – IC-IR 因子加权（用 IC 信息比率给因子赋权）
    ic_selected     – IC-IR 加权 + 仅保留有效因子（|IC_IR|>=0.03）
    markowitz       – 等权因子 + Markowitz 组合权重优化

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

# ============================================================
# 配置
# ============================================================
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")

# Legacy config loading (kept for backward compat)
def _load_config(config_path=None):
    """Load config.yaml and return dict. Falls back to empty dict if unavailable."""
    if config_path is None:
        candidates = [
            "config.yaml",
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"),
            os.path.expanduser("~/.a-share-backtest/config.yaml"),
        ]
        for p in candidates:
            if os.path.exists(p):
                config_path = p
                break
    if config_path is None:
        return {}
    try:
        import yaml
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}

def _merge_args_into_config(args, config):
    """Build final param dict: config.yaml < CLI args."""
    cfg_bt = config.get("backtest", {})
    cfg_costs = config.get("costs", {})
    return {
        "start_date": args.start or cfg_bt.get("start_date", "2021-01-01"),
        "end_date": args.end or cfg_bt.get("end_date", datetime.now().strftime("%Y-%m-%d")),
        "initial_capital": cfg_costs.get("initial_capital", 1_000_000),
        "commission_rate": cfg_costs.get("commission_rate", 0.0003),
        "stamp_tax_rate": cfg_costs.get("stamp_tax_rate", 0.001),
        "slippage_rate": cfg_costs.get("slippage_rate", 0.001),
    }

# Module-level constants (still used as fallbacks; override via config.yaml or CLI)
START_DATE = "2021-01-01"
END_DATE = datetime.now().strftime("%Y-%m-%d")
INITIAL_CAPITAL = 1_000_000
COMMISSION_RATE = 0.0003
STAMP_TAX_RATE = 0.001
SLIPPAGE_RATE = 0.001

# 因子权重（与 sim_daily.py 保持一致；config.yaml 中的值会在加载时覆盖）
FACTOR_WEIGHTS = {
    'mom_5': 0.05, 'mom_10': 0.10, 'mom_20': 0.10, 'mom_60': 0.08, 'mom_120': 0.05,
    'rev_3': 0.05, 'rev_5': 0.08, 'rev_10': 0.05,
    'vol_10': -0.03, 'vol_20': -0.05, 'vol_60': -0.05,
    'vol_change': 0.03,
    'vol_ratio_5': 0.05, 'vol_ratio_20': 0.05, 'amount_ratio': 0.05,
    'rsi_6': 0.03, 'rsi_14': 0.05, 'rsi_28': 0.02,
    'macd_12_26': 0.08, 'macd_5_35': 0.04,
    'boll_pos_10': 0.03, 'boll_pos_20': 0.03, 'boll_width_20': -0.02,
    'atr_14': -0.03,
    'skew_20': 0.02, 'kurt_20': -0.02,
    'vwap_mom': 0.03,
    'rel_strength_20': 0.05, 'rel_strength_60': 0.03,
}


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
# 因子计算
# ============================================================
def calc_factors(close_panel, volume_panel, amount_panel):
    """计算全部技术因子。"""
    factors = {}
    returns = close_panel.pct_change()

    for w in [5, 10, 20, 60, 120]:
        factors[f'mom_{w}'] = close_panel.pct_change(w)

    for w in [3, 5, 10]:
        factors[f'rev_{w}'] = -close_panel.pct_change(w)

    for w in [10, 20, 60]:
        factors[f'vol_{w}'] = returns.rolling(w).std()
    factors['vol_change'] = returns.rolling(20).std() / returns.rolling(60).std()

    factors['vol_ratio_5'] = volume_panel / volume_panel.rolling(5).mean()
    factors['vol_ratio_20'] = volume_panel / volume_panel.rolling(20).mean()
    factors['amount_ratio'] = amount_panel / amount_panel.rolling(20).mean()

    for w in [6, 14, 28]:
        delta = close_panel.diff()
        gain = delta.where(delta > 0, 0).rolling(w).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(w).mean()
        rs = gain / loss.replace(0, np.nan)
        factors[f'rsi_{w}'] = 100 - (100 / (1 + rs))

    for fast, slow in [(12, 26), (5, 35)]:
        ema_f = close_panel.ewm(span=fast).mean()
        ema_s = close_panel.ewm(span=slow).mean()
        macd_line = ema_f - ema_s
        factors[f'macd_{fast}_{slow}'] = macd_line - macd_line.ewm(span=9).mean()

    for w in [10, 20]:
        ma = close_panel.rolling(w).mean()
        std = close_panel.rolling(w).std()
        lower = ma - 2 * std
        upper = ma + 2 * std
        factors[f'boll_pos_{w}'] = (close_panel - lower) / (upper - lower + 1e-10)
        factors[f'boll_width_{w}'] = (upper - lower) / (ma + 1e-10)

    high_low = close_panel.rolling(2).max() - close_panel.rolling(2).min()
    factors['atr_14'] = high_low.rolling(14).mean() / (close_panel + 1e-10)

    factors['skew_20'] = returns.rolling(20).skew()
    factors['kurt_20'] = returns.rolling(20).kurt()

    vol_price = close_panel * volume_panel
    factors['vwap_mom'] = (vol_price.rolling(20).mean() /
                           (volume_panel.rolling(20).mean() + 1e-10))

    cross_mean_ret_20 = close_panel.mean(axis=1).pct_change(20)
    factors['rel_strength_20'] = close_panel.pct_change(20).sub(cross_mean_ret_20, axis=0)

    cross_mean_ret_60 = close_panel.mean(axis=1).pct_change(60)
    factors['rel_strength_60'] = close_panel.pct_change(60).sub(cross_mean_ret_60, axis=0)

    return factors


def standardize(df):
    """截面 Z-Score 标准化。"""
    mean = df.mean(axis=1)
    std = df.std(axis=1).replace(0, np.nan)
    return df.sub(mean, axis=0).div(std, axis=0)


def composite_score_weighted(factors, weights=None):
    """加权因子合成（默认使用 FACTOR_WEIGHTS）。"""
    if weights is None:
        weights = FACTOR_WEIGHTS
    score = pd.DataFrame(0, index=factors[list(factors.keys())[0]].index,
                         columns=factors[list(factors.keys())[0]].columns)
    for name, w in weights.items():
        if name in factors:
            score = score.add(w * standardize(factors[name]), fill_value=0)
    return score


def composite_score_equal(factors):
    """等权因子合成（v3 baseline）。"""
    n = len(factors)
    weights = {name: 1.0 / n for name in factors}
    return composite_score_weighted(factors, weights)


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
def run_backtest(close_panel, score, top_n=20, rebalance_freq=5, stop_loss=0.15,
                 max_position=0.10, use_vol_scaling=False, vol_target=0.20,
                 weight_method='equal', label='default',
                 initial_capital=None,
                 max_industry_weight=0.25, max_daily_turnover=0.30,
                 stock_names=None):
    """
    完整回测引擎。

    交易逻辑全部委托给 core.account（buy/sell/check_stop_loss），
    与 sim_daily.py 使用同一套代码，保证一致性。

    weight_method: 'equal' | 'markowitz'
    """
    from core.account import PortfolioState, buy, sell, check_stop_loss, portfolio_value
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

    for i, date in enumerate(dates):
        if i < 120:
            nav_list.append(icap)
            continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else icap)
            continue

        price_data = close_panel.loc[date]

        # ── 1. 止损（委托 core.account.check_stop_loss）──
        state = check_stop_loss(state, date, price_data)

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
                        weights = {c: 1.0 / len(top_stocks) for c in top_stocks}
                else:
                    weights = {c: 1.0 / len(top_stocks) for c in top_stocks}

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

    trades_df = pd.DataFrame(state.trade_log)
    sl_count = len(trades_df[trades_df['action'] == 'STOP_LOSS']) if len(trades_df) > 0 else 0
    total_cost = float(trades_df['cost'].sum()) if len(trades_df) > 0 else 0

    metrics = {
        'label': label,
        'total_return': round(float(total_ret), 6),
        'annual_return': round(float(ann_ret), 6),
        'annual_volatility': round(float(ann_vol), 6),
        'sharpe_ratio': round(float(sharpe), 4),
        'max_drawdown': round(float(max_dd), 6),
        'calmar_ratio': round(float(calmar), 4),
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
# 结果保存
# ============================================================
def save_results(output_dir, metrics_list, nav_dict, trades_dict, scan_results=None):
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

    # Markdown 报告
    report_md = generate_report(metrics_list, scan_results)
    with open(os.path.join(output_dir, "report.md"), "w") as f:
        f.write(report_md)

    return output_dir


def generate_report(metrics_list, scan_results=None):
    """生成 Markdown 回测报告。"""
    lines = ["# 回测报告\n"]
    lines.append(f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # 策略对比表
    lines.append("## 策略对比\n")
    lines.append("| 策略 | 年化收益 | 夏普 | 最大回撤 | Calmar | 胜率 | 交易次数 | 止损次数 |")
    lines.append("|------|---------|------|---------|--------|------|---------|---------|")
    for m in metrics_list:
        lines.append(
            f"| {m['label']} | {m['annual_return']:.2%} | {m['sharpe_ratio']:.2f} | "
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

    return "\n".join(lines) + "\n"


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
    parser.add_argument("--output-dir", default=None, help="结果输出目录")
    parser.add_argument("--config", default=None, help="config.yaml 路径")
    parser.add_argument("--report-markdown", action="store_true",
                        help="输出 Markdown 报告到 stdout")
    parser.add_argument("--ic-analysis", action="store_true",
                        help="运行 IC 因子分析")
    args = parser.parse_args()

    # Load config and merge with CLI args
    config = _load_config(args.config)
    params = _merge_args_into_config(args, config)

    # Load stock names for industry classification
    stock_names = {}
    hs300_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "hs300_constituents.csv")
    if not os.path.exists(hs300_path):
        hs300_path = "/root/hs300_constituents.csv"
    try:
        hs300 = pd.read_csv(hs300_path)
        stock_names = dict(zip(
            hs300['品种代码'].astype(str).str.zfill(6),
            hs300['品种名称']
        ))
    except Exception:
        pass
    print(f"  使用配置: {len(config)} 个字段已加载")

    print("=" * 60)
    print("A股量化回测系统")
    print("=" * 60)
    t0 = time.time()

    # 1. 加载数据
    print(f"\n[1/5] 加载数据...")
    (close_panel, volume_panel, amount_panel), codes = load_and_build_panel(
        args.start, args.end)
    print(f"  Panel: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只股票")
    print(f"  区间: {close_panel.index[0].date()} ~ {close_panel.index[-1].date()}")

    # 2. 因子计算
    print(f"\n[2/5] 计算因子...")
    factors = calc_factors(close_panel, volume_panel, amount_panel)
    print(f"  共 {len(factors)} 个因子")

    # 3. IC 分析（可选）
    ic_results = None
    if args.ic_analysis:
        print(f"\n[3/5] IC 分析...")
        ic_results = run_ic_analysis(factors, close_panel)
        selected, discarded = select_factors_ic(ic_results)
        print(f"  有效因子: {len(selected)} / {len(factors)}")
        if discarded:
            print(f"  淘汰因子: {discarded}")

        # 打印 IC 表格
        ic_table = pd.DataFrame({
            name: {k: round(v, 4) for k, v in stats.items()}
            for name, stats in sorted(ic_results.items(), key=lambda x: abs(x[1]['ic_ir']), reverse=True)
        }).T
        print(f"\n  IC 统计（按 |IC_IR| 降序）:\n{ic_table.to_string()}")

    # 4. 构建评分
    print(f"\n[3/5] 构建评分矩阵...")
    score_equal = composite_score_equal(factors)
    if ic_results:
        selected_weights = {name: abs(stats['ic_ir'])
                            for name, stats in ic_results.items()
                            if abs(stats['ic_ir']) >= 0.03 and stats['ic_positive_rate'] >= 0.50}
        total_w = sum(selected_weights.values())
        if total_w > 0:
            selected_weights = {k: v / total_w for k, v in selected_weights.items()}
        score_ic = composite_score_weighted(factors, selected_weights)
    else:
        score_ic = None

    # 5. 回测
    print(f"\n[4/5] 运行回测...")
    strategies = args.strategy
    run_all = "all" in strategies

    metrics_list = []
    nav_dict = {}
    trades_dict = {}

    # 解析通用参数覆盖
    top_n = args.top_n
    rebal_freq = args.rebalance_freq
    sl = args.stop_loss

    configs = []

    if run_all or "v3_baseline" in strategies:
        configs.append({
            'label': 'v3_baseline',
            'score': composite_score_weighted(factors),  # 使用 FACTOR_WEIGHTS 加权
            'kwargs': dict(top_n=top_n or 20, rebalance_freq=rebal_freq or 5,
                           stop_loss=sl or 0.15,
                           max_industry_weight=0.25, max_daily_turnover=0.30,
                           stock_names=stock_names),
        })
    if run_all or "v3_optimized" in strategies:
        configs.append({
            'label': 'v3_optimized',
            'score': composite_score_weighted(factors),  # 使用 FACTOR_WEIGHTS 加权
            'kwargs': dict(top_n=top_n or 20, rebalance_freq=rebal_freq or 5,
                           stop_loss=sl or 0.15, use_vol_scaling=True,
                           max_industry_weight=0.25, max_daily_turnover=0.30,
                           stock_names=stock_names),
        })
    if (run_all or "ic_ir_weighted" in strategies) and ic_results:
        configs.append({
            'label': 'ic_ir_all',
            'score': composite_score_weighted(
                factors,
                {name: abs(s['ic_ir']) / sum(abs(st['ic_ir']) for st in ic_results.values())
                 for name, s in ic_results.items()}),
            'kwargs': dict(top_n=top_n or 10, rebalance_freq=rebal_freq or 20,
                           stop_loss=sl or 0.20),
        })
    if (run_all or "ic_selected" in strategies) and score_ic is not None:
        configs.append({
            'label': 'ic_ir_selected',
            'score': score_ic,
            'kwargs': dict(top_n=top_n or 10, rebalance_freq=rebal_freq or 20,
                           stop_loss=sl or 0.20),
        })
    if run_all or "markowitz" in strategies:
        configs.append({
            'label': 'markowitz',
            'score': score_equal,
            'kwargs': dict(top_n=top_n or 10, rebalance_freq=rebal_freq or 20,
                           stop_loss=sl or 0.20, weight_method='markowitz',
                           max_industry_weight=0.25, max_daily_turnover=0.30,
                           stock_names=stock_names),
        })

    if not configs:
        # 没有 IC 分析结果时，ic 策略不可用，用等权替代
        print("  ⚠️ 未启用 --ic-analysis，IC 相关策略不可用，用 v3_baseline + markowitz")
        configs = [
            {'label': 'v3_baseline', 'score': composite_score_weighted(factors),
             'kwargs': dict(top_n=20, rebalance_freq=5, stop_loss=0.15,
                            max_industry_weight=0.25, max_daily_turnover=0.30,
                            stock_names=stock_names)},
            {'label': 'markowitz', 'score': score_equal,
             'kwargs': dict(top_n=10, rebalance_freq=20, stop_loss=0.20, weight_method='markowitz',
                            max_industry_weight=0.25, max_daily_turnover=0.30,
                            stock_names=stock_names)},
        ]

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

    # 7. 输出
    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"回测完成 ({elapsed:.1f}s)")
    print(f"{'=' * 60}")

    # 打印对比表
    print(f"\n{'策略对比汇总':^60}")
    print(f"{'─' * 60}")
    print(f"{'策略':<20} {'年化收益':>10} {'夏普':>7} {'最大回撤':>10} {'Calmar':>7} {'交易':>5}")
    print(f"{'─' * 60}")
    for m in metrics_list:
        print(f"{m['label']:<20} {m['annual_return']:>9.2%} "
              f"{m['sharpe_ratio']:>7.2f} {m['max_drawdown']:>9.2%} "
              f"{m['calmar_ratio']:>7.2f} {m['total_trades']:>5}")

    # 保存
    if args.output_dir:
        out_dir = args.output_dir
    else:
        out_dir = os.path.join(REPORT_DIR,
                               datetime.now().strftime("%Y%m%d_%H%M%S"))

    saved = save_results(out_dir, metrics_list, nav_dict, trades_dict, scan_results)

    # 保存 config 副本供复现
    try:
        import shutil
        shutil.copy2(params.get("_config_path", "config.yaml"),
                     os.path.join(os.path.dirname(out_dir) if os.path.dirname(out_dir) else ".", "config.yaml"))
    except Exception:
        pass

    print(f"\n结果已保存: {saved}/")

    if args.report_markdown:
        report = generate_report(metrics_list, scan_results)
        print(f"\n\n{report}")

    return metrics_list


if __name__ == "__main__":
    main()
