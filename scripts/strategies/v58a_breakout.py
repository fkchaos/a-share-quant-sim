#!/usr/bin/env python3
"""
scripts/strategies/v58a_breakout.py — v58a 窄震出趋势策略
=========================================================
波动率压缩 → 放量突破 → 持有 3-10 日

信号源和 v39g 完全不同:
- v39g: 动量+小市值 (追强)
- v58a: 波动率状态转换 (抄突破)

因子:
- atr_compression: ATR(20)/ATR(60) 衡量波动率压缩
- amplitude_20: 20日振幅
- volume_surge: 量比
- breakout_signal: 突破20日高点
- ma_convergence: 均线粘合度

和 v39g 的回测条件严格一致:
- train=252, test=126, step=63, pool=zz1800, start=2021-01-01, end=2026-06-24
"""
import numpy as np
import pandas as pd


DEFAULT_PARAMS = {
    # ── 风控参数（参考 v39g）──
    "STOP_LOSS": -0.05,
    "TAKE_PROFIT": 0.15,
    "HOLD_DAYS_MAX": 10,
    "HOLD_DAYS_MIN": 3,
    "HOLD_DAYS_EXTEND": 5,
    "HOLD_DAYS_EXTEND_PNL": 0.10,
    "MAX_DAILY_BUY": 4,
    "MAX_POSITION": 0.20,
    "MAX_HOLDINGS": 5,
    "COOLDOWN_DAYS": 0,

    # ── 选股门槛 ──
    "ATR_COMPRESSION_MAX": 0.6,
    "AMPLITUDE_MAX": 0.15,
    "VOLUME_SURGE_MIN": 2.0,
    "MA_BAND_MAX": 0.02,

    # ── 评分权重 ──
    "W_ATR_COMP": 1.0,      # 压缩越狠越优先（负向因子取反）
    "W_AMP": 1.0,           # 振幅越小越优先
    "W_VOL_SURGE": 0.5,     # 放量越大越优先
    "W_MA_CONV": 0.5,       # 均线越粘合越优先
}


def _transpose_ma(close_panel, window):
    """计算n日均线"""
    return close_panel.rolling(window).mean()


def _calc_tr(high_panel, low_panel, close_panel):
    """计算真实波幅 TR = max(H-L, |H-C_prev|, |L-C_prev|)"""
    tr = pd.DataFrame(index=close_panel.index, columns=close_panel.columns, dtype=float)
    prev_close = close_panel.shift(1)
    tr = pd.concat([
        (high_panel - low_panel),
        (high_panel - prev_close).abs(),
        (low_panel - prev_close).abs()
    ]).groupby(level=0).max()
    return tr


def calc_breakout_factors(close_panel, volume_panel, amount_panel,
                          high_panel, low_panel, open_panel=None, **kwargs):
    """
    计算窄震突破因子
    
    Returns:
        dict: {factor_name: DataFrame(index=date, columns=stock)}
    """
    # 1. ATR 比率 =TR(20) / ATR(60)
    tr = _calc_tr(high_panel, low_panel, close_panel)
    atr_20 = tr.rolling(20).mean()
    atr_60 = tr.rolling(60).mean()
    atr_compression = atr_20 / (atr_60 + 1e-10)

    # 2. 20日振幅
    high_20 = high_panel.rolling(20).max()
    low_20 = low_panel.rolling(20).min()
    amplitude_20 = (high_20 - low_20) / (close_panel + 1e-10)

    # 3. 量比 = 当日成交量 / 20日均量
    vol_avg_20 = volume_panel.rolling(20).mean()
    volume_surge = volume_panel / (vol_avg_20 + 1e-10)

    # 4. 突破信号: 收盘 > 昨日20日高点
    high_20_prev = high_panel.rolling(20).max().shift(1)
    breakout_signal = (close_panel > high_20_prev).astype(float)

    # 5. 均线粘合度: std(MA5, MA10, MA20) / mean(MA5, MA10, MA20)
    ma5 = close_panel.rolling(5).mean()
    ma10 = close_panel.rolling(10).mean()
    ma20 = close_panel.rolling(20).mean()
    
    # 逐日计算3条均线的截面变异系数
    ma_mean = (ma5 + ma10 + ma20) / 3
    ma_sq_mean = (ma5**2 + ma10**2 + ma20**2) / 3
    ma_var = ma_sq_mean - ma_mean**2
    ma_var = ma_var.clip(lower=0)  # 防止浮点误差
    ma_std = np.sqrt(ma_var)
    ma_convergence = ma_std / (ma_mean.abs() + 1e-10)

    return {
        'atr_compression': atr_compression,
        'amplitude_20': amplitude_20,
        'volume_surge': volume_surge,
        'breakout_signal': breakout_signal,
        'ma_convergence': ma_convergence,
    }


def _score_column(factors, date, factor_name, reverse=False):
    """
    截面排名打分 (0-1)
    reverse=True: 值越小越好（取反排名）
    """
    if factor_name not in factors or date not in factors[factor_name].index:
        return pd.Series(dtype=float)
    s = factors[factor_name].loc[date].dropna()
    if len(s) == 0:
        return s
    ranked = s.rank(pct=True)
    if reverse:
        ranked = 1.0 - ranked
    return ranked


def select_stocks_v58a(factors, date, current_holdings=None, params=None,
                        sold_recently=None):
    """
    v58a 选股: 窄震+放量突破
    
    1. 硬筛选: ATR压缩 + 振幅小 + 放量 + 突破 + 均线粘合
    2. 评分排序: 按压缩度/振幅/放量/均线粘合综合打分
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    # 检查所有因子可用
    needed = ['atr_compression', 'amplitude_20', 'volume_surge',
              'breakout_signal', 'ma_convergence']
    for f in needed:
        if f not in factors or date not in factors[f].index:
            return []

    # ── 硬筛选 ──
    atr_c = factors['atr_compression'].loc[date]
    candidates = list(atr_c.dropna().index)

    # ATR压缩
    candidates = [c for c in candidates if atr_c[c] < p["ATR_COMPRESSION_MAX"]]
    if not candidates:
        return []

    # 振幅
    amp = factors['amplitude_20'].loc[date]
    candidates = [c for c in candidates if c in amp.index and amp[c] < p["AMPLITUDE_MAX"]]
    if not candidates:
        return []

    # 放量
    vol = factors['volume_surge'].loc[date]
    candidates = [c for c in candidates if c in vol.index and vol[c] > p["VOLUME_SURGE_MIN"]]
    if not candidates:
        return []

    # 突破
    brk = factors['breakout_signal'].loc[date]
    candidates = [c for c in candidates if c in brk.index and brk[c] == 1]
    if not candidates:
        return []

    # 均线粘合
    ma_c = factors['ma_convergence'].loc[date]
    candidates = [c for c in candidates if c in ma_c.index and ma_c[c] < p["MA_BAND_MAX"]]
    if not candidates:
        return []

    # 排除已持有/冷却期
    if current_holdings:
        candidates = [c for c in candidates if c not in current_holdings]
    if sold_recently:
        candidates = [c for c in candidates if c not in sold_recently]
    if not candidates:
        return []

    # ── 评分排序 ──
    scores = pd.Series(0.0, index=candidates, dtype=float)

    # ATR压缩: 越小越好 → reverse
    scores += _score_column(factors, date, 'atr_compression', reverse=True).reindex(candidates).fillna(0) * p["W_ATR_COMP"]

    # 振幅: 越小越好 → reverse
    scores += _score_column(factors, date, 'amplitude_20', reverse=True).reindex(candidates).fillna(0) * p["W_AMP"]

    # 放量: 越大越好
    scores += _score_column(factors, date, 'volume_surge', reverse=False).reindex(candidates).fillna(0) * p["W_VOL_SURGE"]

    # 均线粘合: 越小越好 → reverse
    scores += _score_column(factors, date, 'ma_convergence', reverse=True).reindex(candidates).fillna(0) * p["W_MA_CONV"]

    scores = scores.sort_values(ascending=False)
    selected = scores.index[:p["MAX_DAILY_BUY"]]
    return [(code, scores[code]) for code in selected]
