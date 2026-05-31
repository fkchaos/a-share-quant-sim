#!/usr/bin/env python3
"""
新因子去冗余 + 权重优化
========================
1. 计算新旧因子合并的 IC 和相关性矩阵
2. 去冗余：每组高相关因子只保留 |IC_IR| 最高的
3. 用 IC_IR 归一化构建优化权重
4. 对比：v6b vs 去冗余后的新因子 vs 混合
"""
import sys, os, time, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

import numpy as np
import pandas as pd

from core.factors import calc_factors_panel
from core.scoring import composite_score, factor_correlation
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
    return (close_panel.loc[common_dates].sort_index(),
            volume_panel.loc[common_dates].sort_index(),
            amount_panel.loc[common_dates].sort_index()), list(valid.keys())

def calc_all_factors(close_panel, volume_panel, amount_panel):
    """计算全部因子（旧 29 + 新 15）"""
    eps = 1e-10
    returns = close_panel.pct_change()

    # 旧因子
    old_factors = calc_factors_panel(close_panel, volume_panel, amount_panel)

    # 新因子
    new_factors = {}
    new_factors['turnover_change'] = volume_panel.rolling(5).mean() / (volume_panel.rolling(20).mean() + eps)
    new_factors['pv_divergence'] = -(returns.rolling(10).sum() * (volume_panel.rolling(10).mean() / (volume_panel.rolling(30).mean() + eps)))
    new_factors['amplitude'] = close_panel.rolling(5).std() / (close_panel + eps)
    new_factors['money_flow'] = amount_panel.pct_change(5)
    new_factors['mom_accel'] = returns.rolling(5).sum() - returns.rolling(10).sum()
    up_vol = returns.where(returns > 0).rolling(20).std()
    down_vol = returns.where(returns < 0).rolling(20).std()
    new_factors['vol_skew'] = (up_vol - down_vol) / (up_vol + down_vol + eps)
    new_factors['pv_corr'] = close_panel.rolling(20).corr(volume_panel)
    turnover = volume_panel / (volume_panel.rolling(20).mean() + eps)
    new_factors['turnover_skew'] = turnover.rolling(20).skew()
    new_factors['price_impact'] = returns.abs() / (turnover + eps)
    new_factors['illiquidity'] = returns.abs() / (amount_panel / 1e8 + eps)
    new_factors['trend_strength'] = close_panel.rolling(10).std() / (close_panel.rolling(30).std() + eps)
    new_factors['vol_mom'] = (returns * volume_panel).rolling(10).sum() / (volume_panel.rolling(10).sum() + eps)
    new_factors['chip_kurt'] = close_panel.rolling(20).kurt()
    new_factors['reversal'] = -(close_panel.rolling(5).max() - close_panel) / (close_panel.rolling(5).max() - close_panel.rolling(5).min() + eps)
    obv = (returns * volume_panel).cumsum()
    new_factors['obv_slope'] = obv.rolling(10).apply(
        lambda x: np.polyfit(range(len(x)), x, 1)[0] if len(x) > 1 else 0, raw=True)

    return {**old_factors, **new_factors}

def calc_ic_fast(all_factors, close_panel, forward=20):
    """快速 IC（每5天抽样）"""
    future_ret = close_panel.pct_change(forward).shift(-forward)
    ic = {}
    for fname, fdf in all_factors.items():
        idx = fdf.index[::5]
        vals = []
        for date in idx:
            if date not in future_ret.index:
                continue
            fr = fdf.loc[date].dropna()
            rr = future_ret.loc[date].dropna()
            common = fr.index.intersection(rr.index)
            if len(common) < 10:
                continue
            fv, rv = fr[common].values, rr[common].values
            if np.std(fv) < 1e-10 or np.std(rv) < 1e-10:
                continue
            c = np.corrcoef(fv, rv)[0, 1]
            if not np.isnan(c):
                vals.append(c)
        if len(vals) > 5:
            m, s = np.mean(vals), np.std(vals)
            ic[fname] = {'ic_mean': round(float(m), 6), 'ic_ir': round(float(m/(s+1e-10)), 4), 'n': len(vals)}
    return ic

def deduplicate_factors(all_factors, ic_results, corr_threshold=0.7):
    """去冗余：高相关因子组只保留 |IC_IR| 最高的"""
    # 构建因子截面数据（取最后一天）
    snapshot = {}
    for fname, fdf in all_factors.items():
        if fname in ic_results and len(fdf) > 0:
            snapshot[fname] = fdf.iloc[-1]
    snap_df = pd.DataFrame(snapshot).dropna(axis=1, how='all')

    # 计算相关性
    corr = snap_df.corr()

    # 按 |IC_IR| 降序排列
    sorted_factors = sorted(ic_results.keys(), key=lambda x: abs(ic_results[x]['ic_ir']), reverse=True)

    kept = []
    removed = set()

    for fname in sorted_factors:
        if fname in removed:
            continue
        kept.append(fname)
        # 标记所有与它高相关的因子
        for other in sorted_factors:
            if other != fname and other not in removed:
                c = corr.get(fname, {}).get(other, 0)
                if abs(c) > corr_threshold:
                    removed.add(other)

    print(f"\n去冗余结果：{len(ic_results)} → {len(kept)} 个因子")
    print(f"保留的因子：")
    for f in kept:
        r = ic_results[f]
        print(f"  {f:<25} IC={r['ic_mean']:>+.4f}  IC_IR={r['ic_ir']:>+.4f}")

    if removed:
        print(f"\n剔除的冗余因子：")
        for f in sorted(removed, key=lambda x: abs(ic_results[x]['ic_ir']), reverse=True):
            r = ic_results[f]
            print(f"  {f:<25} IC={r['ic_mean']:>+.4f}  IC_IR={r['ic_ir']:>+.4f}")

    return kept

def build_optimal_weights(ic_results, kept_factors, v6b_weight=0.5):
    """构建优化权重：v6b 占 50%，新因子占 50%（按 IC_IR 归一化）"""
    v6b = STRATEGY_PROFILES['v6b_8f_pos_ic']

    # 新因子权重（按 |IC_IR| 归一化）
    new_weights = {}
    total_ir = sum(abs(ic_results[f]['ic_ir']) for f in kept_factors if f not in v6b.factor_weights)
    for f in kept_factors:
        if f not in v6b.factor_weights:
            ir = ic_results[f]['ic_ir']
            new_weights[f] = abs(ir) / total_ir if total_ir > 0 else 0

    # 合并：v6b 50% + 新因子 50%
    combined = {}
    for f, w in v6b.factor_weights.items():
        combined[f] = w * v6b_weight
    for f, w in new_weights.items():
        combined[f] = w * (1 - v6b_weight)

    return combined

def run_bt_quick(close_panel, score, label='default'):
    """轻量回测"""
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
    print("新因子去冗余 + 权重优化")
    print("=" * 70)

    print("\n[1/5] 加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  {close_panel.shape[0]} 天 × {len(stocks)} 只")

    print("\n[2/5] 计算全部因子...")
    all_factors = calc_all_factors(close_panel, volume_panel, amount_panel)
    print(f"  总因子数: {len(all_factors)} (旧 29 + 新 15)")

    print("\n[3/5] IC 分析...")
    ic_results = calc_ic_fast(all_factors, close_panel)
    print(f"  有 IC 结果的因子: {len(ic_results)} 个")
    print(f"\n  {'因子':<25} {'IC':>8} {'IC_IR':>8}")
    print(f"  {'─'*43}")
    for fname in sorted(ic_results.keys(), key=lambda x: abs(ic_results[x]['ic_ir']), reverse=True):
        r = ic_results[fname]
        sig = '***' if abs(r['ic_ir']) > 0.1 else '**' if abs(r['ic_ir']) > 0.05 else '*' if abs(r['ic_ir']) > 0.02 else ''
        tag = '(新)' if fname not in DEFAULT_FACTOR_WEIGHTS else '(旧)'
        print(f"  {fname:<25} {r['ic_mean']:>+8.4f} {r['ic_ir']:>+8.4f} {tag} {sig}")

    print("\n[4/5] 去冗余...")
    kept = deduplicate_factors(all_factors, ic_results, corr_threshold=0.7)

    print("\n[5/5] 回测...")
    results = {}

    # v6b 基准
    print("  ▶ v6b 基准...", end=" ", flush=True)
    t0 = time.time()
    v6b = STRATEGY_PROFILES['v6b_8f_pos_ic']
    vf = {k: v for k, v in all_factors.items() if k in v6b.factor_weights}
    results['v6b'] = run_bt_quick(close_panel, composite_score(vf, v6b.factor_weights), 'v6b')
    print(f"({time.time()-t0:.0f}s)  {results['v6b']['annual_return']:.2%}  Sharpe={results['v6b']['sharpe_ratio']:.2f}")

    # 方案 A：去冗余新因子（不含 v6b）
    new_kept = [f for f in kept if f not in v6b.factor_weights]
    if new_kept:
        new_ir_total = sum(abs(ic_results[f]['ic_ir']) for f in new_kept)
        new_weights = {f: abs(ic_results[f]['ic_ir']) / new_ir_total for f in new_kept}
        # 负 IC 因子取负权重
        for f in new_weights:
            if ic_results[f]['ic_mean'] < 0:
                new_weights[f] *= -1

        new_f = {k: v for k, v in all_factors.items() if k in new_weights}

        print(f"  ▶ 仅去冗余新因子 ({len(new_kept)}个)...", end=" ", flush=True)
        t0 = time.time()
        results['new_only'] = run_bt_quick(close_panel, composite_score(new_f, new_weights), 'new_only')
        print(f"({time.time()-t0:.0f}s)  {results['new_only']['annual_return']:.2%}  Sharpe={results['new_only']['sharpe_ratio']:.2f}")

    # 方案 B：v6b 50% + 去冗余新因子 50%
    if new_kept:
        opt_weights = build_optimal_weights(ic_results, kept, v6b_weight=0.5)
        opt_f = {k: v for k, v in all_factors.items() if k in opt_weights}

        print(f"  ▶ v6b 50% + 新因子 50% ({len(opt_weights)}个)...", end=" ", flush=True)
        t0 = time.time()
        results['v6b_new_50_50'] = run_bt_quick(close_panel, composite_score(opt_f, opt_weights), 'v6b_new_50_50')
        print(f"({time.time()-t0:.0f}s)  {results['v6b_new_50_50']['annual_return']:.2%}  Sharpe={results['v6b_new_50_50']['sharpe_ratio']:.2f}")

    # 方案 C：全部去冗余因子（含 v6b 和新因子一起，纯 IC_IR 加权）
    all_ir_total = sum(abs(ic_results[f]['ic_ir']) for f in kept)
    all_weights = {}
    for f in kept:
        ir = ic_results[f]['ic_ir']
        all_weights[f] = abs(ir) / all_ir_total if all_ir_total > 0 else 0
        if ic_results[f]['ic_mean'] < 0:
            all_weights[f] *= -1

    all_f = {k: v for k, v in all_factors.items() if k in all_weights}

    print(f"  ▶ 全部去冗余 IC_IR 加权 ({len(kept)}个)...", end=" ", flush=True)
    t0 = time.time()
    results['all_icir'] = run_bt_quick(close_panel, composite_score(all_f, all_weights), 'all_icir')
    print(f"({time.time()-t0:.0f}s)  {results['all_icir']['annual_return']:.2%}  Sharpe={results['all_icir']['sharpe_ratio']:.2f}")

    # 对比
    labels = list(results.keys())
    print(f"\n{'':>25}", end='')
    for l in labels: print(f" {l:>14}", end='')
    print()
    print("─" * (25 + 15 * len(labels)))
    for k in ['annual_return', 'sharpe_ratio', 'max_drawdown', 'calmar_ratio', 'win_rate', 'total_cost']:
        print(f"{k:>25}", end='')
        for l in labels:
            v = results[l][k]
            if k in ('annual_return', 'max_drawdown'): print(f" {v:>13.2%}", end='')
            elif k == 'total_cost': print(f" ¥{v:>12,.0f}", end='')
            else: print(f" {v:>14.4f}", end='')
        print()

    out_path = os.path.join(DATA_DIR, "backtest_results", "dedup_factor_test.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump({'ic': ic_results, 'kept': kept, 'weights': {k: round(v, 4) for k, v in all_weights.items()},
                   'results': results}, f, indent=2)
    print(f"\n结果已保存: {out_path}")

if __name__ == "__main__":
    main()
