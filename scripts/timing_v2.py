#!/usr/bin/env python3
"""
多信号融合择时模块 v2
======================
融合三个信号生成择时分数（0~1）：
  1. 趋势信号（权重 40%）：市场代理 MA60 斜率
  2. 动量信号（权重 30%）：市场代理 20 日收益
  3. 波动率信号（权重 30%）：市场代理 20 日波动率（低波=看多）

择时分数 > 0.5 → 看多（满仓）
择时分数 <= 0.5 → 看空（清仓持现）
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
    cp = close_panel.loc[common].sort_index()
    vp = volume_panel.loc[common].sort_index()
    ap = amount_panel.loc[common].sort_index()
    market_proxy = cp.mean(axis=1)
    return cp, vp, ap, market_proxy, list(valid.keys())


def calc_multi_signal(market_proxy, w_trend=0.4, w_mom=0.3, w_vol=0.3):
    """
    多信号融合择时。
    返回 Series（0~1，>0.5 看多）。
    """
    # 信号 1：趋势（MA60 斜率，标准化到 0~1）
    ma60 = market_proxy.rolling(60).mean()
    ma60_slope = ma60.pct_change(20)  # 20 日 MA60 变化率
    # 用 sigmoid 映射到 0~1
    signal_trend = 1 / (1 + np.exp(-ma60_slope * 100))

    # 信号 2：动量（20 日收益）
    ret_20d = market_proxy.pct_change(20)
    signal_mom = 1 / (1 + np.exp(-ret_20d * 50))

    # 信号 3：波动率（20 日波动率，低波=看多）
    vol_20d = market_proxy.pct_change().rolling(20).std()
    # 波动率倒数归一化
    vol_inv = 1 / (vol_20d + 1e-8)
    vol_median = vol_inv.rolling(252).median()
    signal_vol = (vol_inv / (vol_median + 1e-8)).clip(0, 2) / 2  # 归一化到 0~1

    # 融合
    combined = w_trend * signal_trend + w_mom * signal_mom + w_vol * signal_vol
    combined = combined.fillna(0.5).clip(0, 1)
    return combined


def run_bt(close_panel, score, timing_signal, label='default'):
    """带回测的择时策略。"""
    from core.config import config

    dates = close_panel.index
    n = len(dates)
    warmup = min(120, max(20, n // 3))

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

        # 择时信号
        ts = 0.5
        if timing_signal is not None and date in timing_signal.index:
            ts = float(timing_signal.loc[date])

        if (i - warmup) % 20 == 0 and date in score.index:
            bull = (timing_signal is None) or (ts > 0.5)
            if bull:
                # 看多
                try:
                    day_score = score.loc[date].dropna()
                except KeyError:
                    nav_list.append(nav_list[-1])
                    continue
                valid_idx = day_score.index.isin(price_data.dropna().index)
                day_score = day_score[valid_idx]

                try:
                    returns = close_panel.pct_change()
                    stock_vol = returns.rolling(20).std().loc[date]
                    vol_scale = 0.20 / (stock_vol * np.sqrt(252))
                    vol_scale = vol_scale.clip(0.1, 3.0)
                    day_score = day_score * vol_scale.reindex(day_score.index).fillna(1)
                except Exception:
                    pass

                top_stocks = day_score.nlargest(12).index.tolist()
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
                                    target_val = min(current_pv * w, current_pv * 0.10)
                                    adj_p = p * (1 + config.costs.slippage_rate)
                                    shares = int(target_val / adj_p / 100) * 100
                                    if shares > 0:
                                        state = buy(state, c, p, date, shares=shares)
                            except (TypeError, ValueError):
                                pass
            else:
                # 看空：清仓
                for c in list(state.holdings.keys()):
                    if c in price_data.index:
                        try:
                            p = float(price_data[c])
                            if not (np.isnan(p) or p <= 0):
                                state = sell(state, c, p, date, reason='TIMING_EXIT')
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
    exit_count = len(trades_df[trades_df['action'] == 'TIMING_EXIT']) if len(trades_df) > 0 else 0
    total_cost = float(trades_df['cost'].sum()) if len(trades_df) > 0 else 0

    pct_bull = (timing_signal > 0.5).mean() if timing_signal is not None else 1.0

    return {
        'label': label,
        'annual_return': round(float(ann_ret), 6),
        'sharpe_ratio': round(float(sharpe), 4),
        'sortino_ratio': round(float(sortino), 4),
        'max_drawdown': round(float(max_dd), 6),
        'calmar_ratio': round(float(calmar), 4),
        'total_trades': len(trades_df),
        'stop_loss_trades': int(sl_count),
        'timing_exit_trades': int(exit_count),
        'total_cost': round(total_cost, 2),
        'final_value': round(float(nav.iloc[-1]), 2),
        'pct_bull': round(float(pct_bull), 4),
    }


if __name__ == "__main__":
    t0 = time.time()
    print("=" * 65)
    print("多信号融合择时策略 v2")
    print("=" * 65)

    print("\n加载数据...")
    close_panel, volume_panel, amount_panel, market_proxy, stocks = load_panel()
    print(f"  Panel: {len(close_panel)} 天 × {len(stocks)} 只股票")

    print("\n计算因子...")
    factors = calc_factors_panel(close_panel, volume_panel, amount_panel)
    score = composite_score(factors, DEFAULT_FACTOR_WEIGHTS)

    # 多信号择时
    multi_signal = calc_multi_signal(market_proxy)

    print(f"\n择时信号分布:")
    print(f"  多信号融合: 看多 {multi_signal.mean()*100:.1f}%  看空 {(1-multi_signal.mean())*100:.1f}%")

    # 对比回测
    results = {}

    # A: 无择时（v4 基准）
    results['A_no_timing'] = run_bt(close_panel, score, timing_signal=None, label='A_no_timing')

    # B: 多信号择时
    results['B_multi_signal'] = run_bt(close_panel, score, timing_signal=multi_signal, label='B_multi_signal')

    # C: 简单 MA60 择时（对比）
    ma60 = market_proxy.rolling(60).mean()
    simple_signal = (market_proxy >= ma60).astype(float).fillna(1.0)
    # 转换为 0~1 分数（>0.5 看多）
    simple_combined = simple_signal * 0.8 + 0.1  # 看多=0.9, 看空=0.1
    results['C_ma60_simple'] = run_bt(close_panel, score, timing_signal=simple_combined, label='C_ma60_simple')

    # 打印结果
    for key, m in results.items():
        if m is None:
            print(f"\n  {key}: 回测失败")
            continue
        print(f"\n  {key}:")
        print(f"    Sharpe={m['sharpe_ratio']:.3f}  Ret={m['annual_return']:.2%}  DD={m['max_drawdown']:.2%}")
        print(f"    Cost=¥{m['total_cost']:,.0f}  Trades={m['total_trades']}  "
              f"SL={m['stop_loss_trades']}  TimingExit={m['timing_exit_trades']}  Bull%={m['pct_bull']:.1%}")

    # 汇总
    print(f"\n{'=' * 65}")
    keys = ['A_no_timing', 'B_multi_signal', 'C_ma60_simple']
    header = f"{'':20}" + "".join(f"{k:>16}" for k in keys)
    print(header)
    print(f"{'─' * (20 + 16 * len(keys))}")
    for metric in ['annual_return', 'sharpe_ratio', 'sortino_ratio', 'max_drawdown',
                   'calmar_ratio', 'total_trades', 'timing_exit_trades', 'total_cost', 'pct_bull']:
        row = f"{metric:<20}"
        for k in keys:
            v = results[k][metric] if results[k] else 0
            if metric in ('annual_return', 'max_drawdown', 'pct_bull'):
                row += f"{v:>15.2%} "
            elif metric == 'total_cost':
                row += f"¥{v:>14,.0f} "
            elif metric in ('total_trades', 'timing_exit_trades'):
                row += f"{v:>16d} "
            else:
                row += f"{v:>16.4f} "
        print(row)

    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")

    out_path = os.path.join(DATA_DIR, "backtest_results", "timing_v2_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"保存: {out_path}")
