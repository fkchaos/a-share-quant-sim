#!/usr/bin/env python3
"""f0003a 等权组合因子 IC 分析（f0001a + f0002a）

f0003a = 0.5 * f0001a(overnight_intraday) + 0.5 * f0002a(-IVOL)

factor-factory数据：IC=0.044, IR=0.522（zz1000池）
在zz1800池验证
"""
import sys
sys.path.insert(0, '/root/a-share-quant-sim')

import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from core.db import load_panel_from_db

def calc_f0001a(open_p, close, window=20):
    """计算f0001a因子（overnight - intraday）"""
    overnight = open_p / close.shift(1) - 1
    intraday = close / open_p - 1
    factor = overnight - intraday
    
    # 滚动均值平滑
    if window > 1:
        factor = factor.rolling(window).mean()
    
    return factor

def calc_f0002a_ivol(close, window=20):
    """计算f0002a IVOL因子（-IVOL）"""
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
    
    return -ivols  # 低波溢价：取负

def run_ic_analysis():
    """标准IC分析流程"""
    print("加载数据...")
    panels, codes = load_panel_from_db(
        start_date='2020-01-01', end_date='2026-06-30',
        need_open=True, pool='zz1800'
    )
    close, vol, amt, open_p = panels
    print(f"数据维度: {close.shape[0]}天 × {close.shape[1]}只股票")
    
    # 计算两个子因子
    print("计算f0001a因子...")
    f0001a = calc_f0001a(open_p, close, window=20)
    
    print("计算f0002a IVOL因子...")
    f0002a = calc_f0002a_ivol(close, window=20)
    
    # 等权组合
    print("计算等权组合因子...")
    # 截面对齐后等权组合
    f0001a_ranked = f0001a.rank(axis=1, pct=True)
    f0002a_ranked = f0002a.rank(axis=1, pct=True)
    combo = (f0001a_ranked + f0002a_ranked) / 2
    
    # 未来5日收益
    fwd_ret = close.pct_change(5).shift(-5)
    
    # 逐日计算RankIC
    print("计算RankIC...")
    ic_list = []
    dates = combo.index[20:]  # 跳过预热期
    
    for date in dates:
        f = combo.loc[date].dropna()
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
        verdict = "PASS"
    elif abs(ic_mean) < 0.01 or abs(ir) < 0.1:
        print("❌ 证伪因子，不进入WF")
        verdict = "FAIL"
    else:
        print("⚠️ 微弱信号，需进一步分析")
        verdict = "MARGINAL"
    
    # 与子因子对比
    print("\n=== 与子因子对比 ===")
    print(f"f0001a IC: 0.0191")
    print(f"f0002a IC: 0.0552")
    print(f"组合 IC: {ic_mean:.4f}")
    
    # 保存结果
    import json
    result = {
        'ic_mean': float(ic_mean),
        'ic_std': float(ic_std),
        'ir': float(ir),
        'p_positive': float(p_positive),
        'samples': len(ic_df),
        'verdict': verdict,
    }
    with open('/tmp/f0003a_ic.json', 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\n结果已保存: /tmp/f0003a_ic.json")

if __name__ == '__main__':
    run_ic_analysis()
