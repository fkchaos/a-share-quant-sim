"""v81 低波溢价因子 IC 分析 - 优化版"""
import sys
sys.path.insert(0, '/root/a-share-quant-sim')

import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from core.db import load_panel_from_db

def calc_ivol_vectorized(close: pd.DataFrame, window: int = 20) -> pd.DataFrame:
    """向量化计算IVOL因子（-IVOL）"""
    rets = close.pct_change(fill_method=None)
    mkt_ret = rets.mean(axis=1)
    
    # 预分配结果
    ivols = pd.DataFrame(np.nan, index=close.index, columns=close.columns)
    
    for end_idx in range(window, len(close)):
        start_idx = end_idx - window
        win_rets = rets.iloc[start_idx:end_idx].values  # (window, n_assets)
        win_mkt = mkt_ret.iloc[start_idx:end_idx].values  # (window,)
        
        # 向量化回归：对每只资产同时做
        X = np.column_stack([np.ones(window), win_mkt])  # (window, 2)
        
        # 批量OLS: beta = (X'X)^-1 X'y
        XtX = X.T @ X  # (2, 2)
        XtX_inv = np.linalg.inv(XtX)
        
        for j, asset in enumerate(close.columns):
            y = win_rets[:, j]
            if np.isnan(y).sum() > window - 5:
                continue
            # 填充NaN为0（保持形状）
            y_clean = np.nan_to_num(y, nan=0.0)
            beta = XtX_inv @ (X.T @ y_clean)
            eps = y_clean - X @ beta
            # 只用非NaN位置计算std
            valid_mask = ~np.isnan(y)
            if valid_mask.sum() < 5:
                continue
            ivols.iloc[end_idx, j] = np.std(eps[valid_mask])
    
    # 反向：做多低波动
    return -ivols

def run_ic_analysis():
    """标准IC分析流程"""
    print("加载数据...")
    panels, codes = load_panel_from_db(
        start_date='2020-01-01', 
        end_date='2026-06-30',
        pool='zz1800'
    )
    close, vol, amt = panels
    
    print(f"数据维度: {close.shape[0]}天 × {close.shape[1]}只股票")
    
    # 计算因子
    print("计算IVOL因子...")
    factor = calc_ivol_vectorized(close, window=20)
    
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
        'ic_mean': ic_mean,
        'ic_std': ic_std,
        'ir': ir,
        'p_positive': p_positive,
        'samples': len(ic_df),
    }
    with open('/tmp/v81_ivol_ic.json', 'w') as f:
        json.dump(result, f, indent=2)
    print("\n结果已保存: /tmp/v81_ivol_ic.json")

if __name__ == '__main__':
    run_ic_analysis()
