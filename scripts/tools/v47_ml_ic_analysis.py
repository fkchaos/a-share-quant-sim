"""
v47 ML 因子 IC 分析
计算 ML 因子与未来 N 日收益率的 IC（信息系数）序列。
"""
import numpy as np
import pandas as pd
from core.db import load_panel_from_db
from core.ml_factor import compute_ml_factor, _build_features


def compute_ml_factor_ic(n_train=120, forward_days=5, start_date='2023-01-01', pool='zz1800'):
    """
    计算 ML 因子的 IC 时间序列。
    
    IC = ML 因子分数与未来 N 日收益的截面秩相关系数。
    
    Returns
    -------
    dict: { 'ic_series': pd.Series, 'ic_mean': float, 'ic_std': float, 'ir': float }
    """
    panels, codes = load_panel_from_db(start_date, None, need_open=True, need_hl=True, pool=pool)
    close, volume, amount, open_, high, low = panels
    
    features = _build_features(close, volume, high, low, open_)
    feature_names = list(features.keys())
    
    # 标签：T+1 开盘买入，T+forward_days 收盘卖出
    buy_price = open_.shift(-1)
    sell_price = close.shift(-forward_days)
    raw_label = sell_price / buy_price - 1
    
    all_dates = close.index
    min_history = 25
    
    # 收集有效日期
    valid_dates = []
    X_by_date = []
    y_by_date = []
    
    for i in range(min_history, len(all_dates) - forward_days - 1):
        date = all_dates[i]
        
        feat_arrays = []
        all_valid = True
        for fname in feature_names:
            f = features[fname]
            if date not in f.index:
                all_valid = False
                break
            feat_arrays.append(f.loc[date].values)
        
        if not all_valid:
            continue
        
        X_date = np.column_stack(feat_arrays)
        
        if date not in raw_label.index:
            continue
        y_date = raw_label.loc[date].values
        
        valid_mask = np.all(np.isfinite(X_date), axis=1) & np.isfinite(y_date)
        if valid_mask.sum() < 50:
            continue
        
        valid_dates.append(date)
        X_by_date.append(X_date[valid_mask])
        y_by_date.append(y_date[valid_mask])
    
    if len(valid_dates) < n_train + 10:
        print(f"有效日期不足: {len(valid_dates)} < {n_train + 10}")
        return None
    
    # 滚动预测 + IC 计算
    from sklearn.ensemble import RandomForestRegressor
    
    ic_values = []
    ic_dates = []
    
    # 从第 n_train 天开始，每隔 60 天计算一次（减少计算量）
    for test_idx in range(n_train, len(valid_dates), 60):
        train_start = max(0, test_idx - n_train)
        
        # 构建训练集
        X_train_list = []
        y_train_list = []
        
        for i in range(train_start, test_idx):
            X_d = X_by_date[i]
            y_d = y_by_date[i]
            
            # 截面 z-score
            y_mean = np.nanmean(y_d)
            y_std = np.nanstd(y_d)
            if y_std > 1e-8:
                y_centered = (y_d - y_mean) / y_std
            else:
                y_centered = y_d - y_mean
            
            X_train_list.append(X_d)
            y_train_list.append(y_centered)
        
        if len(X_train_list) == 0:
            continue
        
        X_train = np.vstack(X_train_list)
        y_train = np.concatenate(y_train_list)
        
        train_valid = np.all(np.isfinite(X_train), axis=1) & np.isfinite(y_train)
        X_train = X_train[train_valid]
        y_train = y_train[train_valid]
        
        if len(X_train) < 100:
            continue
        
        # 训练
        model = RandomForestRegressor(
            n_estimators=30, max_depth=4, min_samples_leaf=80,
            max_features=0.5, random_state=42, n_jobs=-1
        )
        model.fit(X_train, y_train)
        
        # 预测
        X_test = X_by_date[test_idx]
        predictions = model.predict(X_test)
        actual_returns = y_by_date[test_idx]
        
        # 计算 IC（秩相关）
        if len(predictions) > 50:
            # 用 Spearman 秩相关
            pred_rank = pd.Series(predictions).rank()
            actual_rank = pd.Series(actual_returns).rank()
            ic = pred_rank.corr(actual_rank, method='spearman')
            
            if np.isfinite(ic):
                ic_values.append(ic)
                ic_dates.append(valid_dates[test_idx])
    
    if len(ic_values) == 0:
        print("无有效 IC 值")
        return None
    
    ic_series = pd.Series(ic_values, index=ic_dates)
    ic_mean = np.mean(ic_values)
    ic_std = np.std(ic_values)
    ir = ic_mean / ic_std if ic_std > 0 else 0
    
    return {
        'ic_series': ic_series,
        'ic_mean': ic_mean,
        'ic_std': ic_std,
        'ir': ir,
        'n_obs': len(ic_values),
        'positive_ic_ratio': sum(1 for x in ic_values if x > 0) / len(ic_values)
    }


if __name__ == '__main__':
    import time
    t0 = time.time()
    
    print("=" * 60)
    print("v47 ML 因子 IC 分析")
    print("=" * 60)
    
    result = compute_ml_factor_ic(n_train=120, forward_days=5, start_date='2023-01-01', pool='zz1800')
    
    elapsed = time.time() - t0
    
    if result:
        print(f"\n耗时: {elapsed:.1f}s")
        print(f"IC 观测数: {result['n_obs']}")
        print(f"IC Mean: {result['ic_mean']:.4f}")
        print(f"IC Std: {result['ic_std']:.4f}")
        print(f"IR (IC Mean / IC Std): {result['ir']:.4f}")
        print(f"正 IC 占比: {result['positive_ic_ratio']:.1%}")
        
        # 判断
        if result['ic_mean'] > 0.03 and result['ir'] > 0.3:
            print("\n✅ ML 因子有效 (IC>0.03, IR>0.3)")
        elif result['ic_mean'] > 0.01:
            print("\n⚠️ ML 因子微弱有效 (IC>0.01)")
        else:
            print("\n❌ ML 因子无效 (IC≈0)")
        
        # IC 时间序列统计
        ic = result['ic_series']
        print(f"\nIC 时间序列:")
        print(f"  最新 10 期 IC: {ic.tail(10).values.round(4).tolist()}")
        print(f"  IC > 0 的月份: {(ic > 0).sum()}/{len(ic)}")
    else:
        print("IC 分析失败")
