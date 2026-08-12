#!/usr/bin/env python3
"""v77 隔夜收益率因子 IC分析"""
import sys
sys.path.insert(0, "/root/a-share-quant-sim")

import numpy as np
import pandas as pd
from core.db import load_panel_from_db
from scripts.strategies.v77_overnight_return import calc_factors_v77

def run_ic_analysis():
    """计算IC、IR、分regime IC"""
    print("=" * 60)
    print("v77 隔夜收益率因子 IC分析")
    print("=" * 60)
    
    # 加载数据
    print("\n[1] 加载数据...")
    panels, codes = load_panel_from_db(
        start_date='2020-01-01', end_date='2026-06-30',
        need_open=True, need_hl=True, pool='zz1800'
    )
    close, vol, amt, opn, high, low = panels
    print(f"  数据: {close.shape[0]}天 x {close.shape[1]}只股票")
    
    # 计算未来5日收益
    print("\n[2] 计算未来5日收益...")
    fwd_ret = close.pct_change(5).shift(-5)  # 未来5日收益
    
    # 逐日计算因子值和IC
    print("\n[3] 逐日计算IC...")
    dates = close.index
    ic_list = []
    
    for i in range(30, len(dates) - 5):  # 跳过前30天（MA需要）和后5天（fwd_ret需要）
        date = dates[i]
        
        # 截取数据到当前日期
        close_slice = close.iloc[:i+1]
        vol_slice = vol.iloc[:i+1]
        amt_slice = amt.iloc[:i+1]
        opn_slice = opn.iloc[:i+1]
        high_slice = high.iloc[:i+1]
        low_slice = low.iloc[:i+1]
        
        # 计算因子
        factors = calc_factors_v77(close_slice, vol_slice, amt_slice, 
                                   high_slice, low_slice, opn_slice)
        factor_values = factors['v77_overnight']
        
        # 计算未来收益
        future_ret = fwd_ret.iloc[i]
        
        # 对齐
        common_stocks = factor_values.index.intersection(future_ret.dropna().index)
        if len(common_stocks) < 100:
            continue
        
        f = factor_values[common_stocks].values
        r = future_ret[common_stocks].values
        
        # Spearman IC
        from scipy.stats import spearmanr
        ic, _ = spearmanr(f, r)
        
        if not np.isnan(ic):
            ic_list.append({'date': date, 'ic': ic})
    
    ic_df = pd.DataFrame(ic_list)
    ic_df.set_index('date', inplace=True)
    
    # 基本统计
    print("\n[4] IC统计:")
    ic_mean = ic_df['ic'].mean()
    ic_std = ic_df['ic'].std()
    ir = ic_mean / ic_std if ic_std > 0 else 0
    ic_positive_pct = (ic_df['ic'] > 0).mean()
    
    print(f"  IC均值: {ic_mean:.4f}")
    print(f"  IC标准差: {ic_std:.4f}")
    print(f"  IR (IC均值/IC标准差): {ir:.4f}")
    print(f"  IC>0比例: {ic_positive_pct:.2%}")
    
    # 判定
    print("\n[5] 判定:")
    if abs(ic_mean) > 0.03 and abs(ir) > 0.3:
        print("  ✅ 有效因子，进入WF验证")
        verdict = "PASS"
    elif abs(ic_mean) < 0.01 or abs(ir) < 0.1:
        print("  ❌ 证伪，不进入WF")
        verdict = "FAIL"
    else:
        print("  ⚠️ 微弱信号，不值得投入WF时间")
        verdict = "MARGINAL"
    
    # 分年IC
    print("\n[6] 分年IC:")
    ic_df['year'] = ic_df.index.year
    yearly_ic = ic_df.groupby('year')['ic'].agg(['mean', 'std'])
    yearly_ic['ir'] = yearly_ic['mean'] / yearly_ic['std']
    print(yearly_ic.to_string())
    
    # IC衰减分析
    print("\n[7] IC衰减分析（近12个月）:")
    recent_12m = ic_df[ic_df.index >= '2025-07-01']
    if len(recent_12m) > 0:
        recent_ic_mean = recent_12m['ic'].mean()
        recent_ir = recent_12m['ic'].mean() / recent_12m['ic'].std() if recent_12m['ic'].std() > 0 else 0
        print(f"  近12个月IC均值: {recent_ic_mean:.4f}")
        print(f"  近12个月IR: {recent_ir:.4f}")
    
    # 保存结果
    result_file = "/tmp/v77_ic_analysis.txt"
    with open(result_file, 'w') as f:
        f.write("v77 隔夜收益率因子 IC分析结果\n")
        f.write("=" * 40 + "\n")
        f.write(f"IC均值: {ic_mean:.4f}\n")
        f.write(f"IC标准差: {ic_std:.4f}\n")
        f.write(f"IR: {ir:.4f}\n")
        f.write(f"IC>0比例: {ic_positive_pct:.2%}\n")
        f.write(f"判定: {verdict}\n")
        f.write(f"\n分年IC:\n{yearly_ic.to_string()}\n")
        f.write(f"\n近12个月IC: {recent_ic_mean:.4f}\n")
        f.write(f"近12个月IR: {recent_ir:.4f}\n")
    
    print(f"\n结果已保存到: {result_file}")
    
    return verdict

if __name__ == "__main__":
    run_ic_analysis()
