"""
Factor calculation engine.

Supports TWO modes:
  1. Signal mode (single-stock):  df → {factor_name: float}
     Used by sim_daily.py for live scoring.
  2. Backtest mode (panel):       close_panel, volume_panel, amount_panel → {factor_name: DataFrame}
     Used by run_backtest.py for historical backtesting.

The underlying math is IDENTICAL — only the input shape differs.
"""

import numpy as np
import pandas as pd


# ── Single-stock (signal mode) ───────────────────────────────────────

def calc_factors_single(df: pd.DataFrame) -> dict:
    """Calculate all factors for a single stock from its OHLCV DataFrame.

    Input:  DataFrame with columns ['open','high','low','close','volume','amount']
            (amount optional, defaults to close*volume)
    Output: {factor_name: float}  (latest value of each factor)
    """
    close = df['close']
    volume = df.get('volume', pd.Series(1, index=df.index))
    amount = df.get('amount', close * volume)
    returns = close.pct_change()
    eps = 1e-10

    factors = {}

    # Market cap (small-cap factor)
    # outstanding_share is optional; if missing, skip
    if 'outstanding_share' in df.columns:
        osh = df['outstanding_share'].iloc[-1]
        if pd.notna(osh) and osh > 0:
            # 流通市值 = close × 流通股本（单位：元）
            # 取对数后取负值 → 小市值 = 大正因子
            cap = close.iloc[-1] * osh
            factors['market_cap'] = cap
            factors['log_market_cap'] = np.log(cap + eps)
            # 小市值因子：市值的倒数（标准化用）
            factors['small_cap'] = -factors['log_market_cap']
        else:
            factors['small_cap'] = np.nan
    else:
        factors['small_cap'] = np.nan

    # Momentum
    for w in [5, 10, 20, 60, 120]:
        factors[f'mom_{w}'] = close.iloc[-1] / close.iloc[-w] - 1 if len(close) >= w else np.nan

    # Reversal (negative momentum)
    for w in [3, 5, 10]:
        factors[f'rev_{w}'] = -(close.iloc[-1] / close.iloc[-w] - 1) if len(close) >= w else np.nan

    # Volatility (lower is better → negative weight in scoring)
    for w in [10, 20, 60]:
        if len(returns) >= w:
            factors[f'vol_{w}'] = returns.iloc[-w:].std()
        else:
            factors[f'vol_{w}'] = np.nan

    # Volatility change (regime)
    if len(returns) >= 60:
        vol_20 = returns.iloc[-20:].std()
        vol_60 = returns.iloc[-60:].std()
        factors['vol_change'] = vol_20 / (vol_60 + eps)
    else:
        factors['vol_change'] = np.nan

    # Volume ratio
    if len(volume) >= 20:
        factors['vol_ratio_5'] = volume.iloc[-1] / (volume.iloc[-5:].mean() + eps)
        factors['vol_ratio_20'] = volume.iloc[-1] / (volume.iloc[-20:].mean() + eps)
        factors['amount_ratio'] = amount.iloc[-1] / (amount.iloc[-20:].mean() + eps)
    else:
        factors['vol_ratio_5'] = factors['vol_ratio_20'] = factors['amount_ratio'] = np.nan

    # RSI
    for w in [6, 14, 28]:
        if len(returns) >= w:
            g = returns.clip(lower=0).iloc[-w:].mean()
            l = (-returns.clip(upper=0)).iloc[-w:].mean()
            rs = g / (l + eps)
            factors[f'rsi_{w}'] = 100 - (100 / (1 + rs))
        else:
            factors[f'rsi_{w}'] = np.nan

    # MACD
    if len(close) >= 26:
        ema12 = close.ewm(span=12, adjust=False).mean().iloc[-1]
        ema26 = close.ewm(span=26, adjust=False).mean().iloc[-1]
        macd_line = ema12 - ema26
        factors['macd_12_26'] = macd_line * 0.2     # scaled for convenience
    else:
        factors['macd_12_26'] = np.nan

    if len(close) >= 35:
        ema5 = close.ewm(span=5, adjust=False).mean().iloc[-1]
        ema35 = close.ewm(span=35, adjust=False).mean().iloc[-1]
        macd_line = ema5 - ema35
        factors['macd_5_35'] = macd_line * 0.2
    else:
        factors['macd_5_35'] = np.nan

    # Bollinger
    if len(close) >= 20:
        ma20 = close.iloc[-20:].mean()
        std20 = close.iloc[-20:].std()
        factors['boll_pos_20'] = (close.iloc[-1] - ma20 + 2*std20) / (4*std20 + eps)
        factors['boll_width_20'] = (4*std20) / (ma20 + eps)

        ma10 = close.iloc[-10:].mean()
        std10 = close.iloc[-10:].std()
        factors['boll_pos_10'] = (close.iloc[-1] - ma10 + 2*std10) / (4*std10 + eps)
    else:
        factors['boll_pos_10'] = factors['boll_pos_20'] = factors['boll_width_20'] = np.nan

    # ATR (using high-low range)
    if len(close) >= 15:
        high_low = df['high'].rolling(2).max() - df['low'].rolling(2).min() if 'high' in df and 'low' in df else abs(returns)
        factors['atr_14'] = high_low.rolling(14).mean().iloc[-1] / (close.iloc[-1] + eps)
    else:
        factors['atr_14'] = np.nan

    # Skewness & Kurtosis
    if len(returns) >= 20:
        factors['skew_20'] = returns.iloc[-20:].skew()
        factors['kurt_20'] = returns.iloc[-20:].kurt()
    else:
        factors['skew_20'] = factors['kurt_20'] = np.nan

    # VWAP momentum
    if len(close) >= 20 and len(volume) >= 20:
        vol_price = close * volume
        vwap = vol_price.iloc[-20:].mean() / (volume.iloc[-20:].mean() + eps)
        factors['vwap_mom'] = (close.iloc[-1] - vwap) / (close.iloc[-1] + eps)
    else:
        factors['vwap_mom'] = np.nan

    # Relative strength (will be filled in after cross-sectional comparison)
    factors['rel_strength_20'] = factors.get('mom_20', np.nan)
    factors['rel_strength_60'] = factors.get('mom_60', np.nan)

    # ── 短线因子（单股模式）────────────────────────────────────────────
    eps = 1e-10

    # gap_ratio: 跳空比
    if 'open' in df.columns and len(close) >= 2:
        factors['gap_ratio'] = (df['open'].iloc[-1] - close.iloc[-2]) / (close.iloc[-2] + eps)
    else:
        factors['gap_ratio'] = np.nan

    # high_low_range + intraday_drift
    if 'high' in df.columns and 'low' in df.columns and len(df) >= 1:
        h = df['high'].iloc[-1]
        l = df['low'].iloc[-1]
        factors['high_low_range'] = (h - l) / (close.iloc[-1] + eps)
        if 'open' in df.columns:
            factors['intraday_drift'] = (close.iloc[-1] - df['open'].iloc[-1]) / (h - l + eps)
        else:
            factors['intraday_drift'] = np.nan
    else:
        factors['high_low_range'] = np.nan
        factors['intraday_drift'] = np.nan

    # 残差动量（single 版本：用时序回归残差）
    # 用过去 20 日收益率对 size/vol/mom/liquidity 时序回归，取最新残差
    if len(close) >= 20:
        _ret = returns.iloc[-20:].values
        _t = np.arange(20, dtype=float)
        # 简化：用收益率对时间趋势回归，残差 = 去趋势后的收益
        # 更准确的 Barra 风格回归需要截面数据，single 版本用近似
        _t_norm = (_t - _t.mean()) / (_t.std() + eps)
        _beta = np.dot(_t_norm, _ret) / (np.dot(_t_norm, _t_norm) + eps)
        _resid = _ret - _beta * _t_norm
        factors['resid_mom'] = _resid[-1]  # 最新残差
    else:
        factors['resid_mom'] = np.nan

    # 基本面质量因子（从缓存加载）
    import os as _os
    _cache = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "data", "quality_factors_cache.csv")
    if _os.path.exists(_cache):
        try:
            _qc = pd.read_csv(_cache, parse_dates=["日期"], dtype={"code": str})
            _code = df.index.name or "unknown"
            # 从缓存中找该股票的最新质量因子
            _stock_qc = _qc[_qc["code"] == _code].sort_values("日期")
            if len(_stock_qc) > 0:
                _latest = _stock_qc.iloc[-1]
                factors["roe"] = _latest.get("roe", np.nan)
                factors["revenue_yoy"] = _latest.get("revenue_yoy", np.nan)
                factors["profit_yoy"] = _latest.get("profit_yoy", np.nan)
                factors["gross_margin"] = _latest.get("gross_margin", np.nan)
                factors["debt_asset"] = _latest.get("debt_asset", np.nan)
            else:
                factors["roe"] = np.nan
                factors["revenue_yoy"] = np.nan
                factors["profit_yoy"] = np.nan
                factors["gross_margin"] = np.nan
                factors["debt_asset"] = np.nan
        except Exception:
            factors["roe"] = np.nan
            factors["revenue_yoy"] = np.nan
            factors["profit_yoy"] = np.nan
            factors["gross_margin"] = np.nan
            factors["debt_asset"] = np.nan
    else:
        factors["roe"] = np.nan
        factors["revenue_yoy"] = np.nan
        factors["profit_yoy"] = np.nan
        factors["gross_margin"] = np.nan
        factors["debt_asset"] = np.nan

    return factors


# ── Panel mode (same math, vectorized over all stocks) ───────────────

def calc_factors_panel(
    close_panel: pd.DataFrame,
    volume_panel: pd.DataFrame = None,
    amount_panel: pd.DataFrame = None,
    open_panel: pd.DataFrame = None,
    high_panel: pd.DataFrame = None,
    low_panel: pd.DataFrame = None,
    industry_map: dict = None,
) -> dict:
    """Calculate factor matrices for ALL stocks at ALL dates.

    Input:  close_panel  — DataFrame (dates × stocks), adjusted close prices
            volume_panel — DataFrame (dates × stocks), REQUIRED for vol_ratio factors
            amount_panel — DataFrame (dates × stocks), REQUIRED for amount_ratio factor
    Output: {factor_name: DataFrame (dates × stocks)}

    ⚠️  volume_panel and amount_panel are technically optional (for API compat)
    but STRONGLY REQUIRED — without them vol_ratio_5/20 and amount_ratio factors
    will be silently zero, causing score distortion and incorrect backtest results.
    """
    if volume_panel is None or amount_panel is None:
        import warnings
        warnings.warn(
            "⚠️  calc_factors_panel: volume_panel/amount_panel not provided. "
            "vol_ratio and amount_ratio factors will be ZERO — backtest results will be INCORRECT. "
            "Always pass volume_panel and amount_panel from load_and_build_panel().",
            stacklevel=2,
        )
        volume_panel = pd.DataFrame(1.0, index=close_panel.index, columns=close_panel.columns)
        amount_panel = close_panel * volume_panel
    # Note: removed the two separate if-blocks above; they're now merged

    returns = close_panel.pct_change()
    eps = 1e-10
    factors = {}

    # Momentum
    for w in [5, 10, 20, 60, 120]:
        factors[f'mom_{w}'] = close_panel.pct_change(w)

    # Reversal
    for w in [3, 5, 10]:
        factors[f'rev_{w}'] = -close_panel.pct_change(w)

    # Volatility
    for w in [10, 20, 60]:
        factors[f'vol_{w}'] = returns.rolling(w).std()

    factors['vol_change'] = returns.rolling(20).std() / (returns.rolling(60).std() + eps)

    # Volume
    factors['vol_ratio_5'] = volume_panel / (volume_panel.rolling(5).mean() + eps)
    factors['vol_ratio_20'] = volume_panel / (volume_panel.rolling(20).mean() + eps)
    factors['amount_ratio'] = amount_panel / (amount_panel.rolling(20).mean() + eps)

    # RSI
    for w in [6, 14, 28]:
        delta = close_panel.diff()
        gain = delta.where(delta > 0, 0).rolling(w).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(w).mean()
        rs = gain / loss.replace(0, np.nan)
        factors[f'rsi_{w}'] = 100 - (100 / (1 + rs))

    # MACD
    for fast, slow in [(12, 26), (5, 35)]:
        ema_f = close_panel.ewm(span=fast, adjust=False).mean()
        ema_s = close_panel.ewm(span=slow, adjust=False).mean()
        macd_line = ema_f - ema_s
        factors[f'macd_{fast}_{slow}'] = macd_line * 0.2

    # Bollinger
    for w in [10, 20]:
        ma = close_panel.rolling(w).mean()
        std = close_panel.rolling(w).std()
        lower = ma - 2 * std
        upper = ma + 2 * std
        factors[f'boll_pos_{w}'] = (close_panel - lower) / (upper - lower + eps)
    factors['boll_width_20'] = (upper - lower) / (ma + eps)

    # ATR (close-to-close range, simplified)
    ct = close_panel.rolling(2).max() - close_panel.rolling(2).min()
    factors['atr_14'] = ct.rolling(14).mean() / (close_panel + eps)

    # Distribution
    factors['skew_20'] = returns.rolling(20).skew()
    factors['kurt_20'] = returns.rolling(20).kurt()

    # VWAP momentum
    vol_price = close_panel * volume_panel
    vwap = vol_price.rolling(20).mean() / (volume_panel.rolling(20).mean() + eps)
    factors['vwap_mom'] = (close_panel - vwap) / (close_panel + eps)

    # Relative strength (vs cross-sectional mean return)
    cross_mean_20 = close_panel.mean(axis=1).pct_change(20)
    factors['rel_strength_20'] = close_panel.pct_change(20).sub(cross_mean_20, axis=0)
    cross_mean_60 = close_panel.mean(axis=1).pct_change(60)
    factors['rel_strength_60'] = close_panel.pct_change(60).sub(cross_mean_60, axis=0)

    # ── v8 新增因子（去冗余后保留）──────────────────────────────────

    # Amplitude: 日内振幅 (high-low)/close，用 close 近似
    high_approx = close_panel.rolling(2).max()
    low_approx = close_panel.rolling(2).min()
    factors['amplitude'] = (high_approx - low_approx) / (close_panel + eps)

    # Illiquidity (Amihud): |return| / amount，非流动性越高值越大
    abs_returns = returns.abs()
    factors['illiquidity'] = abs_returns / (amount_panel + eps)

    # Turnover skew: 换手率偏度（20日）
    turnover = volume_panel / (volume_panel.rolling(20).mean() + eps)
    factors['turnover_skew'] = turnover.rolling(20).skew()

    # Turnover change: 5日换手率 vs 20日换手率
    turnover_5 = volume_panel / (volume_panel.rolling(5).mean() + eps)
    turnover_20 = volume_panel / (volume_panel.rolling(20).mean() + eps)
    factors['turnover_change'] = turnover_5 / (turnover_20 + eps)

    # Price impact: 收益率 / 成交额变化，衡量价格冲击
    amount_change = amount_panel.pct_change(5)
    factors['price_impact'] = returns.rolling(5).sum() / (amount_change.abs() + eps)

    # PV correlation: 价格与成交量的 10日滚动相关系数
    factors['pv_corr'] = close_panel.rolling(10).corr(volume_panel)

    # Chip kurtosis: 成本的峰度（用收益率峰度近似筹码分布）
    factors['chip_kurt'] = returns.rolling(20).kurt()

    # OBV slope: OBV 的线性回归斜率
    obv = (returns.apply(lambda x: (x > 0).astype(int) - (x <= 0).astype(int)) * volume_panel).cumsum()
    obv_slope = obv.rolling(10).apply(
        lambda s: np.polyfit(np.arange(len(s)), s, 1)[0] if len(s) > 1 else 0, raw=True
    )
    factors['obv_slope'] = obv_slope

    # ── 短线因子：gap / intraday / range ──────────────────────────────
    # 需要 open/high/low 面板；缺少时用 close 近似（不报 warning，静默降级）

    if open_panel is not None:
        # gap_ratio: 跳空比 = (open - prev_close) / prev_close
        factors['gap_ratio'] = (open_panel - close_panel.shift(1)) / (close_panel.shift(1) + eps)
    else:
        factors['gap_ratio'] = pd.DataFrame(0.0, index=close_panel.index, columns=close_panel.columns)

    if high_panel is not None and low_panel is not None:
        # high_low_range: 日内振幅 = (high - low) / close
        factors['high_low_range'] = (high_panel - low_panel) / (close_panel + eps)

        if open_panel is not None:
            # intraday_drift: 日内漂移 = (close - open) / (high - low)
            # 衡量当天方向性，值域约 [-1, 1]，正值 = 收盘在开盘上方
            factors['intraday_drift'] = (close_panel - open_panel) / (high_panel - low_panel + eps)
        else:
            factors['intraday_drift'] = pd.DataFrame(0.0, index=close_panel.index, columns=close_panel.columns)
    else:
        factors['high_low_range'] = pd.DataFrame(0.0, index=close_panel.index, columns=close_panel.columns)
        factors['intraday_drift'] = pd.DataFrame(0.0, index=close_panel.index, columns=close_panel.columns)

    # ── 拥挤度因子（Crowding）──────────────────────────────────────────
    # 参考华泰金工行业拥挤度模型，个股层面：
    # 1. 换手率分位数：当日换手率在近 250 日的分位数（越高越拥挤）
    # 2. 短期涨幅分位数：近 5 日涨幅在近 250 日的分位数
    # 3. 量比分位数：当日量比在近 250 日的分位数
    # 4. 振幅分位数：当日振幅在近 250 日的分位数
    #
    # 使用 rolling quantile：计算每个窗口内当前值在窗口中的百分位排名
    # 简化实现：用 rank(pct=True) 的最后一个值作为当前分位数

    # 换手率（volume / 20日均量）
    turnover = volume_panel / (volume_panel.rolling(20).mean() + eps)

    # 滚动分位数：对每个股票独立计算 250 日窗口内当前值的百分位
    def _rolling_pctrank(s):
        """计算序列最后一个值在序列中的百分位"""
        if len(s) < 1:
            return 0.5
        return pd.Series(s).rank(pct=True).iloc[-1]

    factors['crowd_turnover_pct'] = turnover.rolling(250).apply(
        _rolling_pctrank, raw=True
    )

    # 短期涨幅分位数（5日涨幅在近250日的分位数）
    ret_5d = close_panel.pct_change(5)
    factors['crowd_ret5d_pct'] = ret_5d.rolling(250).apply(
        _rolling_pctrank, raw=True
    )

    # 量比分位数
    vol_ratio = volume_panel / (volume_panel.rolling(10).mean() + eps)
    factors['crowd_vr_pct'] = vol_ratio.rolling(250).apply(
        _rolling_pctrank, raw=True
    )

    # 振幅分位数（high-low range）
    if high_panel is not None and low_panel is not None:
        amplitude = (high_panel - low_panel) / (close_panel + eps)
        factors['crowd_amp_pct'] = amplitude.rolling(250).apply(
            _rolling_pctrank, raw=True
        )
    else:
        factors['crowd_amp_pct'] = pd.DataFrame(0.5, index=close_panel.index, columns=close_panel.columns)

    # 综合拥挤度得分（4个指标等权平均，越高越拥挤）
    factors['crowd_score'] = (
        factors['crowd_turnover_pct'] + factors['crowd_ret5d_pct'] +
        factors['crowd_vr_pct'] + factors['crowd_amp_pct']
    ) / 4.0

    # ── 残差动量（Residual Momentum）────────────────────────────────
    # 华泰金工方法：用 Barra 风格因子截面回归，取残差做动量
    # 风格因子：size(log市值), volatility, momentum, liquidity
    # 残差 = 剥离风格暴露后的纯 Alpha

    _n_days = 20  # 残差动量窗口
    _min_stocks = 50  # 最少股票数做回归

    # 构造风格因子截面（每个日期一个截面）
    # size: log(close * volume) 作为流通市值代理
    _size = np.log(close_panel * volume_panel + eps)
    # volatility: 已实现波动率 vol_20
    _vol = factors.get('vol_20', returns.rolling(20).std())
    # momentum: mom_20
    _mom = close_panel.pct_change(20)
    # liquidity: amount_ratio (已计算)
    _liq = factors.get('amount_ratio', amount_panel / (amount_panel.rolling(20).mean() + eps))

    # 截面回归：r_i = α + β1*size_i + β2*vol_i + β3*mom_i + β4*liq_i + ε_i
    # 残差 ε_i = r_i - (α + β1*size_i + β2*vol_i + β3*mom_i + β4*liq_i)
    # 简化：用过去 _n_days 的残差均值作为残差动量因子

    _resid_mom = pd.DataFrame(0.0, index=close_panel.index, columns=close_panel.columns)

    for date in close_panel.index:
        # 截面数据
        _r = returns.loc[date] if date in returns.index else None
        if _r is None:
            continue
        _s = _size.loc[date] if date in _size.index else None
        _v = _vol.loc[date] if date in _vol.index else None
        _m = _mom.loc[date] if date in _mom.index else None
        _l = _liq.loc[date] if date in _liq.index else None

        if _s is None or _v is None or _m is None or _l is None:
            continue

        # 合并截面数据
        _df = pd.DataFrame({'r': _r, 'size': _s, 'vol': _v, 'mom': _m, 'liq': _l})
        _df = _df.dropna()

        if len(_df) < _min_stocks:
            continue

        # 标准化风格因子
        for col in ['size', 'vol', 'mom', 'liq']:
            _df[col] = (_df[col] - _df[col].mean()) / (_df[col].std() + eps)

        # OLS 回归（截面）
        X = _df[['size', 'vol', 'mom', 'liq']].values
        y = _df['r'].values
        try:
            # β = (X'X)^(-1) X'y
            XtX = X.T @ X
            Xty = X.T @ y
            beta = np.linalg.solve(XtX + np.eye(4) * 1e-6, Xty)
            resid = y - X @ beta
        except np.linalg.LinAlgError:
            continue

        # 残差动量 = 过去 _n_days 残差累计（简化：用当前残差近似）
        _stock_resid = pd.Series(resid, index=_df.index)
        _resid_mom.loc[date] = _stock_resid.reindex(_resid_mom.columns).fillna(0)

    factors['resid_mom'] = _resid_mom

    # ── v17: 价量张力因子 ──────────────────────────────────────────
    # 价格偏离度 × 量能变化率
    pct_deviation = (close_panel - close_panel.rolling(20).mean()) / (close_panel.rolling(20).std() + eps)
    vr_5 = volume_panel / (volume_panel.rolling(5).mean() + eps)
    vr_20 = volume_panel / (volume_panel.rolling(20).mean() + eps)
    vol_accel = vr_5 / (vr_20 + eps)
    factors['price_volume_tension'] = pct_deviation * vol_accel
    factors['vol_accel'] = vol_accel

    # ── v18: 波动率的波动率 ──────────────────────────────────────────
    vol_20_for_vov = returns.rolling(20).std()
    factors['vol_of_vol'] = vol_20_for_vov.rolling(20).std()

    # ── 退市风险因子（Delist Risk）──────────────────────────────────
    # 综合信号：低价 + 价格趋势下行 + 成交量萎缩 + 波动率异常
    # 每个截面独立计算，值越高 = 退市风险越大
    # 1. 价格水平：20日均价 < 2元 → 高风险（用 z-score 标准化）
    price_level = close_panel.rolling(20).mean()
    # 2. 价格趋势：20日收益率（负值 = 下跌趋势）
    price_trend = close_panel.pct_change(20)
    # 3. 成交量萎缩：5日量 / 20日量（< 1 = 缩量）
    vol_shrink = volume_panel.rolling(5).mean() / (volume_panel.rolling(20).mean() + eps)
    # 4. 波动率异常：当前波动率 / 历史波动率（> 2 = 异常）
    vol_current = returns.rolling(5).std()
    vol_hist = returns.rolling(60).std()
    vol_abnormal = vol_current / (vol_hist + eps)

    # 综合退市风险得分（越高 = 风险越大）
    # 标准化到截面 z-score 后加权
    def _zscore(df):
        m = df.mean(axis=1)
        s = df.std(axis=1)
        return (df.sub(m, axis=0)).div(s + eps, axis=0)

    # 低价风险：价格越低风险越大（取负 z-score）
    price_risk = -_zscore(price_level)
    # 下跌风险：趋势越负风险越大（取负 z-score）
    trend_risk = -_zscore(price_trend)
    # 缩量风险：量比越小风险越大（取负 z-score）
    shrink_risk = -_zscore(vol_shrink)
    # 波动异常：波动率越高风险越大
    abnormal_risk = _zscore(vol_abnormal)

    factors['delist_risk'] = (price_risk + trend_risk + shrink_risk + abnormal_risk) / 4.0

    # ── 行业轮动因子（Industry Rotation）────────────────────────────────
    # 需要 industry_map: {股票代码: 行业名称}
    # 计算行业动量（20日）- 行业反转（5日），映射到个股
    if industry_map:
        try:
            from core.industry_rotation import calc_industry_rotation_scores
            factors['industry_rot'] = calc_industry_rotation_scores(
                close_panel, industry_map, mom_window=20, rev_window=5
            )
        except Exception:
            factors['industry_rot'] = pd.DataFrame(0.0, index=close_panel.index, columns=close_panel.columns)
    else:
        factors['industry_rot'] = pd.DataFrame(0.0, index=close_panel.index, columns=close_panel.columns)

    return factors


def calc_factors_panel_v11b(close_panel, volume_panel, high_panel, low_panel):
    """v11b 专用 panel 因子计算（仅 13 个因子，无截面回归，飞快）

    需要的因子：
      momentum: mom_20, mom_10, rsi_14, high_low_range
      volatility: vol_60, vol_20, vol_10, boll_width_20
      reversal: rev_10, rev_5, rsi_6, boll_pos_10
      extra: small_cap (log_market_cap)
    """
    returns = close_panel.pct_change()
    eps = 1e-10
    factors = {}

    # Momentum
    factors['mom_20'] = close_panel.pct_change(20)
    factors['mom_10'] = close_panel.pct_change(10)

    # Reversal
    factors['rev_10'] = -close_panel.pct_change(10)
    factors['rev_5'] = -close_panel.pct_change(5)

    # Volatility
    factors['vol_10'] = returns.rolling(10).std()
    factors['vol_20'] = returns.rolling(20).std()
    factors['vol_60'] = returns.rolling(60).std()

    # RSI
    for w, name in [(6, 'rsi_6'), (14, 'rsi_14')]:
        delta = close_panel.diff()
        gain = delta.where(delta > 0, 0).rolling(w).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(w).mean()
        rs = gain / loss.replace(0, np.nan)
        factors[name] = 100 - (100 / (1 + rs))

    # high_low_range
    factors['high_low_range'] = (high_panel - low_panel) / (close_panel + eps)

    # Bollinger
    ma10 = close_panel.rolling(10).mean()
    std10 = close_panel.rolling(10).std()
    factors['boll_pos_10'] = (close_panel - (ma10 - 2 * std10)) / (4 * std10 + eps)
    ma20 = close_panel.rolling(20).mean()
    std20 = close_panel.rolling(20).std()
    factors['boll_width_20'] = (4 * std20) / (ma20 + eps)

    # small_cap (用 close * volume 作为市值代理)
    factors['small_cap'] = -(np.log(close_panel * volume_panel + eps))

    return factors
