#!/usr/bin/env python3
"""
scripts/tools/v58a_ic_analysis.py — v58a 因子 IC/IR 分析
=========================================================
逐个因子 × 多个持有期(1/3/5/10/20天) 的 Rank IC 分析
标准: |IC Mean| > 0.03 且 |IR| > 0.3 → 有效
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from core.db import load_panel_from_db
from scripts.strategies.v58a_breakout import calc_breakout_factors


def calc_ic(factor_panel, fwd_ret_panel, dates):
    """逐日计算 Rank IC (spearman)"""
    ics = []
    for date in dates:
        if date not in factor_panel.index or date not in fwd_ret_panel.index:
            continue
        f = factor_panel.loc[date].dropna()
        r = fwd_ret_panel.loc[date].dropna()
        common = f.index.intersection(r.index)
        if len(common) < 30:
            continue
        ic = f.loc[common].corr(r.loc[common], method='spearman')
        ics.append(ic)
    return pd.Series(ics)


def main():
    print("=" * 60)
    print("v58a 因子 IC 分析")
    print("=" * 60)
    
    start_date = '2021-01-01'
    end_date = '2026-06-24'
    
    print(f"\n加载数据: {start_date} ~ {end_date}, pool=zz1800")
    tpl, codes = load_panel_from_db(start_date=start_date, end_date=end_date,
                                     pool='zz1800', need_open=True, need_hl=True)
    cp, vp, ap, op, hp, lp = tpl
    
    print(f"Panel: {cp.shape[0]} 天 × {cp.shape[1]} 只")
    
    # 计算因子
    print("\n计算 v58a 因子...")
    factors = calc_breakout_factors(cp, vp, ap, hp, lp, op)
    factor_names = list(factors.keys())
    print(f"因子: {factor_names}")
    
    # 检查每个因子的完整度
    print("\n因子完整度:")
    for name, panel in factors.items():
        non_null_dates = panel.notna().any(axis=1).sum()
        print(f"  {name:25s}: {non_null_dates}/{len(panel)} 天有数据")
    
    # 多个持有期的 IC 分析
    holding_periods = [1, 3, 5, 10, 20]
    
    print("\n" + "=" * 60)
    print("IC 分析结果")
    print("=" * 60)
    
    results = []
    
    for h in holding_periods:
        # 前h日收益率
        fwd_ret = cp.pct_change(h).shift(-h)
        
        print(f"\n--- 持有 {h} 天 ---")
        for fname in factor_names:
            ic_series = calc_ic(factors[fname], fwd_ret, cp.index)
            
            if len(ic_series) == 0:
                print(f"  {fname:25s}: 无数据")
                continue
            
            ic_mean = ic_series.mean()
            ic_std = ic_series.std()
            ic_ir = ic_mean / ic_std if ic_std > 0 else 0
            pct_pos = (ic_series > 0).mean()
            
            # 判断
            if abs(ic_mean) > 0.03 and abs(ic_ir) > 0.3:
                sig = "✅ 有效"
            elif abs(ic_mean) > 0.01:
                sig = "⚠️ 微弱"
            else:
                sig = "❌ 无效"
            
            print(f"  {fname:25s}: IC={ic_mean:+.4f}  IR={ic_ir:+.4f}  P(>0)={pct_pos:.1%}  {sig}")
            
            results.append({
                'factor': fname, 'holding': h,
                'ic_mean': ic_mean, 'ic_ir': ic_ir,
                'pct_positive': pct_pos, 'n_days': len(ic_series)
            })
    
    # 汇总有效因子
    df = pd.DataFrame(results)
    valid = df[(df['ic_mean'].abs() > 0.03) & (df['ic_ir'].abs() > 0.3)]
    
    print("\n" + "=" * 60)
    print("有效因子汇总 (|IC|>0.03 & |IR|>0.3)")
    print("=" * 60)
    if len(valid) == 0:
        print("  ⚠️ 无有效因子 — v58a 方向可能不适用")
        # 退而求其次看微弱
        weak = df[(df['ic_mean'].abs() > 0.01)].sort_values('ic_mean', key=abs, ascending=False)
        if len(weak) > 0:
            print("\n  微弱因子 (|IC|>0.01):")
            for _, row in weak.iterrows():
                print(f"    {row['factor']:25s} (h={row['holding']}): IC={row['ic_mean']:+.4f}")
    else:
        for _, row in valid.iterrows():
            direction = "正" if row['ic_mean'] > 0 else "负"
            print(f"  {row['factor']:25s} (h={row['holding']}): IC={row['ic_mean']:+.4f}  IR={row['ic_ir']:+.4f}  方向={direction}")
    
    print("\n完成!")


if __name__ == "__main__":
    main()
