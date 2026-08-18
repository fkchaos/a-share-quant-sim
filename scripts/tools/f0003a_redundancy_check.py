#!/usr/bin/env python3
"""f0003a 冗余度检查"""
import sys
sys.path.insert(0, '/root/a-share-quant-sim')

import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from core.db import load_panel_from_db


def calc_f0001a(open_p, close, window=20):
    """计算f0001a因子"""
    overnight = open_p / close.shift(1) - 1
    intraday = close / open_p - 1
    factor = overnight - intraday
    if window > 1:
        factor = factor.rolling(window).mean()
    return factor


def calc_f0002a_ivol(close, window=20):
    """计算f0002a IVOL因子"""
    rets = close.pct_change(fill_method=None)
    mkt_ret = rets.mean(axis=1)
    ivols = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    
    for end_idx in range(window, len(close)):
        start_idx = end_idx - window
        win_rets = rets.iloc[start_idx:end_idx].values
        win_mkt = mkt_ret.iloc[start_idx:end_idx].values
        X = np.column_stack([np.ones(window), win_mkt])
        try:
            XtX_inv = np.linalg.inv(X.T @ X)
        except:
            continue
        for j, asset in enumerate(close.columns):
            y = win_rets[:, j]
            if np.isnan(y).sum() > window - 5:
                continue
            y_clean = np.nan_to_num(y, nan=0.0)
            beta = XtX_inv @ (X.T @ y_clean)
            eps = y_clean - X @ beta
            valid_mask = ~np.isnan(y)
            if valid_mask.sum() < 5:
                continue
            ivols.iloc[end_idx, j] = np.std(eps[valid_mask])
    return -ivols


def main():
    print("加载数据...")
    panels, codes = load_panel_from_db(
        start_date='2022-01-01', end_date='2026-06-30',
        need_open=True, pool='zz1800'
    )
    close, vol, amt, open_p = panels
    print(f"数据: {close.shape[0]}天 x {close.shape[1]}只股票")
    
    print("计算因子...")
    f0001a = calc_f0001a(open_p, close, window=20)
    f0002a = calc_f0002a_ivol(close, window=20)
    
    # f0003a = 等权组合
    f0001a_ranked = f0001a.rank(axis=1, pct=True)
    f0002a_ranked = f0002a.rank(axis=1, pct=True)
    f0003a = (f0001a_ranked + f0002a_ranked) / 2
    
    # v75j流动性因子（用volume代理）
    turnover_20d = vol.rolling(20).mean()
    
    # 取测试期
    test_start = pd.Timestamp('2022-01-01')
    f0003a = f0003a[f0003a.index >= test_start]
    f0002a = f0002a[f0002a.index >= test_start]
    turnover_20d = turnover_20d[turnover_20d.index >= test_start]
    
    print("计算截面相关性...")
    dates = f0003a.index[20:]  # 跳过预热期
    
    corr_f0003a_f0002a = []
    corr_f0003a_turnover = []
    
    for date in dates:
        f3 = f0003a.loc[date].dropna()
        f2 = f0002a.loc[date].dropna()
        t20 = turnover_20d.loc[date].dropna()
        
        # f0003a vs f0002a
        common = f3.index.intersection(f2.index)
        if len(common) > 100:
            c, _ = spearmanr(f3[common], f2[common])
            if np.isfinite(c):
                corr_f0003a_f0002a.append(c)
        
        # f0003a vs turnover
        common = f3.index.intersection(t20.index)
        if len(common) > 100:
            c, _ = spearmanr(f3[common], t20[common])
            if np.isfinite(c):
                corr_f0003a_turnover.append(c)
    
    corr_f0003a_f0002a = np.array(corr_f0003a_f0002a)
    corr_f0003a_turnover = np.array(corr_f0003a_turnover)
    
    print("\n=== 冗余度分析 ===")
    print(f"f0003a vs f0002a:")
    print(f"  均值: {corr_f0003a_f0002a.mean():.4f}")
    print(f"  >0.3占比: {(corr_f0003a_f0002a > 0.3).mean()*100:.1f}%")
    
    print(f"\nf0003a vs Turnover20:")
    print(f"  均值: {corr_f0003a_turnover.mean():.4f}")
    print(f"  >0.3占比: {(corr_f0003a_turnover > 0.3).mean()*100:.1f}%")
    
    print("\n=== 判定 ===")
    avg_corr_f2 = corr_f0003a_f0002a.mean()
    avg_corr_t = corr_f0003a_turnover.mean()
    
    if avg_corr_f2 > 0.7:
        print(f"f0003a vs f0002a: ❌ 高度冗余 ({avg_corr_f2:.4f})")
    elif avg_corr_f2 > 0.5:
        print(f"f0003a vs f0002a: ⚠️ 中度冗余 ({avg_corr_f2:.4f})")
    else:
        print(f"f0003a vs f0002a: ✅ 独立 ({avg_corr_f2:.4f})")
    
    if avg_corr_t > 0.7:
        print(f"f0003a vs Turnover: ❌ 高度冗余 ({avg_corr_t:.4f})")
    elif avg_corr_t > 0.5:
        print(f"f0003a vs Turnover: ⚠️ 中度冗余 ({avg_corr_t:.4f})")
    else:
        print(f"f0003a vs Turnover: ✅ 独立 ({avg_corr_t:.4f})")
    
    import json
    result = {
        'f0003a_f0002a_mean_corr': float(corr_f0003a_f0002a.mean()),
        'f0003a_turnover_mean_corr': float(corr_f0003a_turnover.mean()),
    }
    with open('/tmp/f0003a_redundancy.json', 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\n结果已保存: /tmp/f0003a_redundancy.json")


if __name__ == '__main__':
    main()
