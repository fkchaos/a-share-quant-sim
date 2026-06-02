"""
ML Rolling Training Engine
==========================

LightGBM 滚动训练 + Walk-Forward 回测核心模块。

架构：
  1. FeatureBuilder  — 从因子面板构建 (stock×date) 特征矩阵和标签
  2. RollingTrainer  — Walk-Forward 滚动训练 + 预测
  3. MLSignalEngine  — 把 ML 预测转成选股信号，复用 run_backtest 交易逻辑

使用：
  from core.ml import FeatureBuilder, RollingTrainer, ml_score_panel
"""

import numpy as np
import pandas as pd
from typing import Optional, Tuple

# ============================================================
# 常量
# ============================================================

# 可用作 ML 特征的全部因子名（与 calc_factors_panel 输出一致）
ALL_FACTOR_NAMES = [
    # 动量
    'mom_5', 'mom_10', 'mom_20', 'mom_60', 'mom_120',
    # 反转
    'rev_3', 'rev_5', 'rev_10',
    # 波动率
    'vol_10', 'vol_20', 'vol_60', 'vol_change',
    # 量能
    'vol_ratio_5', 'vol_ratio_20', 'amount_ratio',
    # RSI
    'rsi_6', 'rsi_14', 'rsi_28',
    # MACD
    'macd_12_26', 'macd_5_35',
    # 布林
    'boll_pos_10', 'boll_pos_20', 'boll_width_20',
    # 统计
    'atr_14', 'skew_20', 'kurt_20',
    # VWAP
    'vwap_mom',
    # 相对强度
    'rel_strength_20', 'rel_strength_60',
    # v8 新增
    'amplitude', 'illiquidity', 'turnover_skew',
    'turnover_change', 'price_impact', 'pv_corr',
    'chip_kurt', 'obv_slope',
    # 短线因子
    'gap_ratio', 'high_low_range', 'intraday_drift',
    # 小市值
    'small_cap',
]


def _factor_names_available(factors: dict) -> list:
    """返回当前 factor dict 中实际存在的因子名（排除 small_cap 可能为 nan 的情况）"""
    names = []
    for name in ALL_FACTOR_NAMES:
        if name in factors and isinstance(factors[name], pd.DataFrame):
            if factors[name].shape[0] > 0 and factors[name].shape[1] > 0:
                names.append(name)
    return names


# ============================================================
# 1. FeatureBuilder
# ============================================================

class FeatureBuilder:
    """从因子面板构建 ML 特征矩阵和标签。

    输入：
      factors : {factor_name: DataFrame (dates × stocks)}
      close_panel : DataFrame (dates × stocks)
      stock_names : dict {code: name} 可选，用于行业编码

    输出：
      X : DataFrame (samples × features)，每行 = (stock, date) 的截面因子值
      y : Series (samples,)，标签 = 未来 forward_period 日超额收益
      dates_index : 每个样本的 date
      codes_index : 每个样本的 stock code
    """

    def __init__(
        self,
        forward_period: int = 5,
        neutralize: bool = True,
    ):
        """
        Parameters
        ----------
        forward_period : int
            预测未来 N 日收益（默认 5 天 ≈ 1 周）
        neutralize : bool
            是否截面去均值（转换为超额收益，减少市场 Beta 影响）
        """
        self.forward_period = forward_period
        self.neutralize = neutralize

    def build(
        self,
        factors: dict,
        close_panel: pd.DataFrame,
        stock_names: Optional[dict] = None,
    ) -> Tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series]:
        """构建特征和标签。

        Returns
        -------
        X : DataFrame (samples × features)
        y : Series (samples,) — 未来 N 日超额收益
        date_index : Series — 每行的日期
        code_index : Series — 每行的股票代码
        """
        factor_names = _factor_names_available(factors)
        if not factor_names:
            raise ValueError("⚠️  No valid factor DataFrames found in factors dict.")

        # 获取所有共同日期和股票
        common_dates = close_panel.index
        for name in factor_names:
            f = factors[name]
            if isinstance(f, pd.DataFrame):
                common_dates = common_dates.intersection(f.index)
        # 去掉最后 forward_period 天（无法计算标签）
        max_date = common_dates[-1]
        label_cutoff = max_date - pd.Timedelta(days=self.forward_period * 2)  # 安全余量
        feature_dates = common_dates[common_dates <= label_cutoff]

        print(f"  FeatureBuilder: {len(factor_names)} features × "
              f"{len(feature_dates)} dates × ~{close_panel.shape[1]} stocks")

        rows_X = []
        rows_y = []
        rows_date = []
        rows_code = []

        for i, date in enumerate(feature_dates):
            # 特征：所有股票的因子截面值
            date_features = {}
            skip_date = False
            for name in factor_names:
                f = factors[name]
                if date in f.index:
                    vals = f.loc[date]
                    if isinstance(vals, pd.Series):
                        date_features[name] = vals
                    else:
                        skip_date = True
                        break
                else:
                    skip_date = True
                    break

            if skip_date:
                continue

            feat_df = pd.DataFrame(date_features)
            # 去掉所有特征都缺失的行（股票）
            valid_stocks = feat_df.dropna(how='all').index

            # 计算标签：未来 forward_period 日收益
            close_today = close_panel.loc[date]
            future_date_idx = close_panel.index.get_loc(date) + self.forward_period
            if future_date_idx >= len(close_panel):
                continue
            future_date = close_panel.index[future_date_idx]
            close_future = close_panel.loc[future_date]

            # 截面收益
            future_ret = (close_future / close_today - 1).reindex(valid_stocks)

            # 截面去均值（超额收益）
            if self.neutralize:
                mean_ret = future_ret.mean()
                future_ret = future_ret - mean_ret

            # 去掉 label 为 nan 的行
            valid_mask = future_ret.notna()
            valid_stocks = valid_stocks[valid_mask]
            if len(valid_stocks) < 10:
                continue

            feat_valid = feat_df.loc[valid_stocks]
            ret_valid = future_ret[valid_stocks]

            rows_X.append(feat_valid)
            rows_y.append(ret_valid)
            rows_date.append(pd.Series(date, index=valid_stocks))
            rows_code.append(pd.Series(valid_stocks, index=valid_stocks))

            # 截面标准化特征
            # （每个日期截面独立标准化，消除量纲差异）

        if not rows_X:
            raise ValueError("⚠️  No valid samples generated — check date alignment")

        X_raw = pd.concat(rows_X, axis=0)
        y_all = pd.concat(rows_y, axis=0)
        date_all = pd.concat(rows_date, axis=0)
        code_all = pd.concat(rows_code, axis=0)

        # 截面标准化（按日期分组）
        X = self._cross_sectional_normalize(X_raw, date_all)

        # 对缺失值填 0（截面标准化后均值 ≈ 0）
        X = X.fillna(0)

        print(f"  FeatureBuilder: {X.shape[0]} samples ready "
              f"({X.shape[1]} features)")

        return X, y_all, date_all, code_all

    def _cross_sectional_normalize(
        self, X: pd.DataFrame, date_index: pd.Series
    ) -> pd.DataFrame:
        """按日期截面标准化特征（z-score）。"""
        X_norm = X.copy()
        for date in date_index.unique():
            mask = date_index == date
            day_X = X.loc[mask]
            mean = day_X.mean()
            std = day_X.std().replace(0, np.nan)
            X_norm.loc[mask] = day_X.sub(mean).div(std).fillna(0)
        return X_norm


# ============================================================
# 2. RollingTrainer
# ============================================================

class RollingTrainer:
    """Walk-Forward 滚动训练引擎。

    Parameters
    ----------
    train_days : int — 训练窗口长度（交易日数，默认 252 ≈ 1年）
    test_days  : int — 测试窗口长度（默认 63 ≈ 1季度）
    step_days  : int — 滚动步长（默认 63，等于 test_days）
    min_train_samples : int — 最少训练样本数（少于这个数跳过）
    """

    def __init__(
        self,
        train_days: int = 252,
        test_days: int = 63,
        step_days: int = 63,
        min_train_samples: int = 5000,
        forward_period: int = 5,
        lgb_params: Optional[dict] = None
    ):
        self.train_days = train_days
        self.test_days = test_days
        self.step_days = step_days
        self.min_train_samples = min_train_samples
        self.forward_period = forward_period

        # LightGBM 默认参数（针对金融时序调优）
        self.lgb_params = lgb_params or {
            'objective': 'regression_l1',   # MAE 更鲁棒，对异常值不敏感
            'metric': 'mae',
            'learning_rate': 0.05,
            'num_leaves': 63,
            'min_data_in_leaf': 20,          # 防止过拟合
            'feature_fraction': 0.8,         # 列采样
            'bagging_fraction': 0.8,         # 行采样
            'bagging_freq': 5,
            'lambda_l1': 0.1,
            'lambda_l2': 1.0,
            'verbose': -1,
        }

    def run(
        self,
        X: pd.DataFrame,
        y: pd.Series,
        date_index: pd.Series,
        code_index: pd.Series,
    ) -> Tuple[pd.Series, list]:
        """执行 Walk-Forward 滚动训练。

        Parameters
        ----------
        X, y : FeatureBuilder.build() 的输出
        date_index, code_index : 每个样本对应的日期和股票代码

        Returns
        -------
        predictions : Series (samples,) — 仅测试集的预测值，训练集为 NaN
        fold_info  : list[dict] — 每轮训练信息
        """
        import lightgbm as lgb

        unique_dates = pd.Series(date_index.unique()).sort_values().values
        n_dates = len(unique_dates)

        if n_dates < self.train_days + self.test_days:
            print(f"  ⚠️  {n_dates} days < train({self.train_days}) + test({self.test_days}), skip ML")
            return pd.Series(np.nan, index=y.index), []

        # 预建日期→样本 mask 缓存
        date_masks = {}
        for date in unique_dates:
            date_masks[date] = date_index == date

        # 滚动训练
        predictions = pd.Series(np.nan, index=y.index, name='pred')
        fold_info = []
        fold = 0

        train_end_idx = self.train_days

        while train_end_idx + self.test_days <= n_dates:
            fold += 1
            train_start_idx = max(0, train_end_idx - self.train_days)
            test_start_idx = train_end_idx
            test_end_idx = min(n_dates, train_end_idx + self.test_days)

            train_dates = unique_dates[train_start_idx:train_end_idx]
            test_dates = unique_dates[test_start_idx:test_end_idx]

            # 收集训练样本（用布尔 mask 保留位置信息）
            train_mask = date_index.isin(train_dates).values
            test_mask = date_index.isin(test_dates).values

            X_train = X[train_mask]
            y_train = y[train_mask]
            X_test = X[test_mask]
            y_test = y[test_mask]

            if len(X_train) < self.min_train_samples:
                print(f"  ML Fold {fold}: skip (train samples {len(X_train)} < {self.min_train_samples})")
                train_end_idx += self.step_days
                continue

            # 训练模型
            train_data = lgb.Dataset(X_train, label=y_train)
            model = lgb.train(
                self.lgb_params,
                train_data,
                num_boost_round=500,
                valid_sets=[lgb.Dataset(X_test, label=y_test)],
                callbacks=[
                    lgb.early_stopping(50),
                    lgb.log_evaluation(period=0),  # 静默
                ],
            )

            # 预测测试集 — 按位置写入，避免重复 index 问题
            y_pred = model.predict(X_test)
            pred_positions = np.where(test_mask)[0]
            predictions.iloc[pred_positions] = y_pred

            # 记录 fold 信息
            train_pred = model.predict(X_train)
            train_ic = np.corrcoef(y_train.values, train_pred)[0, 1] if len(y_train) > 10 else 0
            test_ic = np.corrcoef(y_test.values, y_pred)[0, 1] if len(y_test) > 10 else 0

            info = {
                'fold': fold,
                'train_dates': f"{train_dates[0]}~{train_dates[-1]}",
                'test_dates': f"{test_dates[0]}~{test_dates[-1]}",
                'train_samples': len(X_train),
                'test_samples': len(X_test),
                'train_ic': float(train_ic),
                'test_ic': float(test_ic),
                'best_iteration': model.best_iteration,
            }
            fold_info.append(info)

            print(f"  ML Fold {fold}: {info['test_dates']} | "
                  f"train_ic={info['train_ic']:.4f} test_ic={info['test_ic']:.4f} "
                  f"(samples: {len(X_train)}→{len(X_test)})")

            train_end_idx += self.step_days

        return predictions, fold_info


# ============================================================
# 3. MLSignalEngine — 预测 → 选股面板
# ============================================================

def ml_score_panel(
    predictions: pd.Series,
    date_index: pd.Series,
    code_index: pd.Series,
    close_panel: pd.DataFrame,
) -> pd.DataFrame:
    """将 ML 预测值转换为选股评分面板（dates × stocks），格式与 composite_score 兼容。

    Parameters
    ----------
    predictions : RollingTrainer.run() 输出的预测值（仅测试集有值）
    date_index  : 每个样本的日期
    code_index  : 每个样本的股票代码
    close_panel : 用于对齐日期和股票代码

    Returns
    -------
    score_panel : DataFrame (dates × stocks) — ML 预测分数，训练期为 NaN
    """
    score = pd.DataFrame(index=close_panel.index, columns=close_panel.columns, dtype=float)

    # 只遍历非 NaN 的预测值
    valid_mask = predictions.notna()
    valid_preds = predictions[valid_mask]
    valid_dates = date_index[valid_mask]
    valid_codes = code_index[valid_mask]

    for pred_val, date, code in zip(valid_preds, valid_dates, valid_codes):
        if date in score.index and code in score.columns:
            score.loc[date, code] = pred_val

    # 回填：往前填充到下一个有值的日期（作为信号持续直到下次调仓）
    score = score.ffill().fillna(0)

    return score


# ============================================================
# 4. 一次性入口（供 ml_rolling_train.py 调用）
# ============================================================

def run_ml_pipeline(
    factors: dict,
    close_panel: pd.DataFrame,
    train_days: int = 252,
    test_days: int = 63,
    step_days: int = 63,
    forward_period: int = 5,
    lgb_params: Optional[dict] = None,
    stock_names: Optional[dict] = None,
) -> Tuple[pd.DataFrame, list]:
    """端到端 ML 流水线：构建特征 → 滚动训练 → 返回评分面板。

    Returns
    -------
    score_panel : DataFrame (dates × stocks) — ML 选股评分
    fold_info   : list[dict] — 每轮训练信息
    """
    # Step 1: 构建特征和标签
    print("\n[ML Pipeline] Step 1: Building features...")
    builder = FeatureBuilder(forward_period=forward_period, neutralize=True)
    X, y, date_idx, code_idx = builder.build(factors, close_panel, stock_names)

    # Step 2: Walk-Forward 滚动训练
    print(f"\n[ML Pipeline] Step 2: Walk-Forward training "
          f"(train={train_days}d, test={test_days}d, step={step_days}d)...")
    trainer = RollingTrainer(
        train_days=train_days,
        test_days=test_days,
        step_days=step_days,
        forward_period=forward_period,
        lgb_params=lgb_params,
    )
    predictions, fold_info = trainer.run(X, y, date_idx, code_idx)

    # Step 3: 预测 → 评分面板
    print("\n[ML Pipeline] Step 3: Converting predictions to score panel...")
    score_panel = ml_score_panel(predictions, date_idx, code_idx, close_panel)

    n_predicted = score_panel.abs().sum(axis=1) > 0
    print(f"  Score panel: {score_panel.shape[0]} days × {score_panel.shape[1]} stocks")
    print(f"  Days with ML predictions: {n_predicted.sum()}")

    return score_panel, fold_info
