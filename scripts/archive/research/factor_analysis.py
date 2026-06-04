#!/usr/bin/env python3
"""
因子深度分析 + 优化方案
======================
1. 计算所有因子的 IC 截面均值
2. 因子相关性矩阵 → 找冗余因子
3. 因子分组分析（动量/反转/波动率/量价/趋势）
4. 提出优化后的因子权重
"""
import sys, os, time, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

import numpy as np
import pandas as pd

from core.factors import calc_factors_panel
from core.scoring import standardize, factor_correlation
from core.config import DEFAULT_FACTOR_WEIGHTS

# ── 数据加载 ──────────────────────────────────────────────────────
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

# ── IC 计算 ────────────────────────────────────────────────────────
def calc_ic(factors, close_panel, forward_period=20):
    """计算每个因子的 IC（信息系数）"""
    # 未来收益
    future_ret = close_panel.pct_change(forward_period).shift(-forward_period)
    
    ic_results = {}
    for fname, fdf in factors.items():
        # 对齐日期
        common_idx = fdf.index.intersection(future_ret.index)
        if len(common_idx) < 50:
            continue
        
        ic_vals = []
        for date in common_idx:
            f_row = fdf.loc[date].dropna()
            r_row = future_ret.loc[date].dropna()
            common_cols = f_row.index.intersection(r_row.index)
            if len(common_cols) < 10:
                continue
            f_vals = f_row[common_cols].values
            r_vals = r_row[common_cols].values
            # Spearman IC
            from scipy.stats import spearmanr
            try:
                corr, _ = spearmanr(f_vals, r_vals)
                if not np.isnan(corr):
                    ic_vals.append(corr)
            except:
                pass
        
        if len(ic_vals) > 10:
            ic_mean = np.mean(ic_vals)
            ic_std = np.std(ic_vals)
            ic_ir = ic_mean / (ic_std + 1e-10)
            ic_results[fname] = {
                'ic_mean': round(float(ic_mean), 6),
                'ic_std': round(float(ic_std), 4),
                'ic_ir': round(float(ic_ir), 4),
                'n_obs': len(ic_vals),
            }
    
    return ic_results

# ── 因子分组 ──────────────────────────────────────────────────────
FACTOR_GROUPS = {
    'momentum': ['mom_5', 'mom_10', 'mom_20', 'mom_60', 'mom_120'],
    'reversal': ['rev_3', 'rev_5', 'rev_10'],
    'volatility': ['vol_10', 'vol_20', 'vol_60', 'vol_change'],
    'volume': ['vol_ratio_5', 'vol_ratio_20', 'amount_ratio'],
    'rsi': ['rsi_6', 'rsi_14', 'rsi_28'],
    'macd': ['macd_12_26', 'macd_5_35'],
    'bollinger': ['boll_pos_10', 'boll_pos_20', 'boll_width_20'],
    'atr': ['atr_14'],
    'distribution': ['skew_20', 'kurt_20'],
    'vwap': ['vwap_mom'],
    'rel_strength': ['rel_strength_20', 'rel_strength_60'],
}

# ── 主流程 ────────────────────────────────────────────────────────
if __name__ == "__main__":
    t0 = time.time()
    print("=" * 70)
    print("因子深度分析")
    print("=" * 70)
    
    # 1. 加载数据
    print("\n[1/5] 加载数据...")
    (close_panel, volume_panel, amount_panel), stocks = load_panel()
    print(f"  Panel: {len(close_panel)} 天 × {len(stocks)} 只股票")
    
    # 2. 计算因子
    print("\n[2/5] 计算因子面板...")
    factors = calc_factors_panel(close_panel, volume_panel, amount_panel)
    print(f"  共 {len(factors)} 个因子")
    
    # 3. IC 分析
    print("\n[3/5] IC 分析（20日前瞻收益）...")
    ic_results = calc_ic(factors, close_panel, forward_period=20)
    
    print(f"\n  {'因子':<20} {'IC均值':>10} {'IC标准差':>10} {'IC_IR':>10} {'N':>6}")
    print(f"  {'─'*56}")
    for fname in sorted(ic_results.keys(), key=lambda x: abs(ic_results[x]['ic_ir']), reverse=True):
        r = ic_results[fname]
        sig = '***' if abs(r['ic_ir']) > 0.05 else '**' if abs(r['ic_ir']) > 0.03 else '*' if abs(r['ic_ir']) > 0.02 else ''
        print(f"  {fname:<20} {r['ic_mean']:>+10.4f} {r['ic_std']:>10.4f} {r['ic_ir']:>+10.4f} {r['n_obs']:>6} {sig}")
    
    # 4. 因子分组 IC
    print(f"\n[4/5] 因子分组分析...")
    print(f"\n  {'分组':<15} {'因子数':>6} {'平均|IC_IR|':>12} {'最大|IC_IR|':>12} {'代表因子'}")
    print(f"  {'─'*65}")
    for group_name, group_factors in FACTOR_GROUPS.items():
        group_ic = [ic_results[f]['ic_ir'] for f in group_factors if f in ic_results]
        if group_ic:
            avg_abs_ir = np.mean(np.abs(group_ic))
            max_abs_ir = np.max(np.abs(group_ic))
            best_f = group_factors[np.argmax([abs(ic_results[f]['ic_ir']) if f in ic_results else 0 for f in group_factors])]
            print(f"  {group_name:<15} {len(group_factors):>6} {avg_abs_ir:>12.4f} {max_abs_ir:>12.4f} {best_f}")
    
    # 5. 因子相关性
    print(f"\n[5/5] 因子相关性分析...")
    corr_matrix, redundant = factor_correlation(factors)
    
    if redundant:
        print(f"\n  高相关因子对 (|ρ| > 0.8):")
        for fa, fb, c in redundant[:15]:
            print(f"    {fa:<20} ↔ {fb:<20} ρ = {c:>+.4f}")
    else:
        print("  无高相关因子对")
    
    # 6. 优化建议
    print(f"\n{'='*70}")
    print("优化建议")
    print(f"{'='*70}")
    
    # 6a. 低 IC 因子（考虑删除）
    low_ic = [(f, r) for f, r in ic_results.items() if abs(r['ic_ir']) < 0.02]
    low_ic.sort(key=lambda x: abs(x[1]['ic_ir']))
    if low_ic:
        print(f"\n  ⚠️  低 IC 因子（|IC_IR| < 0.02，考虑删除或降权）:")
        for f, r in low_ic:
            print(f"    {f:<20} IC={r['ic_mean']:>+.4f}  IC_IR={r['ic_ir']:>+.4f}")
    
    # 6b. 高 IC 因子（建议增权）
    high_ic = [(f, r) for f, r in ic_results.items() if abs(r['ic_ir']) > 0.05]
    high_ic.sort(key=lambda x: abs(x[1]['ic_ir']), reverse=True)
    if high_ic:
        print(f"\n  ✅ 高 IC 因子（|IC_IR| > 0.05，建议增权）:")
        for f, r in high_ic:
            print(f"    {f:<20} IC={r['ic_mean']:>+.4f}  IC_IR={r['ic_ir']:>+.4f}")
    
    # 6c. 冗余因子对
    if redundant:
        print(f"\n  🔄 冗余因子对（建议每组只保留一个）:")
        shown = set()
        for fa, fb, c in redundant:
            if fa not in shown and fb not in shown:
                ir_a = abs(ic_results.get(fa, {}).get('ic_ir', 0))
                ir_b = abs(ic_results.get(fb, {}).get('ic_ir', 0))
                keep = fa if ir_a >= ir_b else fb
                drop = fb if keep == fa else fa
                print(f"    保留 {keep} (|IC_IR|={max(ir_a,ir_b):.4f}), 删除 {drop} (|IC_IR|={min(ir_a,ir_b):.4f})")
                shown.add(fa)
                shown.add(fb)
    
    # 6d. 构建优化权重
    print(f"\n  📊 建议的 IC_IR 权重（删除低IC+冗余后）:")
    
    # 要删除的因子
    to_drop = set()
    # 低 IC
    for f, r in low_ic:
        to_drop.add(f)
    # 冗余（保留 IC 高的）
    for fa, fb, c in redundant:
        ir_a = abs(ic_results.get(fa, {}).get('ic_ir', 0))
        ir_b = abs(ic_results.get(fb, {}).get('ic_ir', 0))
        to_drop.add(fb if ir_a >= ir_b else fa)
    
    # 构建权重
    valid_factors = {f: r for f, r in ic_results.items() if f not in to_drop}
    total_abs_ir = sum(abs(r['ic_ir']) for r in valid_factors.values())
    
    opt_weights = {}
    for f, r in valid_factors.items():
        sign = 1.0 if r['ic_mean'] >= 0 else -1.0
        opt_weights[f] = round(sign * abs(r['ic_ir']) / total_abs_ir, 6)
    
    print(f"\n    保留 {len(opt_weights)} 个因子，删除 {len(to_drop)} 个")
    print(f"    {'因子':<20} {'权重':>10}  {'IC_IR':>10}")
    print(f"    {'─'*42}")
    for f, w in sorted(opt_weights.items(), key=lambda x: abs(x[1]), reverse=True):
        ir = ic_results[f]['ic_ir']
        bar = '█' * int(abs(w) * 100)
        print(f"    {f:<20} {w:>+10.4f}  {ir:>+10.4f}  {bar}")
    
    if to_drop:
        print(f"\n    删除的因子: {sorted(to_drop)}")
    
    # 保存结果
    out = {
        'ic_results': ic_results,
        'redundant_pairs': [(a, b, c) for a, b, c in redundant],
        'optimized_weights': opt_weights,
        'dropped_factors': sorted(to_drop),
        'n_factors_original': len(factors),
        'n_factors_optimized': len(opt_weights),
    }
    out_path = os.path.join(DATA_DIR, "backtest_results", "factor_analysis.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    
    elapsed = time.time() - t0
    print(f"\n完成 ({elapsed:.1f}s)")
    print(f"结果已保存: {out_path}")
