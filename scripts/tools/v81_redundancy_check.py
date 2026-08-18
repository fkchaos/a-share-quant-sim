"""v81 IVOL与v75j流动性因子冗余度检查"""
import sys
sys.path.insert(0, '/root/a-share-quant-sim')

import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from core.db import load_panel_from_db

def compute_ivol(panel, date, window=20):
    """计算IVOL因子"""
    dates = sorted(panel.index.get_level_values('date').unique())
    if date not in dates:
        return pd.Series(dtype=float)
    idx = dates.index(date)
    if idx < window:
        return pd.Series(dtype=float)
    win_dates = dates[idx-window:idx+1]
    win = panel.loc[panel.index.get_level_values('date').isin(win_dates)]
    closes = win['close'].unstack('asset')
    rets = closes.pct_change().dropna()
    if len(rets) < 5:
        return pd.Series(dtype=float)
    mkt = rets.mean(axis=1)
    ivols = {}
    for a in rets.columns:
        ra = rets[a].dropna()
        common = ra.index.intersection(mkt.index)
        if len(common) < 5:
            continue
        ra_c, mkt_c = ra[common].values, mkt[common].values
        X = np.column_stack([np.ones(len(ra_c)), mkt_c])
        beta, *_ = np.linalg.lstsq(X, ra_c, rcond=None)
        eps = ra_c - X @ beta
        ivols[a] = np.std(eps)
    return -pd.Series(ivols)  # 低波溢价：取负

def compute_turnover_20d(panel, date):
    """计算20日平均volume作为流动性代理"""
    dates = sorted(panel.index.get_level_values('date').unique())
    if date not in dates:
        return pd.Series(dtype=float)
    idx = dates.index(date)
    if idx < 20:
        return pd.Series(dtype=float)
    win_dates = dates[idx-20:idx+1]
    win = panel.loc[panel.index.get_level_values('date').isin(win_dates)]
    # 用volume作为流动性代理
    vol = win['volume'].unstack('asset')
    return vol.mean(axis=0)

def main():
    print("加载数据...")
    # load_panel_from_db返回((close_panel, volume_panel, amount_panel), codes)
    result, codes = load_panel_from_db(pool='zz1800')
    close_panel, vol_panel, amt_panel = result
    
    # 构建panel
    close_panel = close_panel.copy()
    vol_panel = vol_panel.copy()
    amt_panel = amt_panel.copy()
    
    # 计算turnover = volume / float_shares（近似用volume作为换手率代理）
    # 或者直接用volume的相对变化作为流动性代理
    panel = pd.DataFrame({
        'close': close_panel.stack(),
        'volume': vol_panel.stack(),
        'amount': amt_panel.stack(),
    })
    panel.index.names = ['date', 'asset']
    
    dates = sorted(panel.index.get_level_values('date').unique())
    
    # 取2022-01-01起的数据
    start = pd.Timestamp('2022-01-01')
    test_dates = [d for d in dates if d >= start and d.weekday() < 5]
    
    print(f"数据维度: {len(dates)}天, 测试期: {len(test_dates)}天")
    
    ivol_list = []
    turnover_list = []
    
    for i, date in enumerate(test_dates):
        if i % 100 == 0:
            print(f"  进度: {i}/{len(test_dates)}")
        
        ivol = compute_ivol(panel, date)
        turnover = compute_turnover_20d(panel, date)
        
        common = ivol.index.intersection(turnover.index)
        if len(common) < 100:
            continue
        
        ivol_list.append(ivol[common])
        turnover_list.append(turnover[common])
    
    print(f"\n有效样本: {len(ivol_list)}天")
    
    # 计算截面Rank相关系数
    corrs = []
    for ivol_s, turnover_s in zip(ivol_list, turnover_list):
        if len(ivol_s) < 100:
            continue
        corr, _ = spearmanr(ivol_s, turnover_s)
        if np.isfinite(corr):
            corrs.append(corr)
    
    corrs = np.array(corrs)
    print(f"\n=== 冗余度分析 ===")
    print(f"IVOL vs Turnover20 RankIC相关:")
    print(f"  均值: {corrs.mean():.4f}")
    print(f"  标准差: {corrs.std():.4f}")
    print(f"  中位数: {np.median(corrs):.4f}")
    print(f"  >0.3占比: {(corrs > 0.3).mean()*100:.1f}%")
    print(f"  >0.5占比: {(corrs > 0.5).mean()*100:.1f}%")
    
    # 判定
    avg_corr = corrs.mean()
    if avg_corr > 0.7:
        verdict = "❌ 高度冗余，不宜同时使用"
    elif avg_corr > 0.5:
        verdict = "⚠️ 中度冗余，谨慎使用"
    elif avg_corr > 0.3:
        verdict = "✅ 低度冗余，可组合"
    else:
        verdict = "✅ 独立因子，适合组合"
    
    print(f"\n判定: {verdict}")
    
    # 保存结果
    import json
    result = {
        'mean_corr': float(corrs.mean()),
        'std_corr': float(corrs.std()),
        'median_corr': float(np.median(corrs)),
    }
    with open('/tmp/v81_redundancy.json', 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\n结果已保存: /tmp/v81_redundancy.json")

if __name__ == '__main__':
    main()
