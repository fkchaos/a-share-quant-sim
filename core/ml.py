"""
ML Rolling Training Engine v2
=============================

LightGBM 滚动训练 + Walk-Forward 回测核心模块（增强版）。

四大优化方向（v2）：
  1. 多周期标签融合    — 5d/20d/60d 三周期预测，IC 加权融合
  2. 因子分组训练      — 动量/反转/波动率/量能 4 组独立 GBDT，stacking
  3. Regime Switching  — 市场状态识别（牛/熊/震荡）× 差异化模型参数
  4. 特征增强          — 行业 one-hot + 市值分位数 + 因子交互项

使用：
  from core.ml import FeatureBuilder, RollingTrainer, run_ml_pipeline
"""

import numpy as np
import pandas as pd
from typing import Optional, Tuple, Dict, List

# ============================================================
# 常量
# ============================================================

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
    # 方案4: 波动率+振幅+均线偏离（中长周期）
    'rev_1d', 'std_60', 'roc_60', 'amp_trend', 'ma60_dev',
]

# 因子分组（用于分组训练 stacking）
FACTOR_GROUPS = {
    'momentum': [
        'mom_5', 'mom_10', 'mom_20', 'mom_60', 'mom_120',
        'rel_strength_20', 'rel_strength_60', 'vwap_mom', 'macd_12_26', 'macd_5_35',
    ],
    'reversal': [
        'rev_3', 'rev_5', 'rev_10', 'rsi_6', 'rsi_14', 'rsi_28',
        'boll_pos_10', 'boll_pos_20',
    ],
    'volatility': [
        'vol_10', 'vol_20', 'vol_60', 'vol_change',
        'atr_14', 'skew_20', 'kurt_20', 'amplitude', 'boll_width_20',
        'high_low_range', 'intraday_drift', 'chip_kurt',
    ],
    'volume': [
        'vol_ratio_5', 'vol_ratio_20', 'amount_ratio',
        'turnover_skew', 'turnover_change', 'price_impact',
        'pv_corr', 'illiquidity', 'obv_slope', 'gap_ratio',
    ],
    # 小市值单独一组（风格因子）
    'style': ['small_cap'],
    # 方案4: 中长周期（波动率+振幅+均线偏离）
    'mid_long': [
        'rev_1d', 'std_60', 'roc_60', 'amp_trend', 'ma60_dev',
    ],
}

# 市场状态（regime）参数
REGIME_PARAMS = {
    'bull': {       # 牛市：更激进，更深的树
        'num_leaves': 127,
        'learning_rate': 0.08,
        'min_data_in_leaf': 10,
    }, 'bear': {    # 熊市：更保守，浅层 + 强正则
        'num_leaves': 31,
        'learning_rate': 0.03,
        'min_data_in_leaf': 40,
    }, 'choppy': {  # 震荡市：中等
        'num_leaves': 63,
        'learning_rate': 0.05,
        'min_data_in_leaf': 20,
    },
}


def _factor_names_available(factors: dict) -> list:
    """返回因子面板中实际可用的因子名"""
    names = []
    for name in ALL_FACTOR_NAMES:
        if name in factors and isinstance(factors[name], pd.DataFrame):
            if factors[name].shape[0] > 0 and factors[name].shape[1] > 0:
                names.append(name)
    return names


def _available_factor_group(factors: dict) -> dict:
    """返回当前数据中实际可用的因子分组"""
    available = _factor_names_available(factors)
    groups = {}
    for gname, gfactors in FACTOR_GROUPS.items():
        valid = [f for f in gfactors if f in available]
        if len(valid) >= 2:  # 组内至少 2 个因子才有意义
            groups[gname] = valid
    return groups


# ============================================================
# 1. FeatureBuilder v2
# ============================================================

class FeatureBuilder:
    """从因子面板构建 ML 特征矩阵和标签（增强版）。

    新增功能：
      - 多周期标签：同时生成 forward=5/20/60 三个标签
      - 特征增强：行业 one-hot + 市值分位数 + 因子交互
      - 增强因子添加到 X_extra（与原始因子 concat）
    """

    def __init__(
        self,
        forward_periods: list = None,
        neutralize: bool = True,
        enhance: bool = True,
    ):
        """
        Parameters
        ----------
        forward_periods : list of int
            多周期标签，默认 [5, 20, 60]
        neutralize : bool
            标签截面去均值
        enhance : bool
            是否添加增强特征（行业 one-hot + 市值分位数 + 因子交互）
        """
        self.forward_periods = forward_periods or [5, 20, 60]
        self.neutralize = neutralize
        self.enhance = enhance

    def build(
        self,
        factors: dict,
        close_panel: pd.DataFrame,
        stock_names: Optional[dict] = None,
    ) -> Tuple[pd.DataFrame, Dict[str, pd.Series], pd.Series, pd.Series]:
        """构建特征和标签。

        Returns
        -------
        X : DataFrame (samples × features)
        y_multi : dict[str, Series] — {'fwd_5': ..., 'fwd_20': ..., 'fwd_60': ...}
        date_index : Series — 每行的日期
        code_index : Series — 每行的股票代码
        """
        factor_names = _factor_names_available(factors)
        if not factor_names:
            raise ValueError("⚠️  No valid factor DataFrames found in factors dict.")

        # 共同日期（所有因子对齐）
        common_dates = close_panel.index
        for name in factor_names:
            f = factors[name]
            if isinstance(f, pd.DataFrame):
                common_dates = common_dates.intersection(f.index)
        common_dates = common_dates.sort_values()

        # 去掉最后 max(forward_periods) 天（无法计算标签）
        max_fwd = max(self.forward_periods)
        label_cutoff = common_dates[-1] - pd.Timedelta(days=max_fwd * 2)
        feature_dates = common_dates[common_dates <= label_cutoff]

        print(f"  FeatureBuilder v2: {len(factor_names)} factors × "
              f"{len(feature_dates)} dates × ~{close_panel.shape[1]} stocks")
        print(f"  Multi-period labels: {self.forward_periods}")
        print(f"  Enhanced features: {self.enhance}")

        rows_X = []
        rows_y = {f'fp_{fp}': [] for fp in self.forward_periods}
        rows_date = []
        rows_code = []

        # 市值分位数预计算（如果需要增强特征）
        cap_panel = factors.get('small_cap')  # small_cap = -log(market_cap)，负值越大=市值越小

        for i, date in enumerate(feature_dates):
            # 收集当日所有因子截面
            date_features = {}
            skip = False
            for name in factor_names:
                f = factors[name]
                if date in f.index:
                    vals = f.loc[date]
                    if isinstance(vals, pd.Series):
                        date_features[name] = vals
                    else:
                        skip = True
                        break
                else:
                    skip = True
                    break
            if skip:
                continue

            feat_df = pd.DataFrame(date_features)
            valid_stocks = feat_df.dropna(how='all').index

            # 每个日期、每个 forward_period 都产生一个标签
            all_valid = set(valid_stocks)
            close_today = close_panel.loc[date]

            y_per_fp = {}
            for fp in self.forward_periods:
                fwd_idx = close_panel.index.get_loc(date) + fp
                if fwd_idx >= len(close_panel):
                    all_valid = set()  # 任何 fp 不可用→跳过整行
                    break
                future_date = close_panel.index[fwd_idx]
                close_future = close_panel.loc[future_date]
                fwd_ret = (close_future / close_today - 1).reindex(valid_stocks)
                if self.neutralize:
                    fwd_ret = fwd_ret - fwd_ret.mean()
                y_per_fp[fp] = fwd_ret
                all_valid = all_valid & set(fwd_ret.dropna().index)

            if len(all_valid) < 10:
                continue

            all_valid = sorted(all_valid)
            feat_valid = feat_df.loc[all_valid]

            # === 特征增强 ===
            if self.enhance:
                feat_valid = self._add_enhanced_features(
                    feat_valid, all_valid, cap_panel, date, stock_names
                )

            rows_X.append(feat_valid)
            for fp in self.forward_periods:
                if fp in y_per_fp:
                    rows_y[f'fp_{fp}'].append(y_per_fp[fp][all_valid])
            rows_date.append(pd.Series(date, index=all_valid))
            rows_code.append(pd.Series(all_valid, index=all_valid))

        if not rows_X:
            raise ValueError("⚠️  No valid samples generated — check date alignment")

        X_raw = pd.concat(rows_X, axis=0)
        y_multi = {}
        for fp in self.forward_periods:
            key = f'fp_{fp}'
            if rows_y[key]:
                y_multi[key] = pd.concat(rows_y[key], axis=0)
            else:
                y_multi[key] = pd.Series(dtype=float)

        date_all = pd.concat(rows_date, axis=0)
        code_all = pd.concat(rows_code, axis=0)

        # 截面标准化
        X = self._cross_sectional_normalize(X_raw, date_all)
        X = X.fillna(0)

        print(f"  FeatureBuilder: {X.shape[0]} samples, {X.shape[1]} features")
        for fp in self.forward_periods:
            key = f'fp_{fp}'
            if key in y_multi:
                print(f"    y_{fp}d: mean={y_multi[key].mean():.6f}, "
                      f"std={y_multi[key].std():.6f}")

        return X, y_multi, date_all, code_all

    def _add_enhanced_features(
        self,
        feat_df: pd.DataFrame,
        valid_stocks: list,
        cap_panel: pd.DataFrame,
        date,
        stock_names: dict = None,
    ) -> pd.DataFrame:
        """添加精简增强特征：cap_quantile + 两项交互"""
        extras = pd.DataFrame(index=feat_df.index)

        # ① 市值分位数
        if cap_panel is not None and date in cap_panel.index:
            cap_rank = cap_panel.loc[date].rank(pct=True)
            extras['cap_quantile'] = cap_rank.reindex(valid_stocks).fillna(0.5)
        else:
            extras['cap_quantile'] = 0.5

        # ② 动量-反转交互（mom_20 × rev_5）：捕捉动量与反转的博弈
        if 'mom_20' in feat_df.columns and 'rev_5' in feat_df.columns:
            extras['mom_rev_interact'] = feat_df['mom_20'] * feat_df['rev_5']

        # ③ 波动率-量能交互（vol_20 × vol_ratio_5）：异动信号
        if 'vol_20' in feat_df.columns and 'vol_ratio_5' in feat_df.columns:
            extras['vol_volr_interact'] = feat_df['vol_20'] * feat_df['vol_ratio_5']

        # ④ RSI 背离（rsi_6 - rsi_14）：短中期 RSI 差值
        if 'rsi_6' in feat_df.columns and 'rsi_14' in feat_df.columns:
            extras['rsi_divergence'] = feat_df['rsi_6'] - feat_df['rsi_14']

        return pd.concat([feat_df, extras], axis=1)

    def _cross_sectional_normalize(
        self, X: pd.DataFrame, date_index: pd.Series
    ) -> pd.DataFrame:
        """按日期截面 z-score 标准化"""
        X_norm = X.copy()
        for date in pd.Series(date_index.unique()).sort_values():
            mask = date_index == date
            if mask.sum() == 0:
                continue
            day_X = X.loc[mask]
            mean = day_X.mean()
            std = day_X.std().replace(0, np.nan)
            X_norm.loc[mask] = day_X.sub(mean).div(std).fillna(0)
        return X_norm


# ============================================================
# 2. Regime Detector
# ============================================================

class RegimeDetector:
    """市场状态识别器。

    使用两个信号：
      - 市场趋势：沪深300（或全市场均值）20日均线 vs 60日均线
      - 市场波动率：全市场收益 20 日滚动标准差

    状态定义：
      - bull:   20ma > 60ma AND vol < vol_threshold_high
      - bear:   20ma < 60ma AND vol > vol_threshold_low
      - choppy: 其他情况
    """

    def __init__(
        self,
        vol_window: int = 20,
        vol_threshold_high: float = 0.25,
        vol_threshold_low: float = 0.15,
    ):
        self.vol_window = vol_window
        self.vol_high = vol_threshold_high
        self.vol_low = vol_threshold_low

    def detect(self, close_panel: pd.DataFrame) -> pd.Series:
        """返回每个交易日的市场状态 ('bull'/'bear'/'choppy')"""
        # 截面均值收益（近似市场收益）
        market_ret = close_panel.mean(axis=1).pct_change()
        market_price = (1 + market_ret).cumsum()  # 近似净值

        # 均线
        ma20 = market_price.rolling(20).mean()
        ma60 = market_price.rolling(60).mean()

        # 波动率
        vol = market_ret.rolling(self.vol_window).std() * np.sqrt(252)

        regime = pd.Series('choppy', index=close_panel.index, dtype=str)
        bull_mask = (ma20 > ma60) & (vol < self.vol_high)
        bear_mask = (ma20 < ma60) & (vol > self.vol_low)
        regime[bull_mask] = 'bull'
        regime[bear_mask] = 'bear'

        # 统计
        counts = regime.value_counts()
        print(f"  Regime distribution: bull={counts.get('bull',0)}, "
              f"bear={counts.get('bear',0)}, choppy={counts.get('choppy',0)}")

        return regime


# ============================================================
# 3. RollingTrainer v2
# ============================================================

class RollingTrainer:
    """Walk-Forward 滚动训练引擎（增强版）。

    新增：
      - 多周期标签融合训练
      - 因子分组 stacking
      - Regime 感知参数
    """

    def __init__(
        self,
        train_days: int = 252,
        test_days: int = 63,
        step_days: int = 63,
        min_train_samples: int = 5000,
        forward_periods: list = None,
        use_group_stacking: bool = True,
        use_regime: bool = True,
        lgb_params: dict = None,
        regime_params: dict = None,
    ):
        self.train_days = train_days
        self.test_days = test_days
        self.step_days = step_days
        self.min_train_samples = min_train_samples
        self.forward_periods = forward_periods or [5, 20, 60]
        self.use_group_stacking = use_group_stacking
        self.use_regime = use_regime

        self.lgb_base = lgb_params or {
            'objective': 'regression_l1',
            'metric': 'mae',
            'learning_rate': 0.05,
            'num_leaves': 63,
            'min_data_in_leaf': 20,
            'feature_fraction': 0.8,
            'bagging_fraction': 0.8,
            'bagging_freq': 5,
            'lambda_l1': 0.1,
            'lambda_l2': 1.0,
            'verbose': -1,
        }
        self.regime_overrides = regime_params or REGIME_PARAMS

    def run(
        self,
        X: pd.DataFrame,
        y_multi: Dict[str, pd.Series],
        date_index: pd.Series,
        code_index: pd.Series,
        regime_series: Optional[pd.Series] = None,
    ) -> Tuple[pd.Series, List[dict]]:
        """执行 Walk-Forward 多周期+分组+regime 滚动训练。

        Parameters
        ----------
        X, y_multi : FeatureBuilder.build() 的输出
            y_multi = {'fp_5': Series, 'fp_20': Series, 'fp_60': Series}
        date_index, code_index : 每样本的日期和股票代码
        regime_series : 每日市场状态 ('bull'/'bear'/'choppy')，需要 date_index 对齐

        Returns
        -------
        predictions : Series — 融合后预测值
        fold_info   : list[dict]
        """
        import lightgbm as lgb

        unique_dates = pd.Series(date_index.unique()).sort_values().values
        n_dates = len(unique_dates)
        max_fwd = max(self.forward_periods)

        if n_dates < self.train_days + self.test_days:
            print(f"  ⚠️  {n_dates} days < train+test, skip ML")
            return pd.Series(np.nan, index=y_multi[f'fp_{self.forward_periods[0]}'].index), []

        # 计算融合权重（EWMA 滚动 IC，半衰期=2 folds）
        fp_weights = {fp: 1.0 / len(self.forward_periods) for fp in self.forward_periods}
        fp_ic_history = {fp: [] for fp in self.forward_periods}
        ewma_alpha = 0.3  # EWMA 衰减因子

        predictions = pd.Series(np.nan, index=y_multi[f'fp_{max_fwd}'].index, name='pred')
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

            train_mask = date_index.isin(train_dates).values
            test_mask = date_index.isin(test_dates).values

            if train_mask.sum() < self.min_train_samples:
                train_end_idx += self.step_days
                continue

            # 当前 fold 的 regime（取训练期最后一天的状态）
            train_end_date = train_dates[-1]
            current_regime = 'choppy'
            if self.use_regime and regime_series is not None and train_end_date in regime_series.index:
                current_regime = regime_series.loc[train_end_date]

            # == 针对每个 forward_period 训练模型 ==
            fold_preds = {}      # {fp: pred_array}
            fold_train_ics = {}  # {fp: ic}
            fold_test_ics = {}   # {fp: ic}

            for fp in self.forward_periods:
                key = f'fp_{fp}'
                if key not in y_multi or len(y_multi[key]) == 0:
                    continue

                y_all = y_multi[key]

                X_train = X[train_mask]
                y_train = y_all[train_mask]
                X_test = X[test_mask]
                y_test = y_all[test_mask]

                if len(X_train) < 100 or len(X_test) < 20:
                    continue

                # 差异化参数
                params = dict(self.lgb_base)
                if current_regime in self.regime_overrides:
                    params.update(self.regime_overrides[current_regime])

                # == 分组 stacking（可选）==
                if self.use_group_stacking:
                    y_pred_test, y_pred_train = self._group_stacking_train(
                        X_train, y_train, X_test, params, X.columns.tolist()
                    )
                else:
                    train_data = lgb.Dataset(X_train, label=y_train)
                    model = lgb.train(
                        params, train_data,
                        num_boost_round=500,
                        valid_sets=[lgb.Dataset(X_test, label=y_test)],
                        callbacks=[lgb.early_stopping(50), lgb.log_evaluation(period=0)],
                    )
                    y_pred_test = model.predict(X_test)
                    y_pred_train = model.predict(X_train)

                train_ic = np.corrcoef(y_train.values, y_pred_train)[0, 1] if len(y_train) > 10 else 0
                test_ic = np.corrcoef(y_test.values, y_pred_test)[0, 1] if len(y_test) > 10 else 0

                fold_preds[fp] = y_pred_test
                fold_train_ics[fp] = train_ic
                fold_test_ics[fp] = test_ic

                # EWMA 更新权重
                fp_ic_history[fp].append(test_ic)
                # 越新的 IC 权重越大
                weights_ic = fp_ic_history[fp]
                n_ic = len(weights_ic)
                ewma_weights = [ewma_alpha * (1 - ewma_alpha) ** (n_ic - 1 - i) for i in range(n_ic)]
                ewma_ic = sum(w * ic for w, ic in zip(ewma_weights, weights_ic))
                fp_weights[fp] = max(0.01, ewma_ic)  # 防止负权重，最小 0.01

            if not fold_preds:
                train_end_idx += self.step_days
                continue

            # == 融合多周期预测（IC 加权）==
            weight_sum = sum(fp_weights.get(fp, 0) for fp in fold_preds.keys())
            if weight_sum == 0:
                weight_sum = 1.0

            n_test = sum(len(v) for v in fold_preds.values()) // len(fold_preds)
            fused_pred = np.zeros(n_test)
            for fp, pred in fold_preds.items():
                w = fp_weights.get(fp, 0) / weight_sum
                fused_pred += w * pred[:n_test]

            pred_positions = np.where(test_mask)[0][:n_test]
            common_idx = predictions.iloc[pred_positions].index[:n_test]
            predictions.iloc[pred_positions[:n_test]] = fused_pred

            # fold 信息
            avg_train_ic = np.mean(list(fold_train_ics.values())) if fold_train_ics else 0
            avg_test_ic = np.mean(list(fold_test_ics.values())) if fold_test_ics else 0
            fp_ic_str = ", ".join(f"{fp}d_ic={fold_test_ics.get(fp,0):.4f}" for fp in fold_preds)

            info = {
                'fold': fold,
                'test_dates': f"{test_dates[0]}~{test_dates[-1]}",
                'regime': current_regime,
                'train_samples': int(train_mask.sum()),
                'test_samples': int(test_mask.sum()),
                'train_ic': round(avg_train_ic, 4),
                'test_ic': round(avg_test_ic, 4),
                'fp_ics': fp_ic_str,
                'fp_weights': str({fp: round(fp_weights[fp], 3) for fp in fold_preds}),
            }
            fold_info.append(info)

            print(f"  Fold {fold} [{current_regime:5s}]: {info['test_dates']} | "
                  f"train_ic={avg_train_ic:.4f} test_ic={avg_test_ic:.4f}")

            train_end_idx += self.step_days

        return predictions, fold_info

    def _group_stacking_train(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_test: pd.DataFrame,
        params: dict,
        all_feature_names: list,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """分组 stacking：每个因子组训练一个子模型，输出 stacking 到 meta-learner"""
        import lightgbm as lgb

        groups = _available_factor_group({k: pd.DataFrame() for k in all_feature_names})
        if len(groups) < 2:
            # 因子不够分组，退化为单模型
            train_data = lgb.Dataset(X_train, label=y_train)
            model = lgb.train(
                params, train_data,
                num_boost_round=500,
                valid_sets=[lgb.Dataset(X_test, label=y_train.iloc[:len(X_test)])],
                callbacks=[lgb.early_stopping(50), lgb.log_evaluation(period=0)],
            )
            return model.predict(X_test), model.predict(X_train)

        group_train_preds = []
        group_test_preds = []
        sub_rounds = max(50, params.get('num_boost_round', 500) // len(groups))

        for gname, gfactors in groups.items():
            # 找到该组因子在 X 中的列
            if isinstance(gfactors, str):
                gfactors = [gfactors]
            valid_cols = [c for c in gfactors if c in X_train.columns]
            if len(valid_cols) < 2:
                continue

            X_tr = X_train[valid_cols]
            X_te = X_test[valid_cols]

            tr_data = lgb.Dataset(X_tr, label=y_train)
            sub_model = lgb.train(
                params, tr_data,
                num_boost_round=sub_rounds,
                callbacks=[lgb.log_evaluation(period=0)],
            )
            group_train_preds.append(sub_model.predict(X_tr))
            group_test_preds.append(sub_model.predict(X_te))

        if not group_train_preds:
            # 退化
            train_data = lgb.Dataset(X_train, label=y_train)
            model = lgb.train(params, train_data, num_boost_round=200,
                              callbacks=[lgb.log_evaluation(period=0)])
            return model.predict(X_test), model.predict(X_train)

        # Meta-learner：简单的平均 stacking
        meta_train = np.mean(group_train_preds, axis=0)
        meta_test = np.mean(group_test_preds, axis=0)

        return meta_test, meta_train


# ============================================================
# 4. ml_score_panel — 预测 → 选股面板
# ============================================================

# ============================================================
# 4b. Ensemble Trainer (LGB + XGB + Ridge)
# ============================================================

class EnsembleTrainer:
    """三模型 ensemble：LightGBM + XGBoost + Ridge。

    每个模型独立预测股票收益，然后 stacking 融合。
    Stacking 权重用 OLS 回归（在训练集上拟合最优权重）。

    与 RollingTrainer 接口兼容，直接替换使用。
    """

    def __init__(
        self,
        train_days: int = 252,
        test_days: int = 63,
        step_days: int = 63,
        min_train_samples: int = 5000,
        forward_periods: list = None,
        lgb_params: dict = None,
        xgb_params: dict = None,
        ridge_alpha: float = 1.0,
        stacking_method: str = 'ols',  # 'ols' | 'equal' | 'ic_weighted'
    ):
        self.train_days = train_days
        self.test_days = test_days
        self.step_days = step_days
        self.min_train_samples = min_train_samples
        self.forward_periods = forward_periods or [5, 20]
        self.ridge_alpha = ridge_alpha
        self.stacking_method = stacking_method

        self.lgb_params = lgb_params or {
            'objective': 'regression_l1', 'metric': 'mae',
            'learning_rate': 0.05, 'num_leaves': 63, 'min_data_in_leaf': 20,
            'feature_fraction': 0.8, 'bagging_fraction': 0.8, 'bagging_freq': 5,
            'lambda_l1': 0.1, 'lambda_l2': 1.0, 'verbose': -1,
        }
        self.xgb_params = xgb_params or {
            'objective': 'reg:squarederror', 'eval_metric': 'mae',
            'learning_rate': 0.05, 'max_depth': 6, 'subsample': 0.8,
            'colsample_bytree': 0.8, 'reg_alpha': 0.1, 'reg_lambda': 1.0,
            'verbosity': 0,
        }

    def run(self, X, y_multi, date_index, code_index, regime_series=None):
        """Walk-Forward ensemble 训练"""
        import lightgbm as lgb
        import xgboost as xgb
        from sklearn.linear_model import Ridge

        max_fwd = max(self.forward_periods)
        primary_fp = self.forward_periods[0]  # 用最短的 forward 做主标签

        unique_dates = pd.Series(date_index.unique()).sort_values().values
        n_dates = len(unique_dates)

        if n_dates < self.train_days + self.test_days:
            return pd.Series(np.nan, index=y_multi[f'fp_{primary_fp}'].index), []

        predictions = pd.Series(np.nan, index=y_multi[f'fp_{primary_fp}'].index, name='pred')
        fold_info = []
        fold = 0
        train_end = self.train_days

        while train_end + self.test_days <= n_dates:
            fold += 1
            train_start = max(0, train_end - self.train_days)
            test_start = train_end
            test_end = min(n_dates, train_end + self.test_days)

            train_dates = unique_dates[train_start:train_end]
            test_dates = unique_dates[test_start:test_end]

            train_mask = date_index.isin(train_dates).values
            test_mask = date_index.isin(test_dates).values

            if train_mask.sum() < self.min_train_samples:
                train_end += self.step_days
                continue

            # 用 primary forward 的标签
            key = f'fp_{primary_fp}'
            if key not in y_multi or len(y_multi[key]) == 0:
                train_end += self.step_days
                continue

            y_all = y_multi[key]
            X_train, y_train = X[train_mask], y_all[train_mask]
            X_test, y_test = X[test_mask], y_all[test_mask]

            if len(X_train) < 100 or len(X_test) < 20:
                train_end += self.step_days
                continue

            # --- 训练三个模型 ---
            model_preds = {}
            model_train_preds = {}

            # 1. LightGBM
            try:
                lgb_model = lgb.train(
                    self.lgb_params,
                    lgb.Dataset(X_train, label=y_train),
                    num_boost_round=500,
                    callbacks=[lgb.early_stopping(50), lgb.log_evaluation(period=0)],
                )
                model_preds['lgb'] = lgb_model.predict(X_test)
                model_train_preds['lgb'] = lgb_model.predict(X_train)
            except Exception:
                pass

            # 2. XGBoost
            try:
                xgb_model = xgb.train(
                    self.xgb_params,
                    xgb.DMatrix(X_train, label=y_train),
                    num_boost_round=500,
                    evals=[(xgb.DMatrix(X_test, label=y_test), 'eval')],
                    early_stopping_rounds=50, verbose_eval=False,
                )
                model_preds['xgb'] = xgb_model.predict(xgb.DMatrix(X_test))
                model_train_preds['xgb'] = xgb_model.predict(xgb.DMatrix(X_train))
            except Exception:
                pass

            # 3. Ridge
            try:
                ridge_model = Ridge(alpha=self.ridge_alpha)
                ridge_model.fit(X_train, y_train)
                model_preds['ridge'] = ridge_model.predict(X_test)
                model_train_preds['ridge'] = ridge_model.predict(X_train)
            except Exception:
                pass

            if len(model_preds) == 0:
                train_end += self.step_days
                continue

            # --- Stacking 融合 ---
            if self.stacking_method == 'ols' and len(model_preds) >= 2:
                # OLS stacking：在训练集上拟合最优权重
                from sklearn.linear_model import LinearRegression
                stack_X_train = np.column_stack([model_train_preds[m] for m in model_preds])
                stack_X_test = np.column_stack([model_preds[m] for m in model_preds])
                meta = LinearRegression(positive=True).fit(stack_X_train, y_train.values)
                fused_pred = meta.predict(stack_X_test)
                stacking_weights = dict(zip(model_preds.keys(), meta.coef_))
            elif self.stacking_method == 'ic_weighted' and len(model_preds) >= 2:
                # IC 加权
                ics = {}
                for m, pred in model_train_preds.items():
                    ics[m] = max(0.01, np.corrcoef(y_train.values, pred)[0, 1])
                total = sum(ics.values())
                weights = {m: ics[m] / total for m in model_preds}
                fused_pred = sum(weights[m] * model_preds[m] for m in model_preds)
                stacking_weights = weights
            else:
                # 等权
                fused_pred = np.mean(list(model_preds.values()), axis=0)
                stacking_weights = {m: 1.0 / len(model_preds) for m in model_preds}

            # 写入预测
            pred_positions = np.where(test_mask)[0][:len(fused_pred)]
            predictions.iloc[pred_positions] = fused_pred

            # IC: compute train IC from training set fusion
            if self.stacking_method == 'ols' and len(model_train_preds) >= 2:
                from sklearn.linear_model import LinearRegression
                fused_train = LinearRegression(positive=True).fit(
                    np.column_stack(list(model_train_preds.values())),
                    y_train.values
                ).predict(np.column_stack(list(model_train_preds.values())))
            elif self.stacking_method == 'ic_weighted' and len(model_train_preds) >= 2:
                ics = {m: max(0.01, float(np.corrcoef(y_train.values, p)[0, 1]))
                       for m, p in model_train_preds.items()}
                total = sum(ics.values())
                fused_train = sum(ics[m] / total * model_train_preds[m] for m in model_train_preds)
            else:
                fused_train = np.mean(list(model_train_preds.values()), axis=0)

            train_ic = float(np.corrcoef(y_train.values, fused_train)[0, 1]) if len(y_train) > 10 else 0
            test_ic = float(np.corrcoef(y_test.values, fused_pred)[0, 1]) if len(y_test) > 10 else 0

            info = {
                'fold': fold,
                'test_dates': f"{test_dates[0]}~{test_dates[-1]}",
                'models': list(model_preds.keys()),
                'stacking_weights': str({k: round(v, 3) for k, v in stacking_weights.items()}),
                'train_ic': round(float(train_ic), 4),
                'test_ic': round(float(test_ic), 4),
            }
            fold_info.append(info)
            print(f"  Ensemble Fold {fold}: {info['test_dates']} | "
                  f"models={list(model_preds.keys())} test_ic={test_ic:.4f} | "
                  f"weights={stacking_weights}")

            train_end += self.step_days

        return predictions, fold_info


# ============================================================
# 4b. ml_score_panel — 预测 → 选股面板
# ============================================================

def ml_score_panel(
    predictions: pd.Series,
    date_index: pd.Series,
    code_index: pd.Series,
    close_panel: pd.DataFrame,
) -> pd.DataFrame:
    """将 ML 预测值转换为选股评分面板（dates × stocks）"""
    score = pd.DataFrame(index=close_panel.index, columns=close_panel.columns, dtype=float)

    valid_mask = predictions.notna()
    for pred_val, date, code in zip(
        predictions[valid_mask],
        date_index[valid_mask],
        code_index[valid_mask],
    ):
        if date in score.index and code in score.columns:
            score.loc[date, code] = pred_val

    score = score.ffill().fillna(0)
    return score


# ============================================================
# 5. run_ml_pipeline v2 — 端到端流水线
# ============================================================

def run_ml_pipeline(
    factors: dict,
    close_panel: pd.DataFrame,
    train_days: int = 252,
    test_days: int = 63,
    step_days: int = 63,
    forward_periods: list = None,
    use_multi_period: bool = True,
    use_group_stacking: bool = True,
    use_regime: bool = True,
    use_enhanced_features: bool = True,
    lgb_params: dict = None,
    use_ensemble: bool = False,
    ensemble_stacking: str = 'ols',
    regime_params: dict = None,
    stock_names: dict = None,
) -> Tuple[pd.DataFrame, List[dict]]:
    """端到端 ML 流水线（增强版）。

    Parameters
    ----------
    use_multi_period : bool — 多周期标签融合
    use_group_stacking : bool — 因子分组 stacking
    use_regime : bool — regime switching
    use_enhanced_features : bool — 特征增强

    Returns
    -------
    score_panel : DataFrame (dates × stocks)
    fold_info   : list[dict]
    """
    forward_periods = forward_periods or [5, 20, 60]

    # Step 1: 构建特征 + 多周期标签
    print("\n[ML v2] Step 1: Building features with multi-period labels...")
    builder = FeatureBuilder(
        forward_periods=forward_periods if use_multi_period else [forward_periods[0]],
        neutralize=True,
        enhance=use_enhanced_features,
    )
    X, y_multi, date_idx, code_idx = builder.build(factors, close_panel, stock_names)

    # Step 2: Regime detection
    regime_series = None
    if use_regime:
        print("\n[ML v2] Step 2: Detecting market regimes...")
        detector = RegimeDetector()
        regime_series = detector.detect(close_panel)

    # Step 3: Walk-Forward 训练
    if use_ensemble:
        print(f"\n[ML v2] Step 3: Ensemble training (LGB+XGB+Ridge, stacking={ensemble_stacking})...")
        trainer = EnsembleTrainer(
            train_days=train_days, test_days=test_days, step_days=step_days,
            forward_periods=forward_periods if use_multi_period else [forward_periods[0]],
            lgb_params=lgb_params, stacking_method=ensemble_stacking,
        )
    else:
        print(f"\n[ML v2] Step 3: Walk-Forward training "
              f"(group_stacking={use_group_stacking}, regime={use_regime})...")
        trainer = RollingTrainer(
            train_days=train_days, test_days=test_days, step_days=step_days,
            forward_periods=forward_periods if use_multi_period else [forward_periods[0]],
            use_group_stacking=use_group_stacking, use_regime=use_regime,
            lgb_params=lgb_params, regime_params=regime_params,
        )
    predictions, fold_info = trainer.run(X, y_multi, date_idx, code_idx, regime_series)

    # Step 4: → 评分面板
    print("\n[ML v2] Step 4: Converting predictions to score panel...")
    score_panel = ml_score_panel(predictions, date_idx, code_idx, close_panel)

    n_valid = (score_panel.abs().sum(axis=1) > 0).sum()
    print(f"  Score panel: {score_panel.shape[0]}d × {score_panel.shape[1]}s")
    print(f"  Days with predictions: {n_valid}")

    return score_panel, fold_info
