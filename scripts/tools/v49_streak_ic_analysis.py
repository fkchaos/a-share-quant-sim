#!/usr/bin/env python3
"""
scripts/tools/v49_streak_ic_analysis.py — v49 连板因子 IC 分析
计算连板辨识度因子的截面 IC/IR，验证其独立预测能力。
"""
import sys
import os
import warnings
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

warnings.filterwarnings('ignore', category=UserWarning)
warnings.filterwarnings('ignore', message='.*constant.*')

import pandas as pd
import numpy as np
from core.db import load_panel_from_db
from core.streak_factor import compute_streak_factor


def compute_ic(panels, forward_days=5, decay_days=252):
    """
    计算连板辨识度因子的 IC（截面秩相关）
    
    参数:
        panels: (close_panel, volume_panel, amount_panel, ...) tuple
        forward_days: 未来收益计算天数
        decay_days: 连板因子衰减窗口
    
    返回:
        {date: ic_value} 字典
    """
    close_panel = panels[0]
    
    # 随机采样1000只股票加速
    np.random.seed(42)
    sample_codes = np.random.choice(close_panel.columns, size=1000, replace=False)
    close_sample = close_panel[sample_codes]
    
    # 计算未来 N 日收益
    future_ret = close_sample.shift(-forward_days) / close_sample - 1
    
    ic_series = {}
    
    # 逐日计算 IC（每10天采样一次）
    dates = close_sample.index[::10]
    
    for date in dates:
        if date not in future_ret.index:
            continue
        
        # 计算当日连板因子（用采样后的子集）
        try:
            sub_panels = (close_sample, panels[1][sample_codes] if len(panels) > 1 else None)
            streak = compute_streak_factor(sub_panels, date=date, decay_days=decay_days)
        except Exception as e:
            continue
        
        # 当日未来收益
        ret = future_ret.loc[date]
        
        # 对齐
        common = streak.index.intersection(ret.index)
        common = common[streak[common].notna() & ret[common].notna()]
        
        if len(common) < 50:
            continue
        
        s = streak[common]
        r = ret[common]
        
        # Spearman 秩相关（处理常数数组异常）
        try:
            ic = s.rank().corr(r.rank(), method='spearman')
        except Exception:
            continue
        if np.isnan(ic):
            continue
        ic_series[date] = ic
    
    return ic_series


def main():
    print("=" * 60)
    print("v49 连板因子 IC 分析")
    print("=" * 60)
    
    # 加载数据
    print("\n加载 zz1800 K线数据...", flush=True)
    panels, codes = load_panel_from_db(pool='zz1800', start_date='2022-01-01')
    close_panel = panels[0]
    print(f"  {len(codes)} 只股票, {len(close_panel.index)} 个交易日", flush=True)
    
    results = {}
    
    for fwd in [1, 3, 5, 10]:
        print(f"\n── 前向 {fwd} 日收益 ──", flush=True)
        ic = compute_ic(panels, forward_days=fwd, decay_days=252)
        
        if not ic:
            print(f"  无有效数据", flush=True)
            continue
        
        ic_vals = pd.Series(ic)
        ic_mean = ic_vals.mean()
        ic_std = ic_vals.std()
        ir = ic_mean / ic_std if ic_std > 0 else 0
        pct_positive = (ic_vals > 0).mean() * 100
        
        results[fwd] = {
            'IC Mean': ic_mean,
            'IC Std': ic_std,
            'IR': ir,
            '正IC占比': pct_positive,
            'N': len(ic_vals)
        }
        
        print(f"  IC Mean: {ic_mean:.4f}", flush=True)
        print(f"  IC Std:  {ic_std:.4f}", flush=True)
        print(f"  IR:      {ir:.4f}", flush=True)
        print(f"  正IC占比: {pct_positive:.1f}%", flush=True)
        print(f"  样本数:  {len(ic_vals)}", flush=True)
    
    # 分年度
    if results:
        print(f"\n── 分年度 IC ──", flush=True)
        for fwd in [5]:
            ic = compute_ic(panels, forward_days=fwd, decay_days=252)
            ic_vals = pd.Series(ic)
            for year in sorted(set(d.year for d in ic_vals.index)):
                year_ic = ic_vals[ic_vals.index.year == year]
                if len(year_ic) > 5:
                    ym = year_ic.mean()
                    yr = year_ic.mean() / year_ic.std() if year_ic.std() > 0 else 0
                    print(f"  {year}: IC={ym:.4f}, IR={yr:.4f}, N={len(year_ic)}", flush=True)
    
    # 结论
    print(f"\n{'=' * 60}")
    ic5 = results.get(5, {}).get('IC Mean', 0)
    ir5 = results.get(5, {}).get('IR', 0)
    if abs(ic5) > 0.03 and abs(ir5) > 0.3:
        print(f"✅ 连板因子有效: IC={ic5:.4f}, IR={ir5:.4f} → 继续 WF")
    elif abs(ic5) > 0.01:
        print(f"⚠️ 连板因子微弱: IC={ic5:.4f}, IR={ir5:.4f} → 不值得独立成策略")
    else:
        print(f"❌ 连板因子证伪: IC={ic5:.4f}, IR={ir5:.4f} → 放弃")
    print(f"{'=' * 60}", flush=True)


if __name__ == '__main__':
    main()
