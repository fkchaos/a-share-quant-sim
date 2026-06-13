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
from core.config import STRATEGY_PROFILES, DEFAULT_FACTOR_WEIGHTS, MarketFilter
from core.factors import calc_factors_panel
from core.scoring import composite_score, composite_score_equal, standardize
from core.account import PortfolioState, buy, sell, check_stop_loss, portfolio_value
from core.strategy import StrategyEngine

# ============================================================
# 配置
# ============================================================
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(_BASE_DIR, "data"))
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")

# Module-level constants (override via config.yaml or CLI)
START_DATE = "2021-01-01"
END_DATE = datetime.now().strftime("%Y-%m-%d")

FACTOR_WEIGHTS = DEFAULT_FACTOR_WEIGHTS  # 权威权重，来自 core/config.py


from core.data import load_and_build_panel
from core.db import load_panel_from_db, init_db

# 数据源选择：True=从数据库读，False=从CSV读（兼容旧流程）
USE_DB = os.environ.get("USE_DB", "1") == "1"

def load_panel(start_date=None, end_date=None, need_open=False, need_hl=False, pool="zz800"):
    """统一数据加载入口，USE_DB 环境变量控制从 DB 还是 CSV 读"""
    if USE_DB:
        init_db()
        return load_panel_from_db(start_date, end_date, need_open=need_open, need_hl=need_hl, pool=pool)
    else:
        return load_and_build_panel(start_date, end_date, need_open=need_open, need_hl=need_hl)


# ============================================================
# IC 分析
# ============================================================
def calc_ic_series(factor_df, forward_returns):
    """计算因子 IC 序列。"""
    # Skip non-DataFrame inputs (e.g. constant Series from degraded factors)
    if not isinstance(factor_df, pd.DataFrame):
        return pd.Series(dtype=float)

    common_idx = factor_df.index.intersection(forward_returns.index)
    ic_values = pd.Series(index=common_idx, dtype=float)

    for date in common_idx:
        f_vals = factor_df.loc[date]
        r_vals = forward_returns.loc[date]

        # Defensive: ensure both are Series
        if not isinstance(f_vals, pd.Series) or not isinstance(r_vals, pd.Series):
            continue
        if f_vals.dropna().empty or r_vals.dropna().empty:
            continue

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
                 use_holding_decay=False,
                 exec_timing='close',
                 open_panel=None,
                 warmup_days=120,
                 use_market_filter=False,
                 market_filter_method='ma_crossover',
                 market_ma_short=20,
                 market_ma_long=60,
                 use_hmm_position=False,
                 run_kwargs=None):
    """
    完整回测引擎。

    交易逻辑全部委托给 core.account（buy/sell/check_stop_loss），
    与 sim_daily.py 使用同一套代码，保证一致性。

    weight_method: 'equal' | 'markowitz'
    exec_timing:   'close' — 用收盘价执行（默认, T日收盘后操作）
                   'open'  — 用开盘价执行（T日上午出信号、下午开盘操作）
                   区别: 卖出/买入成交价从 close → open
                         portfolio_value 仍用 close 做 mark-to-market
    """
    from core.account import PortfolioState, buy, sell, check_stop_loss, portfolio_value
    from core.account import check_take_profit, apply_holding_decay
    from core.config import TradingCosts

    icap = initial_capital or TradingCosts().initial_capital
    dates = close_panel.index

    # ── 执行时序 ──
    if exec_timing not in ('close', 'open'):
        print(f"  ⚠️  exec_timing='{exec_timing}' 不合法, 回退到 'close'")
        exec_timing = 'close'
    if exec_timing == 'open' and open_panel is None:
        print(f"  ⚠️  exec_timing='open' 但未提供 open_panel, 回退到 'close'")
        exec_timing = 'close'
    _exec_price_desc = 'close价(收盘后)' if exec_timing == 'close' else 'open价(开盘执行)'

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
              f"exec={_exec_price_desc} "
              f"industry_cap={max_industry_weight if _industry_enabled else 'OFF'} "
              f"tp={use_take_profit} decay={use_holding_decay} atr={use_atr_stop}")

    # ── 预计算 ATR 面板（用于 ATR 自适应止损）──
    # 使用 close-to-close range 近似 ATR（与 core/factors.py 一致）
    atr_panel = None
    if use_atr_stop:
        ct = close_panel.rolling(2).max() - close_panel.rolling(2).min()
        atr_norm = ct.rolling(14).mean() / (close_panel + 1e-10)
        atr_panel = atr_norm * close_panel  # absolute ATR value

    # ── HMM 仓位预计算 ──────────────────────────────────────────
    _hmm_positions = None
    if use_hmm_position:
        try:
            from core.hmm_timing import compute_hmm_positions_batch
            _hmm_positions = compute_hmm_positions_batch(close_panel, min_lookback=60)
            n_bear = (_hmm_positions < 0.5).sum() if _hmm_positions is not None else 0
            n_total = len(_hmm_positions) if _hmm_positions is not None else 0
            if label == 'default' or os.environ.get('BACKTEST_DEBUG'):
                print(f"  📊 HMM 仓位: {n_bear}/{n_total} 天低仓位(<50%), "
                      f"平均仓位={_hmm_positions.mean():.0%}")
        except Exception as _e:
            if label == 'default':
                print(f"  ⚠️ HMM 预计算失败: {_e}, 回退满仓")
            _hmm_positions = None

    for i, date in enumerate(dates):
        if i < warmup_days:
            nav_list.append(icap)
            continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else icap)
            continue

        price_data = close_panel.loc[date]

        # ── 执行价: open 模式下用开盘价做买卖成交价 ──
        _exec_price_series = None
        if exec_timing == 'open' and open_panel is not None and date in open_panel.index:
            _exec_price_series = open_panel.loc[date]

        def _exec_price(code):
            """获取某股票的执行价：open 模式用开盘价, 否则用收盘价"""
            if _exec_price_series is not None and code in _exec_price_series.index:
                ep = _exec_price_series[code]
                if not pd.isna(ep) and ep > 0:
                    return ep
            return price_data.get(code, np.nan)

        # ── 1. 止损（委托 core.account.check_stop_loss）──
        # 止损/止盈/decay 始终用 close 做盈亏判定（mark-to-market 用 close）
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
        # open 模式: 评分滞后 1 天（T 日上午用 T-1 日收盘数据算的信号，T 日开盘执行）
        # close 模式: 当日评分当日执行（T 日收盘价执行）
        sig_date = date
        if exec_timing == 'open' and i > warmup_days:
            sig_date = dates[i - 1]  # T-1 日的评分

        if (i - warmup_days) % rebalance_freq == 0 and sig_date in score.index:
            rebal_count += 1
            day_score = score.loc[sig_date].dropna()
            valid_idx = day_score.index.isin(price_data.dropna().index)
            day_score = day_score[valid_idx]

            # ── opt-6: 市场择时 filter ──────────────────────────
            # 当 MA20 < MA60（空头排列）时，跳过买入操作
            # ── opt-6: 市场择时 filter ──────────────────────────
            _market_bear = False
            if use_market_filter and i > market_ma_long:
                _method = market_filter_method
                _ma_s = market_ma_short
                _ma_l = market_ma_long
                if _method == 'ma_crossover':
                    _market_proxy = close_panel.iloc[max(0, i-_ma_l):i].mean(axis=1)
                    _ma_short_val = _market_proxy.rolling(_ma_s).mean().iloc[-1]
                    _ma_long_val = _market_proxy.rolling(_ma_l).mean().iloc[-1]
                    if not (np.isnan(_ma_short_val) or np.isnan(_ma_long_val)):
                        _market_bear = _ma_short_val < _ma_long_val
                        if _market_bear:
                            day_score = pd.Series(dtype=float)

            if use_vol_scaling:
                returns = close_panel.pct_change()
                # vol 也滞后 1 天（跟信号一致）
                vol_date = sig_date if sig_date in returns.index else date
                stock_vol = returns.rolling(20).std().loc[vol_date]
                vol_scale = vol_target / (stock_vol * np.sqrt(252))
                vol_scale = vol_scale.clip(0.1, 3.0)
                day_score = day_score * vol_scale.reindex(day_score.index).fillna(1)

            top_stocks = day_score.nlargest(top_n).index.tolist()

            # 排除科创板（688xxx/689xxx）— 股票池包含科创板但不交易
            top_stocks = [c for c in top_stocks if not (c.startswith('688') or c.startswith('689'))]

            if top_stocks:
                current_pv = portfolio_value(state, date, price_data)

                # 卖出不在目标中的（用执行价）
                for c in list(state.holdings.keys()):
                    if c not in top_stocks and c in price_data.index:
                        ep = _exec_price(c)
                        if not pd.isna(ep) and ep > 0:
                            state = sell(state, c, ep, date, reason='SELL')

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
                    _method_map = {'weighted': 'equal', 'equal': 'equal', 'vol_inverse': 'vol_inverse', 'risk_parity': 'risk_parity'}
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

                # ── HMM 仓位管理 ────────────────────────────────────
                _hmm_pos = 1.0
                if use_hmm_position and _hmm_positions is not None:
                    _hmm_pos = _hmm_positions.get(date, 1.0)
                    # HMM 趋势下跌时，不做新买入（让现有持仓自然退出）
                    if _hmm_pos < 0.5:
                        day_score = pd.Series(dtype=float)

                # 对目标持仓买入（用执行价）
                for c in top_stocks:
                    if c not in state.holdings and c in price_data.index and not pd.isna(price_data[c]):
                        ep = _exec_price(c)
                        if ep > 0:
                            w = weights.get(c, 1.0 / len(top_stocks))
                            target_val = min(current_pv * w, current_pv * max_position)
                            # HMM 仓位缩放
                            if _hmm_pos < 1.0:
                                target_val = target_val * _hmm_pos
                            from core.account import compute_buy_shares
                            adj_p = ep * (1 + TradingCosts().slippage_rate)
                            shares = int(target_val / adj_p / 100) * 100
                            if shares > 0:
                                state = buy(state, c, ep, date, shares=shares)

        # ── 记录净值 ──
        dv = portfolio_value(state, date, price_data)
        nav_list.append(dv)

    # 绩效指标
    nav = pd.Series(nav_list, index=dates[:len(nav_list)])
    # 只取 warmup 之后的数据计算绩效（WF 场景下跳过训练期）
    eval_start = min(warmup_days, len(nav) - 1)
    eval_nav = nav.iloc[eval_start:]
    if len(eval_nav) < 5:
        # 数据太少，用全部数据
        eval_nav = nav
    rets = eval_nav.pct_change().dropna()
    years = max(len(eval_nav) / 252, 0.01)
    total_ret = eval_nav.iloc[-1] / eval_nav.iloc[0] - 1
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = rets.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    max_dd = ((eval_nav.cummax() - eval_nav) / eval_nav.cummax()).max()
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
                 stop_loss=0.20, label='wf',
                 score_fn=None,
                 volume_panel=None, amount_panel=None,
                 high_panel=None, low_panel=None,
                 run_kwargs=None,
                 industry_map=None,
                 **kwargs):
    """Walk-Forward 过拟合检测。

    将时间轴上滑动窗口：训练期(约1年) → 测试期(约1季度)
    每一轮回测独立进行，最终拼接样本外净值曲线。

    score_fn: 评分函数 fn(factors_panel) -> score_df，必须提供
    volume_panel/amount_panel/high_panel/low_panel: 完整的面板数据（用于 WF 切片）
    """
    run_kwargs = run_kwargs or {}
    # 提取 market_filter 参数（从 kwargs 或 run_kwargs）
    _mf_keys = ('use_market_filter', 'market_filter_method', 'market_ma_short', 'market_ma_long')
    for _k in _mf_keys:
        if _k in kwargs:
            run_kwargs[_k] = kwargs[_k]

    dates = close_panel.index
    n = len(dates)
    fold_results = []
    fold_navs = []
    fold = 0

    if score_fn is None:
        raise ValueError("walk_forward 必须提供 score_fn 参数")
    if run_kwargs is None:
        run_kwargs = {}

    # volume/amount/high/low 面板切片辅助函数
    def _slice_panel(panel, idx):
        if panel is not None:
            return panel.loc[idx]
        return None

    train_end = train_days
    while train_end + test_days <= n:
        fold += 1
        train_start = max(0, train_end - train_days)
        test_start = train_end
        test_end = min(n, train_end + test_days)

        # 切片窗口
        window_dates = dates[train_start:test_end]
        sub_close = close_panel.loc[window_dates]
        sub_volume = _slice_panel(volume_panel, window_dates)
        sub_amount = _slice_panel(amount_panel, window_dates)
        sub_high = _slice_panel(high_panel, window_dates)
        sub_low = _slice_panel(low_panel, window_dates)

        # 计算因子（用真实 volume/amount/high/low）
        from core.factors import calc_factors_panel
        sub_factors = calc_factors_panel(
            sub_close, sub_volume, sub_amount,
            high_panel=sub_high, low_panel=sub_low,
            industry_map=industry_map,
        )
        sub_score = score_fn(sub_factors)

        # 截取测试期评分
        test_dates = dates[test_start:test_end]
        sub_score_test = sub_score.loc[test_dates]
        sub_close_test = sub_close.loc[test_dates]

        # 跑回测：传入 train+test 数据，用 warmup_days 跳过训练期
        # warmup_days 是相对于 sub_close 起始的偏移 = 训练期长度
        _warmup = train_end - train_start
        m, nav, _ = run_backtest(
            sub_close, sub_score,
            top_n=top_n, rebalance_freq=rebalance_freq,
            stop_loss=stop_loss, label=f'{label}_fold{fold}',
            warmup_days=_warmup,
            **run_kwargs,
        )

        # 只取 test 期 nav 片段用于拼接
        test_nav = nav[test_dates] if nav is not None else None

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
        if test_nav is not None:
            fold_navs.append(test_nav)

        print(f"  WF Fold {fold}: {fold_results[-1]['test']} | "
              f"Ret={m['annual_return']:.1%} Sharpe={m['sharpe_ratio']:.2f} "
              f"DD={m['max_drawdown']:.1%}")

        train_end += step_days

    # 拼接样本外净值
    # Bug 1 修复：按时间拼接 test 期切片，保留累计净值
    if fold_navs:
        combined_nav = None
        for tnav in fold_navs:
            if combined_nav is None:
                combined_nav = tnav / tnav.iloc[0]
            else:
                # 用上一段末尾净值衔接，保留累计
                combined_nav = pd.concat([combined_nav, tnav * (combined_nav.iloc[-1] / tnav.iloc[0])])
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
        "zz800_constituents.csv"
    )
    if not os.path.exists(hs300_path):
        hs300_path = "/root/zz800_constituents.csv"
    try:
        hs300 = pd.read_csv(hs300_path)
        return dict(zip(
            hs300['code'].astype(str).str.zfill(6),
            hs300['name']
        ))
    except Exception as e:
        print(f"  ⚠️  Could not load stock names: {e}")
        return {}


# ============================================================
# 主流程
# ============================================================
def main():
    # 动态生成策略帮助文本
    from core.config import STRATEGY_PROFILES
    available = " | ".join(sorted(STRATEGY_PROFILES.keys()))

    parser = argparse.ArgumentParser(
        description="A股量化回测系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"可用策略（来自 core.config.STRATEGY_PROFILES）：\n  {available}\n"
               f"指定多个：--strategy v5_tp_decay v6a_12f_icir v7c_8f_no_ind\n"
               f"全部运行：--strategy all")
    parser.add_argument("--strategy", nargs="+", default=["all"],
                        help=f"策略名称列表，或 all（全部）。可用：{available}")
    parser.add_argument("--start", default=None, help="回测起始日期 (YYYY-MM-DD)")
    parser.add_argument("--end", default=None, help="回测结束日期 (YYYY-MM-DD)")
    parser.add_argument("--top-n", type=int, default=None, help="覆盖：持仓数量")
    parser.add_argument("--rebalance-freq", type=int, default=None, help="覆盖：调仓频率(日)")
    parser.add_argument("--stop-loss", type=float, default=None, help="覆盖：止损比例")
    parser.add_argument("--max-position", type=float, default=None, help="覆盖：单只最大仓位")
    parser.add_argument("--scan", action="store_true", help="启用参数网格扫描")
    parser.add_argument("--walk-forward", action="store_true", help="启用 Walk-Forward 过拟合检测")
    parser.add_argument("--output-dir", default=None, help="结果输出目录")
    parser.add_argument("--report-markdown", action="store_true", help="输出 Markdown 报告到 stdout")
    parser.add_argument("--ic-analysis", action="store_true", help="运行 IC 因子分析")
    parser.add_argument("--exec-timing", choices=["close", "open"], default="close",
                        help="执行时序: close=收盘价执行(默认), open=开盘价执行(盘中模式)")
    parser.add_argument("--log", action="store_true", help="自动追加结果到 docs/RESULTS_LOG.md")
    args = parser.parse_args()

    # Load stock names for industry classification
    stock_names = _load_stock_names()

    # ── 加载行业分类映射 ──────────────────────────────────────────
    from core.db import load_industry_map
    _industry_map = load_industry_map()
    if _industry_map:
        print(f"  行业分类: {len(_industry_map)} 只股票, {len(set(_industry_map.values()))} 个行业")

    print("=" * 60)
    print("A股量化回测系统  |  策略参数来源：core.config.STRATEGY_PROFILES")
    print("=" * 60)
    t0 = time.time()

    # ── 1. 加载数据（need_hl 始终加载，短线因子所有策略都用）──
    need_open = (args.exec_timing == "open")
    need_hl = True  # 短线因子在面板模式下始终需要
    print(f"\n[1/5] 加载数据... (exec_timing={args.exec_timing})")
    loaded, codes = load_panel(
        args.start, args.end, need_open=need_open, need_hl=need_hl,
    )
    close_panel  = loaded[0]
    volume_panel = loaded[1]
    amount_panel = loaded[2]
    open_panel   = loaded[3] if len(loaded) > 3 else None
    high_panel   = loaded[4] if len(loaded) > 4 else None
    low_panel    = loaded[5] if len(loaded) > 5 else None
    print(f"  Panel: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只股票")
    print(f"  区间: {close_panel.index[0].date()} ~ {close_panel.index[-1].date()}")
    if need_open and open_panel is not None:
        print(f"  Open panel: {open_panel.shape[0]} 天 × {open_panel.shape[1]} 只股票")
    if high_panel is not None and low_panel is not None:
        print(f"  H/L panel: {high_panel.shape[0]} 天 × {high_panel.shape[1]} 只股票")

    # ── 2. 因子计算 ────────────────────────────────────────────
    print(f"\n[2/5] 计算因子...")
    factors = calc_factors_panel(
        close_panel, volume_panel, amount_panel,
        open_panel=open_panel, high_panel=high_panel, low_panel=low_panel,
        industry_map=_industry_map,
    )
    print(f"  共 {len(factors)} 个因子")

    # ── 3.5 加载基本面质量因子 ──────────────────────────────────
    _has_quality_profile = False
    for _name in args.strategy:
        if _name in STRATEGY_PROFILES:
            _pf = STRATEGY_PROFILES[_name]
            if _pf.factor_weights and any(k in _pf.factor_weights for k in ['roe', 'revenue_yoy', 'profit_yoy', 'gross_margin', 'debt_asset']):
                _has_quality_profile = True
                break

    if _has_quality_profile:
        print(f"  📊 加载基本面质量因子...")
        try:
            from scripts.tools.quality_data import build_quality_factors
            _quality = build_quality_factors(codes, close_panel.index, start_year="2019")
            factors.update(_quality)
            print(f"  ✅ 质量因子已加载（共 {len(factors)} 个因子）")
        except Exception as _e:
            print(f"  ⚠️ 质量因子加载失败: {_e}，回退为 NaN")

    # ── 3. IC 分析（可选） ────────────────────────────────────
    ic_results = None
    if args.ic_analysis:
        print(f"\n[3/5] IC 分析...")
        ic_results = run_ic_analysis(factors, close_panel)

    # ── 4. 评分构建 ────────────────────────────────────────────
    print(f"\n[3/5] 构建评分矩阵...")
    score_equal = composite_score(factors)  # 默认 29 因子等权

    # ── 5. 策略选择：自动从 STRATEGY_PROFILES 读取 ─────────────
    print(f"\n[4/5] 运行回测...")

    requested = args.strategy
    run_all = "all" in requested

    # 解析命令行通用参数覆盖
    cli_top_n = args.top_n
    cli_rebal = args.rebalance_freq
    cli_sl = args.stop_loss
    cli_max_pos = args.max_position

    configs = []

    def _build_cfg(profile, score):
        """从 StrategyProfile 构建运行 config，支持命令行参数覆盖"""
        kw = dict(
            top_n=cli_top_n if cli_top_n is not None else profile.top_n,
            rebalance_freq=cli_rebal if cli_rebal is not None else profile.rebalance_freq,
            stop_loss=cli_sl if cli_sl is not None else profile.stop_loss,
            max_position=cli_max_pos if cli_max_pos is not None else profile.max_position,
            max_industry_weight=profile.max_industry_weight,
            max_daily_turnover=profile.max_daily_turnover,
            weight_method=profile.weight_method,
            stock_names=stock_names,
            use_take_profit=profile.use_take_profit,
            tp_tiers=profile.tp_tiers,
            use_holding_decay=profile.use_holding_decay,
            exec_timing=args.exec_timing,
            # opt-6: market filter
            use_market_filter=profile.use_market_filter,
            market_filter_method=profile.market_filter_method,
            market_ma_short=profile.market_ma_short,
            market_ma_long=profile.market_ma_long,
            # HMM 仓位管理
            use_hmm_position=profile.use_hmm_position,
        )
        if args.exec_timing == 'open':
            kw['open_panel'] = open_panel
        return {'label': profile.label, 'score': score, 'kwargs': kw}

    # Score building: unified via StrategyEngine (supports factor + ensemble + multi)
    def _build_score_for_profile(profile):
        if profile.multi_strategy:
            engine = StrategyEngine(profile=profile.label, mode="multi")
            return engine.score_panel(factors)
        elif profile.ensemble_groups:
            engine = StrategyEngine(profile=profile.label, mode="ensemble")
            # HMM 因子择时：预计算 HMM 仓位序列
            _hmm_pos = None
            if getattr(profile, 'use_hmm_position', False):
                try:
                    from core.hmm_timing import compute_hmm_positions_batch
                    _hmm_pos = compute_hmm_positions_batch(close_panel, min_lookback=60)
                except Exception as _e:
                    print(f"  ⚠️ HMM 预计算失败: {_e}")
            return engine.score_panel(factors, hmm_positions=_hmm_pos)
        elif profile.factor_weights:
            engine = StrategyEngine(profile=profile.label, mode="factor")
            return engine.score_panel(factors)
        else:
            return score_equal

    def _mode_for_profile(profile):
        if profile.multi_strategy: return "multi"
        if profile.ensemble_groups: return "ensemble"
        if profile.factor_weights: return "factor"
        return "factor"

    if run_all:
        for name, profile in sorted(STRATEGY_PROFILES.items()):
            score = _build_score_for_profile(profile)
            configs.append(_build_cfg(profile, score))
    else:
        for name in requested:
            if name not in STRATEGY_PROFILES:
                print(f"  Unknown strategy '{name}', skipping")
                continue
            profile = STRATEGY_PROFILES[name]
            score = _build_score_for_profile(profile)
            configs.append(_build_cfg(profile, score))

    if not configs:
        print("  ⚠️ 未匹配到任何策略，使用 v4_baseline")
        configs.append(_build_cfg(STRATEGY_PROFILES["v4_baseline"], score_equal))

    metrics_list = []
    nav_dict = {}
    trades_dict = {}

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
        # 对每个策略分别跑 WF
        for cfg in configs:
            profile = STRATEGY_PROFILES.get(cfg['label'])
            # 用 StrategyEngine 构建统一的评分函数（支持 factor/ensemble/multi）
            _engine = StrategyEngine(profile=cfg['label'], mode=_mode_for_profile(profile))
            score_fn = lambda factors, eng=_engine: eng.score_panel(factors)
            print(f"\n  --- WF: {cfg['label']} ---")
            # 从 cfg 提取 run_backtest 的风控参数（止盈/衰减/行业限制等）
            _rk = {k: v for k, v in cfg['kwargs'].items()
                   if k not in ('top_n', 'rebalance_freq', 'stop_loss', 'label', 'score',
                                'exec_timing', 'open_panel', 'stock_names',
                                'use_market_filter', 'market_filter_method',
                                'market_ma_short', 'market_ma_long')}
            # 提取 market_filter 参数（WF 透传）
            _mf = {k: cfg['kwargs'].get(k) for k in
                   ('use_market_filter', 'market_filter_method', 'market_ma_short', 'market_ma_long')
                   if k in cfg['kwargs']}
            wf_results, wf_nav = walk_forward(
                close_panel,
                top_n=top_n, rebalance_freq=rebal_freq, stop_loss=sl,
                label=cfg['label'], score_fn=score_fn,
                volume_panel=volume_panel, amount_panel=amount_panel,
                high_panel=high_panel, low_panel=low_panel,
                run_kwargs=_rk,
                industry_map=_industry_map,
                **_mf,
            )
            if wf_results:
                print(f"\n  Walk-Forward 汇总 {cfg['label']} ({len(wf_results)} folds):")
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
            from scripts.archive.log_backtest_result import append_row
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
