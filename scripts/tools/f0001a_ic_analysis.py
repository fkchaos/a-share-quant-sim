#!/usr/bin/env python3
"""f0001a 隔夜-日内反转因子 IC 分析

factor-factory定义：
  overnight = open[t] / close[t-1] - 1
  intraday = close[t] / open[t] - 1
  factor = overnight - intraday（做多"高开低走"的票）

与v77的区别：v77只用了overnight成分，f0001a是overnight-intraday组合
"""
import sys
sys.path.insert(0, '/root/a-share-quant-sim')

import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from core.db import load_panel_from_db

def calc_f0001a(close: pd.DataFrame, open_p: pd.DataFrame) -> pd.DataFrame:
    """计算f0001a因子：overnight - intraday"""
    prev_close = close.shift(1)
    overnight = open_p / prev_close - 1.0
    intraday = close / open_p - 1.0
    factor = overnight - intraday
    return factor

def run_ic_analysis():
    print("加载数据...")
    panels, codes = load_panel_from_db(
        start_date='2020-01-01',
        end_date='2026-06-30',
        need_open=True,
        pool='zz1800'
    )
    close, vol, amt, open_p = panels
    print(f"数据维度: {close.shape[0]}天 × {close.shape[1]}只股票")

    # 计算因子
    print("计算f0001a因子...")
    factor = calc_f0001a(close, open_p)

    # 未来5日收益
    fwd_ret = close.pct_change(5).shift(-5)

    # 逐日计算RankIC
    print("计算RankIC...")
    ic_list = []
    dates = factor.index[20:]  # 跳过预热期

    for date in dates:
        f = factor.loc[date].dropna()
        r = fwd_ret.loc[date].dropna()
        common = f.index.intersection(r.index)
        if len(common) < 50:
            continue
        ic, _ = spearmanr(f[common], r[common])
        ic_list.append({'date': date, 'ic': ic})

    ic_df = pd.DataFrame(ic_list).set_index('date')

    # 统计
    ic_mean = ic_df['ic'].mean()
    ic_std = ic_df['ic'].std()
    ir = ic_mean / ic_std if ic_std > 0 else 0
    p_positive = (ic_df['ic'] > 0).mean()

    print("\n=== IC分析结果 ===")
    print(f"IC均值: {ic_mean:.4f}")
    print(f"IC标准差: {ic_std:.4f}")
    print(f"IR: {ir:.4f}")
    print(f"P(>0): {p_positive:.1%}")

    # 分年IC
    print("\n=== 分年IC ===")
    ic_df['year'] = ic_df.index.year
    for year, group in ic_df.groupby('year'):
        y_ic = group['ic'].mean()
        y_ir = y_ic / group['ic'].std() if group['ic'].std() > 0 else 0
        print(f"{year}: IC={y_ic:.4f}, IR={y_ir:.2f}, 样本={len(group)}天")

    # IC衰减（近12个月）
    print("\n=== IC衰减分析 ===")
    recent_12m = ic_df[ic_df.index >= '2025-07-01']
    if len(recent_12m) > 0:
        recent_ic = recent_12m['ic'].mean()
        recent_ir = recent_ic / recent_12m['ic'].std() if recent_12m['ic'].std() > 0 else 0
        print(f"近12个月IC均值: {recent_ic:.4f}")
        print(f"近12个月IR: {recent_ir:.4f}")

    # 判定
    print("\n=== 判定 ===")
    if abs(ic_mean) > 0.03 and abs(ir) > 0.3:
        print("✅ 有效因子，可进入WF验证")
    elif abs(ic_mean) < 0.01 or abs(ir) < 0.1:
        print("❌ 证伪因子，不进入WF")
    else:
        print("⚠️ 微弱信号，需进一步分析")

    # 保存结果
    import json
    result = {
        'ic_mean': float(ic_mean),
        'ic_std': float(ic_std),
        'ir': float(ir),
        'p_positive': float(p_positive),
        'samples': len(ic_df),
    }
    with open('/tmp/f0001a_ic.json', 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\n结果已保存: /tmp/f0001a_ic.json")

    return ic_mean, ir

if __name__ == '__main__':
    run_ic_analysis()
