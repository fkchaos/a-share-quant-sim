#!/usr/bin/env python3
"""追高类因子IC分析：量价相关性 / 资金净流入 / 波动率调整动量

3个因子分别做IC分析，通过门槛（|IC|>0.03, |IR|>0.3）的再进WF。
"""
import sys
sys.path.insert(0, "/root/a-share-quant-sim")

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from core.db import load_panel_from_db


def calc_price_volume_corr(close, vol, window=20):
    """量价相关性因子：日收益率与成交量变化率的滚动相关系数
    
    正相关 = 健康上涨（价升量增）
    负相关 = 无量空涨（不可持续）
    """
    daily_ret = close.pct_change(fill_method=None)
    daily_vol_chg = vol.pct_change(fill_method=None)
    
    # 逐列计算滚动相关系数
    result = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    for col in close.columns:
        r = daily_ret[col].rolling(window, min_periods=window//2).corr(daily_vol_chg[col])
        result[col] = r
    return result


def calc_net_money_flow(close, opn, vol, window=20):
    """资金净流入因子：N日内 (close - open) * volume 的累计值
    
    收盘价>开盘价代表买方占优，乘以成交量=资金净流入
    取rank后标准化
    """
    # 日内资金方向 = (收盘-开盘) * 成交量
    daily_flow = (close - opn) * vol
    # N日累计
    cum_flow = daily_flow.rolling(window, min_periods=window//2).sum()
    return cum_flow


def calc_risk_adjusted_momentum(close, window=20):
    """波动率调整动量：N日收益率 / N日波动率（类似个股Sharpe）
    
    涨得多+波动小 = 高质量趋势
    """
    ret = close.pct_change(window, fill_method=None)
    vol = close.pct_change(fill_method=None).rolling(window, min_periods=window//2).std()
    vol = vol.replace(0, np.nan)
    return ret / vol


def run_ic_analysis():
    """计算3个因子的IC、IR、分年IC"""
    print("=" * 60)
    print("追高类因子 IC分析（3因子）")
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
    fwd_ret = close.pct_change(5, fill_method=None).shift(-5)
    
    # 因子定义
    factors_def = {
        'pv_corr': ('量价相关性', calc_price_volume_corr),
        'net_flow': ('资金净流入', calc_net_money_flow),
        'ram': ('波动率调整动量', calc_risk_adjusted_momentum),
    }
    
    results = {}
    
    for factor_key, (factor_name, calc_func) in factors_def.items():
        print(f"\n{'='*60}")
        print(f"[{factor_name}] 开始计算...")
        print(f"{'='*60}")
        
        # 逐日计算因子和IC
        dates = close.index
        ic_list = []
        
        for i in range(30, len(dates) - 5):
            date = dates[i]
            
            # 截取数据
            close_slice = close.iloc[:i+1]
            vol_slice = vol.iloc[:i+1]
            opn_slice = opn.iloc[:i+1]
            
            # 计算因子
            if factor_key == 'pv_corr':
                factor_values = calc_func(close_slice, vol_slice, window=20)
            elif factor_key == 'net_flow':
                factor_values = calc_func(close_slice, opn_slice, vol_slice, window=20)
            else:  # ram
                factor_values = calc_func(close_slice, window=20)
            
            # 取最新一天的因子值
            latest_factors = factor_values.iloc[-1]
            
            # 计算未来收益
            future_ret = fwd_ret.iloc[i]
            
            # 对齐
            common_stocks = latest_factors.dropna().index.intersection(future_ret.dropna().index)
            if len(common_stocks) < 100:
                continue
            
            f = latest_factors[common_stocks].values
            r = future_ret[common_stocks].values
            
            # Spearman IC
            ic, _ = spearmanr(f, r)
            
            if not np.isnan(ic):
                ic_list.append({'date': date, 'ic': ic})
        
        ic_df = pd.DataFrame(ic_list)
        ic_df.set_index('date', inplace=True)
        
        # 基本统计
        ic_mean = ic_df['ic'].mean()
        ic_std = ic_df['ic'].std()
        ir = ic_mean / ic_std if ic_std > 0 else 0
        ic_positive_pct = (ic_df['ic'] > 0).mean()
        
        print(f"\n  IC均值: {ic_mean:.4f}")
        print(f"  IC标准差: {ic_std:.4f}")
        print(f"  IR (IC均值/IC标准差): {ir:.4f}")
        print(f"  IC>0比例: {ic_positive_pct:.2%}")
        
        # 判定（含P(>0)检查）
        if abs(ic_mean) > 0.03 and abs(ir) > 0.3 and (ic_positive_pct > 0.6 or ic_positive_pct < 0.4):
            print(f"  ✅ 有效因子，进入WF验证")
            verdict = "PASS"
        elif abs(ic_mean) < 0.01 or abs(ir) < 0.1 or (0.45 < ic_positive_pct < 0.55):
            print(f"  ❌ 证伪，不进入WF")
            verdict = "FAIL"
        else:
            print(f"  ⚠️ 微弱信号，不值得投入WF时间")
            verdict = "MARGINAL"
        
        # 分年IC
        print(f"\n  分年IC:")
        ic_df['year'] = ic_df.index.year
        yearly_ic = ic_df.groupby('year')['ic'].agg(['mean', 'std'])
        yearly_ic['ir'] = yearly_ic['mean'] / yearly_ic['std']
        print(yearly_ic.to_string())
        
        # IC衰减分析
        recent_12m = ic_df[ic_df.index >= '2025-07-01']
        if len(recent_12m) > 0:
            recent_ic_mean = recent_12m['ic'].mean()
            recent_ir = recent_12m['ic'].mean() / recent_12m['ic'].std() if recent_12m['ic'].std() > 0 else 0
            print(f"\n  近12个月IC均值: {recent_ic_mean:.4f}")
            print(f"  近12个月IR: {recent_ir:.4f}")
        
        results[factor_key] = {
            'name': factor_name,
            'ic_mean': ic_mean,
            'ic_std': ic_std,
            'ir': ir,
            'ic_positive_pct': ic_positive_pct,
            'verdict': verdict,
            'yearly_ic': yearly_ic,
            'recent_ic': recent_ic_mean if len(recent_12m) > 0 else None,
            'recent_ir': recent_ir if len(recent_12m) > 0 else None,
        }
    
    # 汇总
    print("\n" + "=" * 60)
    print("汇总结果")
    print("=" * 60)
    print(f"{'因子':<15} {'IC均值':>8} {'IR':>8} {'判定':<10}")
    print("-" * 50)
    for key, r in results.items():
        print(f"{r['name']:<15} {r['ic_mean']:>8.4f} {r['ir']:>8.4f} {r['verdict']:<10}")
    
    # 保存结果
    result_file = "/tmp/v79_chasing_factors_ic_analysis.txt"
    with open(result_file, 'w') as f:
        f.write("追高类因子 IC分析结果\n")
        f.write("=" * 40 + "\n\n")
        for key, r in results.items():
            f.write(f"【{r['name']}】\n")
            f.write(f"  IC均值: {r['ic_mean']:.4f}\n")
            f.write(f"  IC标准差: {r['ic_std']:.4f}\n")
            f.write(f"  IR: {r['ir']:.4f}\n")
            f.write(f"  IC>0比例: {r['ic_positive_pct']:.2%}\n")
            f.write(f"  判定: {r['verdict']}\n")
            recent_ic_str = f"{r['recent_ic']:.4f}" if r['recent_ic'] is not None else "N/A"
            recent_ir_str = f"{r['recent_ir']:.4f}" if r['recent_ir'] is not None else "N/A"
            f.write(f"  近12个月IC: {recent_ic_str}\n")
            f.write(f"  近12个月IR: {recent_ir_str}\n")
            f.write(f"\n分年IC:\n{r['yearly_ic'].to_string()}\n\n")
    
    print(f"\n结果已保存到: {result_file}")
    return results


if __name__ == "__main__":
    run_ic_analysis()
