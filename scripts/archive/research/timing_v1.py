#!/usr/bin/env python3
"""
指数择时策略 v1
================
在 v5 策略基础上，加入市场趋势择时 filter。

择时信号来源：
  - 用股票池收盘价的市值加权均值作为市场代理指数
  - 计算 MA60/MA20 趋势
  - MA 线上=看多(满仓)，线下=看空(清仓持现)

对比：
  A. current:     无择时，rf=20, vt=0.20（v4 基准）
  B. timing_M60:  择时 MA60, rf=10, vt=0.10
  C. timing_MA20: 择时 MA20, rf=10, vt=0.10
  D. timing_M60_v4: 择时 MA60, rf=20, vt=0.20（用 v4 参数 + 择时）
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


def load_panel(start='2021-01-01', end='2026-05-29'):
    files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
    stock_data = {}
    for f in files:
        code = f.replace(".csv", "")
        df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
        if len(df) > 0:
            stock_data[code] = df

    valid = {}
    for code, df in stock_data.items():
        if df.index.min() <= pd.Timestamp(start) + pd.Timedelta(days=60) and \
           df.index.max() >= pd.Timestamp(end) - pd.Timedelta(days=30):
            valid[code] = df

    close_panel = pd.DataFrame({c: d['close'] for c, d in valid.items()})
    volume_panel = pd.DataFrame({c: d['volume'] for c, d in valid.items()})
    amount_panel = pd.DataFrame({c: d.get('amount', d['close'] * d['volume']) for c, d in valid.items()})

    common = close_panel.dropna(how='all').index
    common = common[(common >= pd.Timestamp(start)) & (common <= pd.Timestamp(end))]

    cp = close_panel.loc[common].sort_index()
    vp = volume_panel.loc[common].sort_index()
    ap = amount_panel.loc[common].sort_index()

    # 市场代理指数：股票池等权均值
    market_proxy = cp.mean(axis=1)

    return cp, vp, ap, market_proxy, list(valid.keys())


def calc_timing_signal(market_proxy, ma_period=60):
    """择时信号：MA 线上=1（看多），线下=0（看空/持现）。NaN 时默认看多。"""
    ma = market_proxy.rolling(ma_period).mean()
    signal = (market_proxy >= ma).astype(float)
    signal = signal.fillna(1.0)
    return signal


def run_bt(close_panel, score, timing_signal,
           top_n=12, rebal_freq=10, stop_loss=0.20,
           use_vol_scaling=True, vol_target=0.10,
           max_position=0.10, label='default'):
    from core.config import config

    state = PortfolioState(
        cash=config.costs.initial_capital,
        initial_capital=config.costs.initial_capital
    )
    dates = close_panel.index
    nav_list = []

    # 预热期
    warmup = 120
    for i in range(warmup):
        if i < len(dates):
            nav_list.append(config.costs.initial_capital)

    for i, date in enumerate(dates):
        if i < warmup:
            continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else config.costs.initial_capital)
            continue

        try:
            price_data = close_panel.loc[date]
        except KeyError:
            nav_list.append(nav_list[-1])
            continue

        state = check_stop_loss(state, date, price_data)

        # 择时信号
        if timing_signal is not None:
            timing = float(timing_signal.loc[date]) if date in timing_signal.index else 1.0
        else:
            timing = 1.0

        # 调仓日
        if (i - warmup) % rebal_freq == 0 and date in score.index:
            if timing > 0.5:
                # 看多：正常选股
                try:
                    day_score = score.loc[date].dropna()
                except KeyError:
                    nav_list.append(nav_list[-1])
                    continue
                valid_idx = day_score.index.isin(price_data.dropna().index)
                day_score = day_score[valid_idx]

                if use_vol_scaling:
                    returns = close_panel.pct_change()
                    try:
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
                                if not np.isnan(p) and p > 0:
                                    state = sell(state, c, p, date, reason='SELL')
                            except (TypeError, ValueError):
                                pass

                    weights = {c: 1.0 / len(top_stocks) for c in top_stocks}
                    for c in top_stocks:
                        if c not in state.holdings and c in price_data.index:
                            try:
                                p = float(price_data[c])
                                if not np.isnan(p) and p > 0:
                                    w = weights.get(c, 1.0 / len(top_stocks))
                                    target_val = min(current_pv * w, current_pv * max_position)
                                    adj_p = p * (1 + config.costs.slippage_rate)
                                    shares = int(target_val / adj_p / 100) * 100
                                    if shares > 0:
                                        state = buy(state, c, p, date, shares=shares)
                            except (TypeError, ValueError):
                                pass
            else:
                # 看空：清仓持现
                for c in list(state.holdings.keys()):
                    if c in price_data.index:
                        try:
                            p = float(price_data[c])
                            if not np.isnan(p) and p > 0:
                                state = sell(state, c, p, date, reason='TIMING_EXIT')
                        except (TypeError, ValueError):
                            pass

        try:
            dv = portfolio_value(state, date, price_data)
        except Exception:
            dv = nav_list[-1] if nav_list else config.costs.initial_capital
        nav_list.append(dv)

    actual_dates = dates[:len(nav_list)]
    nav = pd.Series(nav_list, index=actual_dates)
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
    calmar = ann_ret / max_dd if max_dd > 0 else 0

    trades_df = pd.DataFrame(state.trade_log)
    sl_count = len(trades_df[trades_df['action'] == 'STOP_LOSS']) if len(trades_df) > 0 else 0
    exit_count = len(trades_df[trades_df['action'] == 'TIMING_EXIT']) if len(trades_df) > 0 else 0
    total_cost = float(trades_df['cost'].sum()) if len(trades_df) > 0 else 0

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
    }


if __name__ == "__main__":
    t0 = time.time()
    print("=" * 65)
    print("指数择时策略 v1")
    print("=" * 65)

    print("\n加载数据...")
    close_panel, volume_panel, amount_panel, market_proxy, stocks = load_panel()
    print(f"  Panel: {len(close_panel)} 天 × {len(stocks)} 只股票")

    print("\n计算因子...")
    factors = calc_factors_panel(close_panel, volume_panel, amount_panel)
    score = composite_score(factors, DEFAULT_FACTOR_WEIGHTS)

    # 择时信号
    signal_M60 = calc_timing_signal(market_proxy, ma_period=60)
    signal_MA20 = calc_timing_signal(market_proxy, ma_period=20)

    print(f"\n择时信号分布:")
    print(f"  MA60: 看多 {signal_M60.mean()*100:.1f}%  看空 {(1-signal_M60.mean())*100:.1f}%")
    print(f"  MA20: 看多 {signal_MA20.mean()*100:.1f}%  看空 {(1-signal_MA20.mean())*100:.1f}%")

    # 回测对比
    results = {}

    combos = [
        ('A_current',       dict(rebal_freq=20, vol_target=0.20, timing=None, label='A_current')),
        ('B_timing_M60',    dict(rebal_freq=10, vol_target=0.10, timing=signal_M60, label='B_timing_M60')),
        ('C_timing_MA20',   dict(rebal_freq=10, vol_target=0.10, timing=signal_MA20, label='C_timing_MA20')),
        ('D_timing_M60_v4', dict(rebal_freq=20, vol_target=0.20, timing=signal_M60, label='D_timing_M60_v4')),
    ]

    for key, cfg in combos:
        m = run_bt(
            close_panel, score,
            timing_signal=cfg['timing'],
            top_n=12,
            rebal_freq=cfg['rebal_freq'],
            vol_target=cfg['vol_target'],
            label=key
        )
        if m is None:
            print(f"\n  {key}: 回测失败")
            continue
        results[key] = m
        print(f"\n  {key}:")
        print(f"    Sharpe={m['sharpe_ratio']:.3f}  Ret={m['annual_return']:.2%}  DD={m['max_drawdown']:.2%}")
        print(f"    Cost=¥{m['total_cost']:,.0f}  Trades={m['total_trades']}  "
              f"SL={m['stop_loss_trades']}  TimingExit={m['timing_exit_trades']}")

    if not results:
        print("\n所有回测均失败")
        sys.exit(1)

    # 汇总表
    print(f"\n{'=' * 65}")
    keys = list(results.keys())
    header = f"{'':20}" + "".join(f"{k:>14}" for k in keys)
    print(header)
    print(f"{'─' * (20 + 14 * len(keys))}")
    for metric in ['annual_return', 'sharpe_ratio', 'sortino_ratio', 'max_drawdown',
                   'calmar_ratio', 'total_trades', 'timing_exit_trades', 'total_cost']:
        row = f"{metric:<20}"
        for k in keys:
            v = results[k][metric]
            if metric in ('annual_return', 'max_drawdown'):
                row += f"{v:>13.2%} "
            elif metric == 'total_cost':
                row += f"¥{v:>12,.0f} "
            elif metric in ('total_trades', 'timing_exit_trades'):
                row += f"{v:>14d} "
            else:
                row += f"{v:>14.4f} "
        print(row)

    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")

    out_path = os.path.join(DATA_DIR, "backtest_results", "timing_v1_results.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"保存: {out_path}")
