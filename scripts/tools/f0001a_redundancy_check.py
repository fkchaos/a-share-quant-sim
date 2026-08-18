#!/usr/bin/env python3
"""f0001a 冗余度检查：与v81 IVOL、v75j流动性因子的相关性"""
import sys
sys.path.insert(0, '/root/a-share-quant-sim')

import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from core.db import load_panel_from_db

def calc_f0001a(close, open_p):
    prev_close = close.shift(1)
    overnight = open_p / prev_close - 1.0
    intraday = close / open_p - 1.0
    return overnight - intraday

def calc_ivol(close, window=20):
    """计算IVOL因子（-IVOL）"""
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

def calc_turnover(volume, window=20):
    """20日平均volume作为流动性代理"""
    return volume.rolling(window).mean()

def main():
    print("加载数据...")
    panels, codes = load_panel_from_db(
        start_date='2022-01-01', end_date='2026-06-30',
        need_open=True, pool='zz1800'
    )
    close, vol, amt, open_p = panels
    print(f"数据: {close.shape[0]}天 x {close.shape[1]}只股票")

    # 计算三个因子
    print("计算因子...")
    f0001a = calc_f0001a(close, open_p)
    ivol = calc_ivol(close, window=20)
    turnover = calc_turnover(vol, window=20)

    # 逐日计算截面相关性
    print("计算截面相关性...")
    dates = close.index[30:]  # 跳过预热期
    
    f0001a_ivol_corrs = []
    f0001a_turnover_corrs = []
    
    for date in dates:
        f1 = f0001a.loc[date].dropna()
        f2 = ivol.loc[date].dropna()
        f3 = turnover.loc[date].dropna()
        
        # f0001a vs ivol
        common1 = f1.index.intersection(f2.index)
        if len(common1) > 100:
            corr1, _ = spearmanr(f1[common1], f2[common1])
            if np.isfinite(corr1):
                f0001a_ivol_corrs.append(corr1)
        
        # f0001a vs turnover
        common2 = f1.index.intersection(f3.index)
        if len(common2) > 100:
            corr2, _ = spearmanr(f1[common2], f3[common2])
            if np.isfinite(corr2):
                f0001a_turnover_corrs.append(corr2)

    f0001a_ivol_corrs = np.array(f0001a_ivol_corrs)
    f0001a_turnover_corrs = np.array(f0001a_turnover_corrs)

    print("\n=== 冗余度分析 ===")
    print(f"f0001a vs IVOL:")
    print(f"  均值: {f0001a_ivol_corrs.mean():.4f}")
    print(f"  >0.3占比: {(f0001a_ivol_corrs > 0.3).mean()*100:.1f}%")
    
    print(f"\nf0001a vs Turnover20:")
    print(f"  均值: {f0001a_turnover_corrs.mean():.4f}")
    print(f"  >0.3占比: {(f0001a_turnover_corrs > 0.3).mean()*100:.1f}%")

    # 判定
    avg_corr1 = f0001a_ivol_corrs.mean()
    avg_corr2 = f0001a_turnover_corrs.mean()
    
    print("\n=== 判定 ===")
    if avg_corr1 > 0.5:
        print("f0001a vs IVOL: ⚠️ 中度冗余")
    elif avg_corr1 > 0.3:
        print("f0001a vs IVOL: ✅ 低度冗余")
    else:
        print("f0001a vs IVOL: ✅ 独立因子")
        
    if avg_corr2 > 0.5:
        print("f0001a vs Turnover: ⚠️ 中度冗余")
    elif avg_corr2 > 0.3:
        print("f0001a vs Turnover: ✅ 低度冗余")
    else:
        print("f0001a vs Turnover: ✅ 独立因子")

    import json
    result = {
        'f0001a_ivol_mean_corr': float(avg_corr1),
        'f0001a_turnover_mean_corr': float(avg_corr2),
    }
    with open('/tmp/f0001a_redundancy.json', 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\n结果已保存: /tmp/f0001a_redundancy.json")

if __name__ == '__main__':
    main()
