"""
v47 — ML 因子计算模块
用随机森林从价量特征预测横截面收益率，输出为独立因子分数。
"""
import numpy as np
import pandas as pd


def compute_ml_factor_from_panels(panels: tuple, n_train: int = 252, retrain_freq: int = 5) -> pd.Series:
    """
    从 load_panel_from_db 返回的面板数据计算 ML 因子。
    
    Parameters
    ----------
    panels : tuple
        (close, volume, amount, open, high, low) — 均为 DataFrame (date x stock)。
        至少需要 close, volume, high, low, open。
    n_train : int
        训练窗口天数。
    retrain_freq : int
        每 N 天重训一次（当前实现每期末都重训，预留参数）。
    
    Returns
    -------
    pd.Series
        最新一期的 ML 因子分数 (stock -> score)，归一化到 [0, 1]。
    """
    close = panels[0]
    volume = panels[1]
    # amount = panels[2]
    open_ = panels[3] if len(panels) > 3 else None
    high = panels[4] if len(panels) > 4 else None
    low = panels[5] if len(panels) > 5 else None
    
    if open_ is None or high is None or low is None:
        raise ValueError("需要 need_open=True, need_hl=True 的面板数据")
    
    return compute_ml_factor(close, volume, high, low, open_, n_train)


def compute_ml_factor(close: pd.DataFrame, volume: pd.DataFrame,
                      high: pd.DataFrame, low: pd.DataFrame,
                      open_: pd.DataFrame, n_train: int = 252) -> pd.Series:
    """
    计算 ML 因子分数。
    
    Parameters
    ----------
    close, volume, high, low, open_ : pd.DataFrame
        date x stock 格式的面板数据。
    n_train : int
        训练窗口天数。
    
    Returns
    -------
    pd.Series
        最新一期的 ML 因子分数 (stock -> score)，归一化到 [0, 1]。
    """
    from sklearn.ensemble import RandomForestRegressor
    
    # 确保数据足够
    if len(close) < n_train + 30:
        return pd.Series(dtype=float)
    
    # 构造特征
    features = _build_features(close, volume, high, low, open_)
    feature_names = list(features.keys())
    
    # 标签：T+1 开盘买入，T+5 收盘卖出的收益率
    buy_price = open_.shift(-1)   # T+1 开盘
    sell_price = close.shift(-5)  # T+5 收盘
    raw_label = sell_price / buy_price - 1
    
    # 对齐日期
    all_dates = close.index
    min_history = 25  # 需要至少25天历史计算特征
    
    # 收集有效日期的特征和标签
    valid_dates = []
    X_by_date = []
    y_by_date = []
    
    for i in range(min_history, len(all_dates) - 6):
        date = all_dates[i]
        
        # 检查所有特征是否可用
        feat_arrays = []
        all_valid = True
        for fname in feature_names:
            f = features[fname]
            if date not in f.index:
                all_valid = False
                break
            row = f.loc[date].values
            feat_arrays.append(row)
        
        if not all_valid:
            continue
        
        # 特征矩阵: (n_stocks, n_features)
        X_date = np.column_stack(feat_arrays)
        
        # 标签
        if date not in raw_label.index:
            continue
        y_date = raw_label.loc[date].values
        
        # 去除 NaN/Inf
        valid_mask = np.all(np.isfinite(X_date), axis=1) & np.isfinite(y_date)
        
        if valid_mask.sum() < 50:
            continue
        
        # 截面 z-score 化标签
        y_valid = y_date[valid_mask]
        y_mean = np.nanmean(y_valid)
        y_std = np.nanstd(y_valid)
        if y_std > 1e-8:
            y_centered = (y_valid - y_mean) / y_std
        else:
            y_centered = y_valid - y_mean
        
        valid_dates.append(date)
        X_by_date.append(X_date[valid_mask])
        y_by_date.append(y_centered)
    
    if len(valid_dates) < n_train:
        return pd.Series(dtype=float)
    
    # 取最新的 n_train 天做训练
    train_start = len(valid_dates) - n_train
    
    # 构建训练集
    X_train = np.vstack(X_by_date[train_start:])
    y_train = np.concatenate(y_by_date[train_start:])
    
    # 去除训练集中的 NaN/Inf
    train_valid = np.all(np.isfinite(X_train), axis=1) & np.isfinite(y_train)
    X_train = X_train[train_valid]
    y_train = y_train[train_valid]
    
    if len(X_train) < 100:
        return pd.Series(dtype=float)
    
    # 训练随机森林
    model = RandomForestRegressor(
        n_estimators=30,
        max_depth=4,
        min_samples_leaf=80,
        max_features=0.5,
        random_state=42,
        n_jobs=-1
    )
    model.fit(X_train, y_train)
    
    # 预测最新一期
    latest_X = X_by_date[-1]
    
    # 获取股票名（从 close 的列）
    stock_names = close.columns.tolist()
    
    # 确保维度一致
    if latest_X.shape[0] != len(stock_names):
        # 有股票被过滤掉了，需要从特征中恢复
        latest_date = valid_dates[-1]
        valid_stocks = []
        for fname in feature_names:
            f = features[fname]
            if latest_date in f.index:
                row = f.loc[latest_date]
                valid_stocks = row.index.tolist()
                break
        stock_names = valid_stocks[:latest_X.shape[0]]
    
    predictions = model.predict(latest_X)
    
    # 归一化到 [0, 1]
    pred_min = predictions.min()
    pred_max = predictions.max()
    if pred_max > pred_min:
        scores = (predictions - pred_min) / (pred_max - pred_min)
    else:
        scores = np.full_like(predictions, 0.5)
    
    return pd.Series(scores, index=stock_names[:len(scores)])


def _build_features(close, volume, high, low, open_):
    """
    构造特征面板。
    返回 dict of DataFrame (date x stock)。
    """
    features = {}
    
    # 1. mom_5: 5日动量
    features['mom_5'] = close.pct_change(5)
    
    # 2. mom_20: 20日动量
    features['mom_20'] = close.pct_change(20)
    
    # 3. vol_ratio_5: 5日量比
    vol_ma5 = volume.rolling(5).mean()
    features['vol_ratio_5'] = volume / vol_ma5
    
    # 4. vol_std_20: 20日波动率
    features['vol_std_20'] = close.pct_change().rolling(20).std()
    
    # 5. reversal_3: 3日反转
    features['reversal_3'] = -close.pct_change(3)  # 取反：超跌=正
    
    # 6. illiq_20: Amihud 非流动性（20日）
    ret_abs = close.pct_change().abs()
    dollar_vol = volume * close
    features['illiq_20'] = (ret_abs / dollar_vol).rolling(20).mean()
    
    # 7. pv_corr_20: 量价相关系数（20日）
    ret = close.pct_change()
    vol_norm = volume / volume.rolling(20).mean()
    features['pv_corr_20'] = _rolling_corr_panel(ret, vol_norm, 20)
    
    # 8. gap_ratio: 开盘跳空
    features['gap_ratio'] = open_ / close.shift(1) - 1
    
    # 9. turnover_change: 换手率变化
    features['turnover_change'] = volume / volume.rolling(20).mean()
    
    # 10. high_low_range: 高低幅
    features['high_low_range'] = (high - low) / close
    
    return features


def _rolling_corr_panel(x, y, window):
    """
    计算面板数据的滚动相关系数。
    x, y: DataFrame (date x stock)
    返回: DataFrame (date x stock)
    """
    # 用 numpy 逐列计算
    result = pd.DataFrame(index=x.index, columns=x.columns, dtype=float)
    
    for col in x.columns:
        if col not in y.columns:
            continue
        x_col = x[col]
        y_col = y[col]
        corr = x_col.rolling(window).corr(y_col)
        result[col] = corr
    
    return result
