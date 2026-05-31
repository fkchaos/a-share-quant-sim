#!/usr/bin/env python3
"""
量价因子快速筛选 + 回测
========================
Step 1: 计算 15 个新因子 + IC 分析（快）
Step 2: 选最好的 3-5 个加入 v6b 混合回测（只跑一次）
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
from core.config import STRATEGY_PROFILES, DEFAULT_FACTOR_WEIGHTS

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

def calc_new_factors(close_panel, volume_panel, amount_panel):
    """计算 15 个新量价因子"""
    eps = 1e-10
    factors = {}
    returns = close_panel.pct_change()

    # 1. 换手率变化率
    factors['turnover_change'] = volume_panel.rolling(5).mean() / (volume_panel.rolling(20).mean() + eps)

    # 2. 量价背离
    price_trend = returns.rolling(10).sum()
    vol_trend = volume_panel.rolling(10).mean() / (volume_panel.rolling(30).mean() + eps)
    factors['pv_divergence'] = -(price_trend * vol_trend)  # 负号：价升量缩=危险

    # 3. 振幅因子（用 rolling std 近似）
    factors['amplitude'] = close_panel.rolling(5).std() / (close_panel + eps)

    # 4. 资金流强度
    factors['money_flow'] = amount_panel.pct_change(5)

    # 5. 动量加速度
    factors['mom_accel'] = returns.rolling(5).sum() - returns.rolling(10).sum()

    # 6. 波动率偏度
    up_vol = returns.where(returns > 0).rolling(20).std()
    down_vol = returns.where(returns < 0).rolling(20).std()
    factors['vol_skew'] = (up_vol - down_vol) / (up_vol + down_vol + eps)

    # 7. 量价相关性
    factors['pv_corr'] = close_panel.rolling(20).corr(volume_panel)

    # 8. 换手率偏度
    turnover = volume_panel / (volume_panel.rolling(20).mean() + eps)
    factors['turnover_skew'] = turnover.rolling(20).skew()

    # 9. 价格冲击
    factors['price_impact'] = returns.abs() / (turnover + eps)

    # 10. Amihud 非流动性
    factors['illiquidity'] = returns.abs() / (amount_panel / 1e8 + eps)

    # 11. 趋势强度
    factors['trend_strength'] = close_panel.rolling(10).std() / (close_panel.rolling(30).std() + eps)

    # 12. 成交量加权动量
    factors['vol_mom'] = (returns * volume_panel).rolling(10).sum() / (volume_panel.rolling(10).sum() + eps)

    # 13. 筹码集中度（价格峰度）
    factors['chip_kurt'] = close_panel.rolling(20).kurt()

    # 14. 反转信号
    factors['reversal'] = -(close_panel.rolling(5).max() - close_panel) / (close_panel.rolling(5).max() - close_panel.rolling(5).min() + eps)

    # 15. 量价比（OBV 变体）
    obv = (returns * volume_panel).cumsum()
    factors['obv_slope'] = obv.rolling(10).apply(lambda x: np.polyfit(range(len(x)), x, 1)[0] if len(x) > 1 else 0, raw=True)

    return factors

def calc_ic_fast(factors, close_panel, forward=20):
    """快速 IC：全截面一次性计算"""
    future_ret = close_panel.pct_change(forward).shift(-forward)
    ic_results = {}
    for fname, fdf in factors.items():
        # 取每第5天的截面
        sample_idx = fdf.index[::5]
        ic_vals = []
        for date in sample_idx:
            if date not in future_ret.index:
                continue
            f_row = fdf.loc[date].dropna()
            r_row = future_ret.loc[date].dropna()
            common = f_row.index.intersection(r_row.index)
            if len(common) < 10:
                continue
            fv = f_row[common].values
            rv = r_row[common].values
            if np.std(fv) < 1e-10 or np.std(rv) < 1e-10:
                continue
            c = np.corrcoef(fv, rv)[0, 1]
            if not np.isnan(c):
                ic_vals.append(c)
        if len(ic_vals) > 5:
            m = np.mean(ic_vals)
            s = np.std(ic_vals)
            ic_results[fname] = {'ic_mean': round(float(m), 6), 'ic_std': round(float(s), 4),
                                  'ic_ir': round(float(m / (s + 1e-10)), 4), 'n': len(ic_vals)}
    return ic_results

def run_bt_quick(close_panel, score, label='default'):
    """轻量回测（~60s）"""
    p = STRATEGY_PROFILES["v6b_8f_pos_ic"]
    state = PortfolioState(cash=200_000, initial_capital=200_000)
    dates = close_panel.index
    nav_list = []

    for i, date in enumerate(dates):
        if i < 120:
            nav_list.append(200_000)
            continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1])
            continue
        price_data = close_panel.loc[date]
        state = check_stop_loss(state, date, price_data)
        if p.use_take_profit and p.tp_tiers:
            state = check_take_profit(state, date, price_data, p.tp_tiers)
        if p.use_holding_decay:
            state = apply_holding_decay(state, date, price_data, rebalance_freq=p.rebalance_freq)
        if (i - 120) % p.rebalance_freq == 0 and date in score.index:
            ds = score.loc[date].dropna()
            vi = ds.index.isin(price_data.dropna().index)
            ds = ds[vi]
            if p.use_vol_scaling:
                vol = close_panel.pct_change().rolling(20).std().loc[date]
                vs = (p.vol_target / (vol * np.sqrt(252))).clip(0.1, 3.0)
                ds = ds * vs.reindex(ds.index).fillna(1)
            top = ds.nlargest(p.top_n).index.tolist()
            if top:
                cpv = portfolio_value(state, date, price_data)
                for c in list(state.holdings.keys()):
                    if c not in top and c in price_data.index:
                        pr = price_data[c]
                        if not pd.isna(pr) and pr > 0:
                            state = sell(state, c, pr, date, reason='SELL')
                ws = {c: 1.0 / len(top) for c in top}
                for c in top:
                    if c not in state.holdings and c in price_data.index:
                        pr = price_data[c]
                        if not pd.isna(pr) and pr > 0:
                            w = ws.get(c, 1.0 / len(top))
                            tv = min(cpv * w, cpv * p.max_position)
                            sh = int(tv / (pr * 1.001) / 100) * 100
                            if sh > 0:
                                state = buy(state, c, pr, date, shares=sh)
        nav_list.append(portfolio_value(state, date, price_data))

    nav = pd.Series(nav_list, index=dates[:len(nav_list)])
    rets = nav.pct_change().dropna()
    yr = max(len(nav) / 252, 0.01)
    tr = nav.iloc[-1] / nav.iloc[0] - 1
    ar = (1 + tr) ** (1 / yr) - 1
    av = rets.std() * np.sqrt(252)
    sp = ar / av if av > 0 else 0
    dd = ((nav.cummax() - nav) / nav.cummax()).max()
    cm = ar / dd if dd > 0 else 0
    wr = (rets > 0).sum() / len(rets)
    td = pd.DataFrame(state.trade_log)
    tc = float(td['cost'].sum()) if len(td) > 0 else 0
    return {'label': label, 'annual_return': round(float(ar), 4), 'sharpe_ratio': round(float(sp), 4),
            'max_drawdown': round(float(dd), 4), 'calmar_ratio': round(float(cm), 4),
            'win_rate': round(float(wr), 4), 'total_trades': len(td), 'total_cost': round(tc, 0)}

def main():
    print("=" * 70)
    print("量价因子快速筛选")
    print("=" * 70)

    print("\n[1/3] 加载数据 + 计算因子...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  {close_panel.shape[0]} 天 × {len(stocks)} 只股票")

    new_factors = calc_new_factors(close_panel, volume_panel, amount_panel)
    old_factors = calc_factors_panel(close_panel, volume_panel, amount_panel)
    print(f"  新因子: {len(new_factors)} 个 | 旧因子: {len(old_factors)} 个")

    print("\n[2/3] IC 分析...")
    ic_new = calc_ic_fast(new_factors, close_panel)
    ic_old = calc_ic_fast({k: v for k, v in old_factors.items() if k in DEFAULT_FACTOR_WEIGHTS}, close_panel)

    print(f"\n  新因子 IC 排名:")
    print(f"  {'因子':<25} {'IC':>8} {'IC_IR':>8}")
    print(f"  {'─'*43}")
    for fname in sorted(ic_new.keys(), key=lambda x: abs(ic_new[x]['ic_ir']), reverse=True):
        r = ic_new[fname]
        sig = '***' if abs(r['ic_ir']) > 0.03 else '**' if abs(r['ic_ir']) > 0.02 else ''
        print(f"  {fname:<25} {r['ic_mean']:>+8.4f} {r['ic_ir']:>+8.4f} {sig}")

    # 筛选好因子：|IC_IR| > 0.02 且与旧因子低相关
    good_new = {f: r for f, r in ic_new.items() if abs(r['ic_ir']) > 0.02}
    print(f"\n  |IC_IR| > 0.02 的新因子: {len(good_new)} 个")

    # 检查与 v6b 因子的相关性
    v6b_factors = set(STRATEGY_PROFILES['v6b_8f_pos_ic'].factor_weights.keys())
    print(f"\n  与 v6b 因子的相关性检查:")
    for fname in good_new:
        ir_new = ic_new[fname]['ic_ir']
        # 简单检查：新因子 IC 方向是否与 v6b 因子互补
        direction = '正向' if ir_new > 0 else '负向(反转)'
        print(f"    {fname}: IC_IR={ir_new:>+.4f}  {direction}")

    print("\n[3/3] 回测（仅混合方案）...")
    results = {}

    # v6b 基准
    print("  ▶ v6b 基准...", end=" ", flush=True)
    t0 = time.time()
    v6b = STRATEGY_PROFILES['v6b_8f_pos_ic']
    vf = {k: v for k, v in old_factors.items() if k in v6b.factor_weights}
    results['v6b'] = run_bt_quick(close_panel, composite_score(vf, v6b.factor_weights), 'v6b')
    print(f"({time.time()-t0:.0f}s)  {results['v6b']['annual_return']:.2%}  Sharpe={results['v6b']['sharpe_ratio']:.2f}")

    # 方案 A：v6b + 全部好新因子（各占 50% 权重）
    if good_new:
        # 取 |IC_IR| 最高的 5 个
        top_new = sorted(good_new.items(), key=lambda x: abs(x[1]['ic_ir']), reverse=True)[:5]
        top_new_names = [f for f, _ in top_new]

        combined_w = {}
        # v6b 权重占 60%
        for f, w in v6b.factor_weights.items():
            combined_w[f] = w * 0.6
        # 新因子占 40%
        total_ir = sum(abs(r['ic_ir']) for _, r in top_new)
        for f, r in top_new:
            w = 0.4 * abs(r['ic_ir']) / total_ir
            combined_w[f] = w if r['ic_mean'] > 0 else -w

        all_f = {**old_factors, **new_factors}
        valid_f = {k: v for k, v in all_f.items() if k in combined_w}

        print(f"  ▶ v6b + 新因子 ({', '.join(top_new_names)})...", end=" ", flush=True)
        t0 = time.time()
        results['v6b_plus_new'] = run_bt_quick(close_panel, composite_score(valid_f, combined_w), 'v6b_plus_new')
        m = results['v6b_plus_new']
        print(f"({time.time()-t0:.0f}s)  {m['annual_return']:.2%}  Sharpe={m['sharpe_ratio']:.2f}")

    # 输出对比
    labels = list(results.keys())
    print(f"\n{'':>25}", end='')
    for l in labels: print(f" {l:>14}", end='')
    print()
    print("─" * (25 + 15 * len(labels)))
    for k in ['annual_return', 'sharpe_ratio', 'max_drawdown', 'calmar_ratio']:
        print(f"{k:>25}", end='')
        for l in labels:
            v = results[l][k]
            print(f" {v:>13.2%}" if k in ('annual_return', 'max_drawdown') else f" {v:>14.4f}", end='')
        print()

    out_path = os.path.join(DATA_DIR, "backtest_results", "new_factor_test.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump({'ic_new': ic_new, 'ic_old': ic_old,
                   'good_new': {f: ic_new[f] for f in good_new}, 'results': results}, f, indent=2)
    print(f"\n结果已保存: {out_path}")

if __name__ == "__main__":
    main()
