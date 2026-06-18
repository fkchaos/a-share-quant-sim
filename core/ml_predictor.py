"""
ML Predictor — 模拟盘在线推理引擎
====================================

从已训练的模型（pickle 序列化）加载 LightGBM ensemble，
对当日因子截面做预测，输出选股评分。

设计原则：
  - 模型离线训练（ml_rolling_train.py 的 training mode）
  - 模拟盘只 inference（本模块）← 训练/推理彻底分离
  - 输入输出接口与 score_all_stocks() 对齐，无缝切换
  - 支持纯 ML 模式和 hybrid 模式（ML + 因子加权）

使用流程：
  1. 训练并保存模型：
     from core.ml_predictor import train_and_save
     train_and_save(factors, close_panel, model_dir=os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "ml_models",
                    profile="v6b_8f_pos_ic", hybrid_alpha=0.8)
  2. 模拟盘推理：
     from core.ml_predictor import MLPredictor
     predictor = MLPredictor(model_dir=os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "ml_models")
     scores = predictor.predict(all_factors_single_stock)  # {code: score}
"""

import os
import json
import pickle
import hashlib
import numpy as np
import pandas as pd
from typing import Dict, Optional, Tuple, List
from datetime import datetime

from core.config import STRATEGY_PROFILES
from core.ml import (
    FeatureBuilder, RollingTrainer, EnsembleTrainer,
    run_ml_pipeline, ALL_FACTOR_NAMES,
)

# ──────────────────────────────────────────────────────────────
# 训练并保存模型（离线，可每日/每周更新）
# ──────────────────────────────────────────────────────────────

def train_and_save(
    factors: dict,
    close_panel: pd.DataFrame,
    model_dir: str = os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "ml_models"),
    profile: str = "v6b_8f_pos_ic",
    hybrid_alpha: float = 0.8,
    forward_periods: list = None,
    train_days: int = 252,
    stock_names: dict = None,
    verbose: bool = True,
) -> dict:
    """
    全量训练并保存 ML ensemble 模型 + 元数据。

    Parameters
    ----------
    factors : dict
        因子面板 {factor_name: DataFrame (dates × stocks)}
    close_panel : DataFrame
        收盘价面板 (dates × stocks)
    model_dir : str
        模型保存目录
    profile : str
        策略 profile 名（用于 hybrid 加权 + 选股过滤参数）
    hybrid_alpha : float
        ML 权重 (1-α 为因子权重)
    forward_periods : list
        预测周期标签，默认 [5, 20]
    train_days : int
        训练窗口天数
    stock_names : dict
        股票代码→名称映射（用于行业分散等）

    Returns
    -------
    meta : dict — 元数据（含模型路径、训练日期、策略参数等）
    """
    os.makedirs(model_dir, exist_ok=True)
    forward_periods = forward_periods or [5, 20]

    if verbose:
        print(f"[ML Predictor] 训练开始 | profile={profile} α={hybrid_alpha} fwd={forward_periods}")

    # ── Step 1: 构建特征和标签 ──
    builder = FeatureBuilder(
        forward_periods=forward_periods,
        neutralize=True,
        enhance=False,  # 关闭增强特征（已验证无效）
    )
    X, y_multi, date_idx, code_idx = builder.build(factors, close_panel, stock_names)

    if verbose:
        print(f"[ML Predictor] 特征矩阵: {X.shape[0]} samples, {X.shape[1]} features")

    # ── Step 2: 全量 Ensemble 训练（不做 Walk-Forward，直接用最近 train_days 天） ──
    unique_dates = pd.Series(date_idx.unique()).sort_values().values
    n_dates = len(unique_dates)

    if n_dates < train_days:
        raise ValueError(f"数据不足: {n_dates} < train_days={train_days}")

    # 取最近 train_days 天作为训练集
    train_start = max(0, n_dates - train_days)
    train_dates = unique_dates[train_start:]
    train_mask = date_idx.isin(train_dates).values

    primary_fp = forward_periods[0]
    key = f'fp_{primary_fp}'
    if key not in y_multi or len(y_multi[key]) == 0:
        raise ValueError(f"标签 {key} 为空")

    y_all = y_multi[key]
    X_train = X[train_mask].copy()
    y_train = y_all[train_mask].copy()
    feature_cols = X_train.columns.tolist()

    if verbose:
        print(f"[ML Predictor] 训练集: {X_train.shape[0]} samples, "
              f"latest train date={train_dates[-1]}")

    # 截面标准化（保存均值/方差供推理时使用）
    feat_mean = X_train.mean()
    feat_std = X_train.std().replace(0, 1.0)
    X_train_norm = (X_train - feat_mean) / feat_std

    # ── 训练三个模型 ──
    import lightgbm as lgb
    import xgboost as xgb
    from sklearn.linear_model import Ridge

    lgb_params = {
        'objective': 'regression_l1', 'metric': 'mae',
        'learning_rate': 0.05, 'num_leaves': 63, 'min_data_in_leaf': 20,
        'feature_fraction': 0.8, 'bagging_fraction': 0.8, 'bagging_freq': 5,
        'lambda_l1': 0.1, 'lambda_l2': 1.0, 'verbose': -1,
    }
    xgb_params = {
        'objective': 'reg:squarederror', 'eval_metric': 'mae',
        'learning_rate': 0.05, 'max_depth': 6, 'subsample': 0.8,
        'colsample_bytree': 0.8, 'reg_alpha': 0.1, 'reg_lambda': 1.0,
        'verbosity': 0,
    }

    models = {}
    train_preds = {}

    # LightGBM
    try:
        # 拆分验证集（最后 63 天 ≈ 1/4 训练集）
        _n = len(X_train_norm)
        _split = max(_n - 63 * len(X_train_norm) // 252, int(_n * 0.8))
        _X_tr = X_train_norm.iloc[:_split]
        _y_tr = y_train.iloc[:_split]
        _X_val = X_train_norm.iloc[_split:]
        _y_val = y_train.iloc[_split:]

        if len(_X_val) < 100:
            # 数据量不够时不做 early stopping
            lgb_model = lgb.train(
                lgb_params,
                lgb.Dataset(X_train_norm, label=y_train),
                num_boost_round=200,
                callbacks=[lgb.log_evaluation(period=0)],
            )
        else:
            lgb_model = lgb.train(
                lgb_params,
                lgb.Dataset(_X_tr, label=_y_tr),
                num_boost_round=500,
                valid_sets=[lgb.Dataset(_X_val, label=_y_val)],
                callbacks=[lgb.early_stopping(50), lgb.log_evaluation(period=0)],
            )
        models['lgb'] = lgb_model
        train_preds['lgb'] = lgb_model.predict(X_train_norm)
        if verbose:
            ic = np.corrcoef(y_train.values, train_preds['lgb'])[0, 1]
            print(f"  LGB 训练 IC: {ic:.4f}")
    except Exception as e:
        if verbose:
            print(f"  LGB 训练失败: {e}")

    # XGBoost
    try:
        xgb_model = xgb.train(
            xgb_params,
            xgb.DMatrix(X_train_norm, label=y_train),
            num_boost_round=500,
            verbose_eval=False,
        )
        models['xgb'] = xgb_model
        train_preds['xgb'] = xgb_model.predict(xgb.DMatrix(X_train_norm))
        if verbose:
            ic = np.corrcoef(y_train.values, train_preds['xgb'])[0, 1]
            print(f"  XGB 训练 IC: {ic:.4f}")
    except Exception as e:
        if verbose:
            print(f"  XGB 训练失败: {e}")

    # Ridge
    try:
        ridge_model = Ridge(alpha=1.0)
        ridge_model.fit(X_train_norm, y_train)
        models['ridge'] = ridge_model
        train_preds['ridge'] = ridge_model.predict(X_train_norm)
        if verbose:
            ic = np.corrcoef(y_train.values, train_preds['ridge'])[0, 1]
            print(f"  Ridge 训练 IC: {ic:.4f}")
    except Exception as e:
        if verbose:
            print(f"  Ridge 训练失败: {e}")

    if not models:
        raise RuntimeError("所有模型训练失败")

    # ── Stacking 权重（OLS with positive constraint） ──
    if len(models) >= 2:
        from sklearn.linear_model import LinearRegression
        stack_X = np.column_stack(list(train_preds.values()))
        meta = LinearRegression(positive=True, fit_intercept=False).fit(
            stack_X, y_train.values
        )
        stacking_weights = dict(zip(train_preds.keys(), meta.coef_))
    else:
        stacking_weights = {list(models.keys())[0]: 1.0}

    if verbose:
        print(f"  Stacking 权重: {stacking_weights}")

    # ── 保存模型和元数据 ──
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    model_paths = {}
    for name, model in models.items():
        path = os.path.join(model_dir, f"{name}_{timestamp}.pkl")
        with open(path, 'wb') as f:
            pickle.dump(model, f)
        model_paths[name] = path

    # 标准化参数
    scaler_path = os.path.join(model_dir, f"scaler_{timestamp}.pkl")
    with open(scaler_path, 'wb') as f:
        pickle.dump({'mean': feat_mean, 'std': feat_std, 'feature_cols': feature_cols}, f)

    # 元数据
    meta = {
        'timestamp': timestamp,
        'model_paths': model_paths,
        'scaler_path': scaler_path,
        'stacking_weights': stacking_weights,
        'profile': profile,
        'hybrid_alpha': hybrid_alpha,
        'forward_periods': forward_periods,
        'train_days': train_days,
        'n_train_samples': len(X_train),
        'train_end_date': str(train_dates[-1]),
        'model_names': list(models.keys()),
        'feature_cols': feature_cols,
    }

    meta_path = os.path.join(model_dir, f"meta_{timestamp}.json")
    with open(meta_path, 'w') as f:
        json.dump(meta, f, indent=2, default=str)

    # 保存最新的元数据引用
    latest_link = os.path.join(model_dir, "latest.json")
    with open(latest_link, 'w') as f:
        json.dump(meta, f, indent=2, default=str)

    if verbose:
        print(f"[ML Predictor] 模型已保存 → {model_dir}/meta_{timestamp}.json")

    return meta


# ──────────────────────────────────────────────────────────────
# 在线推理（模拟盘用）
# ──────────────────────────────────────────────────────────────

class MLPredictor:
    """
    ML 在线推理器。

    加载离线训练好的模型，对当日因子截面做预测。
    输入格式兼容 score_all_stocks() 的输出（逐股因子 dict）。

    Usage:
        predictor = MLPredictor(model_dir=os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "ml_models")
        scores = predictor.predict(all_factors)  # {code: score}
    """

    def __init__(self, model_dir: str = os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "ml_models")):
        self.model_dir = model_dir
        self.models = {}
        self.scaler = None
        self.meta = None
        self._load_latest()

    def _load_latest(self):
        """加载最新模型"""
        latest_path = os.path.join(self.model_dir, "latest.json")
        if not os.path.exists(latest_path):
            raise FileNotFoundError(f"未找到模型元数据: {latest_path}，请先运行 train_and_save()")

        with open(latest_path) as f:
            self.meta = json.load(f)

        with open(self.meta['scaler_path'], 'rb') as f:
            self.scaler = pickle.load(f)

        for name, path in self.meta['model_paths'].items():
            if os.path.exists(path):
                with open(path, 'rb') as f:
                    self.models[name] = pickle.load(f)

        if not self.models:
            raise RuntimeError("没有成功加载任何模型")

        print(f"[ML Predictor] 已加载模型: {list(self.models.keys())} "
              f"(trained={self.meta['train_end_date']})")

    def predict(self, all_factors: Dict[str, Dict[str, float]]) -> Dict[str, float]:
        """
        对当日截面做 ML 预测。

        Parameters
        ----------
        all_factors : dict
            {code: {factor_name: float}}  — 每个股票的因子值

        Returns
        -------
        scores : dict
            {code: ml_score}  — 预测值（越高越好）
        """
        feature_cols = self.scaler['feature_cols']
        feat_mean = self.scaler['mean']
        feat_std = self.scaler['std']

        # 构建特征矩阵
        codes = []
        X_rows = []
        for code, factors in all_factors.items():
            row = [factors.get(c, np.nan) for c in feature_cols]
            codes.append(code)
            X_rows.append(row)

        if not codes:
            return {}

        X = pd.DataFrame(X_rows, columns=feature_cols)
        # 截面标准化（与训练时一致）
        X = (X - feat_mean) / feat_std
        X = X.fillna(0)

        # 各模型预测
        model_preds = {}
        for name, model in self.models.items():
            try:
                if name == 'lgb':
                    model_preds[name] = model.predict(X)
                elif name == 'xgb':
                    import xgboost as xgb
                    model_preds[name] = model.predict(xgb.DMatrix(X))
                elif name == 'ridge':
                    model_preds[name] = model.predict(X)
                else:
                    model_preds[name] = model.predict(X)
            except Exception:
                pass

        if not model_preds:
            return {code: 0.0 for code in codes}

        # Stacking 融合
        sw = self.meta.get('stacking_weights', {})
        total_w = sum(sw.get(m, 1.0) for m in model_preds)
        fused = np.zeros(len(codes))
        for name, pred in model_preds.items():
            w = sw.get(name, 1.0) / total_w
            fused += w * pred

        return {code: float(score) for code, score in zip(codes, fused)}

    @property
    def hybrid_alpha(self) -> float:
        return self.meta.get('hybrid_alpha', 1.0)

    @property
    def profile(self) -> str:
        return self.meta.get('profile', 'v6b_8f_pos_ic')