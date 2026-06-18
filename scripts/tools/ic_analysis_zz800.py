"""
IC_IR 分析脚本 — 在715只中证800股票上计算所有因子的截面IC/IR

用法:
  python scripts/ic_analysis_zz800.py [--output ic_results.csv]
"""

import sys, os

import numpy as np
import pandas as pd
from pathlib import Path

DATA_DIR = Path(os.environ.get('BACKTEST_DATA_DIR', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')))
DAILY_DIR = DATA_DIR / 'daily'
CONSTITUENTS_CSV = Path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)), 'data', 'zz800_constituents.csv')))

# ── 加载选股池 ─────────────────────────────────────────────────
def load_zz800_codes():
    """加载中证800成分股代码（去重）"""
    df = pd.read_csv(CONSTITUENTS_CSV, dtype={'code': str})
    # 去重（AKShare接口有重复）
    codes = sorted(df['code'].unique().tolist())
    codes = [str(c) for c in codes]  # 确保是字符串
    # 排除科创板(688开头)
    codes = [c for c in codes if not c.startswith('688')]
    # 排除ST
    if 'name' in df.columns:
        name_map = dict(zip(df['code'].astype(str), df['name']))
        codes = [c for c in codes if 'ST' not in str(name_map.get(c, ''))]
    return codes

# ── 加载日线面板 ───────────────────────────────────────────────
def load_panel(codes, min_days=100):
    """加载日线数据，返回 close_panel, volume_panel, amount_panel, high_panel, low_panel"""
    data = {}
    for c in codes:
        f = DAILY_DIR / f"{c}.csv"
        if not f.exists():
            continue
        try:
            df = pd.read_csv(f, index_col='date', parse_dates=True)
            if len(df) >= min_days and 'close' in df.columns:
                data[c] = df
        except Exception:
            continue
    
    if not data:
        print("ERROR: 没有加载到任何股票数据")
        return None, None, None, None, None
    
    # 构建统一日期索引
    all_dates = sorted(set().union(*[df.index for df in data.values()]))
    
    close_panel = pd.DataFrame({c: data[c]['close'] for c in data}, index=all_dates)
    volume_panel = pd.DataFrame({c: data[c]['volume'] for c in data}, index=all_dates)
    amount_panel = pd.DataFrame(
        {c: data[c].get('amount', data[c]['close'] * data[c]['volume']) for c in data},
        index=all_dates
    )
    high_panel = pd.DataFrame({c: data[c]['high'] for c in data if 'high' in data[c].columns}, index=all_dates)
    low_panel = pd.DataFrame({c: data[c]['low'] for c in data if 'low' in data[c].columns}, index=all_dates)
    
    print(f"Panel: {len(all_dates)} 天 × {len(data)} 只股票")
    print(f"日期范围: {all_dates[0].strftime('%Y-%m-%d')} ~ {all_dates[-1].strftime('%Y-%m-%d')}")
    
    return close_panel, volume_panel, amount_panel, high_panel, low_panel

# ── 因子面板计算 ───────────────────────────────────────────────
def calc_factor_panels(close_panel, volume_panel, amount_panel, high_panel=None, low_panel=None):
    """计算所有因子的面板"""
    returns = close_panel.pct_change()
    eps = 1e-10
    
    factors = {}
    
    # Momentum
    for w in [5, 10, 20, 60, 120]:
        factors[f'mom_{w}'] = close_panel / close_panel.shift(w) - 1
    
    # Reversal
    for w in [3, 5, 10]:
        factors[f'rev_{w}'] = -(close_panel / close_panel.shift(w) - 1)
    
    # Volatility
    for w in [10, 20, 60]:
        factors[f'vol_{w}'] = returns.rolling(w).std()
    
    # Volatility change
    vol_20 = returns.rolling(20).std()
    vol_60 = returns.rolling(60).std()
    factors['vol_change'] = vol_20 / (vol_60 + eps)
    
    # Volume ratio
    vol5_mean = volume_panel.rolling(5).mean()
    vol20_mean = volume_panel.rolling(20).mean()
    amt20_mean = amount_panel.rolling(20).mean()
    factors['vol_ratio_5'] = volume_panel / (vol5_mean + eps)
    factors['vol_ratio_20'] = volume_panel / (vol20_mean + eps)
    factors['amount_ratio'] = amount_panel / (amt20_mean + eps)
    
    # RSI
    for w in [6, 14]:
        delta = close_panel.diff()
        gain = delta.clip(lower=0).rolling(w).mean()
        loss = (-delta.clip(upper=0)).rolling(w).mean()
        rs = gain / (loss + eps)
        factors[f'rsi_{w}'] = 100 - 100 / (1 + rs)
    
    # Bollinger position
    for w in [10, 20]:
        ma = close_panel.rolling(w).mean()
        std = close_panel.rolling(w).std()
        factors[f'boll_pos_{w}'] = (close_panel - ma) / (std + eps)
    
    # High-Low Range
    if high_panel is not None and low_panel is not None and not high_panel.empty and not low_panel.empty:
        factors['high_low_range'] = (high_panel - low_panel) / (close_panel + eps)
    
    return factors

# ── IC 计算 ────────────────────────────────────────────────────
def calc_ic_series(factor_panel, fwd_ret):
    """计算单个因子的IC序列"""
    ics = []
    for dt in factor_panel.index:
        if dt not in fwd_ret.index:
            continue
        fv = factor_panel.loc[dt].dropna()
        rv = fwd_ret.loc[dt].dropna()
        common = fv.index.intersection(rv.index)
        if len(common) < 10:
            continue
        corr = np.corrcoef(fv[common], rv[common])[0, 1]
        if not np.isnan(corr):
            ics.append(corr)
    return ics

def calc_ic_ir(factor_panel, fwd_ret, label=""):
    """计算IC/IR指标"""
    ics = calc_ic_series(factor_panel, fwd_ret)
    if len(ics) < 5:
        return None
    ic = np.mean(ics)
    ir = ic / np.std(ics) if np.std(ics) > 0 else 0
    pos_pct = sum(1 for x in ics if x > 0) / len(ics)
    return {
        'factor': label,
        'IC': round(ic, 6),
        'IR': round(ir, 4),
        'IC_std': round(np.std(ics), 6),
        'pos_pct': round(pos_pct, 3),
        'N': len(ics)
    }

# ── 主流程 ─────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description='IC_IR Analysis on ZZ800')
    parser.add_argument('--output', default=None, help='输出CSV路径')
    parser.add_argument('--min-days', type=int, default=100, help='最少交易日数')
    args = parser.parse_args()
    
    print("=" * 60)
    print("IC_IR 分析 — 中证800选股池")
    print("=" * 60)
    
    # 1. 加载选股池
    codes = load_zz800_codes()
    print(f"\n选股池: {len(codes)} 只 (中证800去重去科创去ST)")
    
    # 2. 加载面板
    print(f"\n加载日线数据 ({DAILY_DIR})...")
    result = load_panel(codes, min_days=args.min_days)
    if result[0] is None:
        return
    close_panel, volume_panel, amount_panel, high_panel, low_panel = result
    
    # 3. 计算因子面板
    print("\n计算因子面板...")
    factors = calc_factor_panels(close_panel, volume_panel, amount_panel, high_panel, low_panel)
    print(f"因子数量: {len(factors)}")
    
    # 4. 计算前向收益
    print("\n计算截面IC/IR...")
    results = []
    
    for period_name, period in [('5d', 5), ('10d', 10), ('20d', 20)]:
        fwd_ret = close_panel.shift(-period) / close_panel - 1
        
        print(f"\n--- 持有期 {period_name} ---")
        period_results = []
        
        for fname, fpanel in factors.items():
            r = calc_ic_ir(fpanel, fwd_ret, fname)
            if r:
                r['period'] = period_name
                period_results.append(r)
                results.append(r)
        
        # 按IR排序打印
        period_results.sort(key=lambda x: abs(x['IR']), reverse=True)
        print(f"{'因子':>20} | {'IC':>8} | {'IR':>7} | {'IC_std':>8} | {'+%':>5} | {'N':>5}")
        print("-" * 65)
        for r in period_results:
            sig = "✅" if abs(r['IR']) > 0.1 else ("⚠️" if abs(r['IR']) > 0.05 else "❌")
            print(f"{r['factor']:>20} | {r['IC']:>+8.4f} | {r['IR']:>+7.4f} | {r['IC_std']:>8.4f} | {r['pos_pct']:>5.1%} | {r['N']:>5} {sig}")
    
    # 5. 汇总
    print("\n" + "=" * 60)
    print("汇总：各因子在不同持有期下的IR")
    print("=" * 60)
    
    # 透视表
    ir_data = {}
    for r in results:
        fname = r['factor']
        if fname not in ir_data:
            ir_data[fname] = {}
        ir_data[fname][r['period']] = r['IR']
    
    # 按20d IR排序
    sorted_factors = sorted(ir_data.keys(), key=lambda x: abs(ir_data[x].get('20d', 0)), reverse=True)
    
    print(f"\n{'因子':>20} | {'IR(5d)':>8} | {'IR(10d)':>8} | {'IR(20d)':>8} | {'平均|IR|':>8}")
    print("-" * 65)
    for fname in sorted_factors:
        ir5 = ir_data[fname].get('5d', 0)
        ir10 = ir_data[fname].get('10d', 0)
        ir20 = ir_data[fname].get('20d', 0)
        avg_ir = (abs(ir5) + abs(ir10) + abs(ir20)) / 3
        sig = "✅" if avg_ir > 0.1 else ("⚠️" if avg_ir > 0.05 else "❌")
        print(f"{fname:>20} | {ir5:>+8.4f} | {ir10:>+8.4f} | {ir20:>+8.4f} | {avg_ir:>8.4f} {sig}")
    
    # 6. 保存结果
    if args.output:
        df = pd.DataFrame(results)
        df.to_csv(args.output, index=False)
        print(f"\n结果已保存: {args.output}")
    
    return results

if __name__ == '__main__':
    main()
