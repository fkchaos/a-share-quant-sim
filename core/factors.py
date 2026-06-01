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

    return factors


# ── Panel mode (same math, vectorized over all stocks) ───────────────

def calc_factors_panel(
    close_panel: pd.DataFrame,
    volume_panel: pd.DataFrame = None,
    amount_panel: pd.DataFrame = None,
    open_panel: pd.DataFrame = None,
    high_panel: pd.DataFrame = None,
    low_panel: pd.DataFrame = None,
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
            factors['intraday_drift'] = pd.Series(0.0, index=close_panel.index)
    else:
        factors['high_low_range'] = pd.Series(0.0, index=close_panel.index)
        factors['intraday_drift'] = pd.Series(0.0, index=close_panel.index)

    return factors
