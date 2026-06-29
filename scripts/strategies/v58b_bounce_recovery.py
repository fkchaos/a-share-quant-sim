#!/usr/bin/env python3
"""
scripts/strategies/v58b_bounce_recovery.py — v58b 超跌+资金承接策略
=============================================================================
超跌 → 资金回流 → 2-5日反弹

和 v39g 互补:
- v39g: 追强（mom>3% + 小市值）
- v58b: 抄底（跌幅深 + 资金回流）

因子:
- drop_5d / drop_10d: 跌幅
- rsi_14: 超卖程度
- fund_flow: 资金流入/流出比率(10日)
- vol_reversal: 跌势末期放量
- recovery_signal: 连阴后收阳
"""
import numpy as np
import pandas as pd


DEFAULT_PARAMS = {
    # ── 风控参数 ──
    "STOP_LOSS": -0.05,
    "TAKE_PROFIT": 0.10,
    "HOLD_DAYS_MAX": 5,
    "HOLD_DAYS_MIN": 2,
    "HOLD_DAYS_EXTEND": 3,
    "HOLD_DAYS_EXTEND_PNL": 0.05,
    "MAX_DAILY_BUY": 4,
    "MAX_POSITION": 0.20,
    "MAX_HOLDINGS": 5,
    "COOLDOWN_DAYS": 0,

    # ── 选股门槛 ──
    "DROP_10D_MIN": -0.15,        # 10日跌幅至少 -15%
    "DROP_10D_MAX": -0.05,        # 但不超 -5% 以上(排除极端)
    "DROP_5D_MAX": -0.03,         # 5日跌幅
    "RSI_14_MAX": 35,             # 超卖
    "FUND_FLOW_MIN": 1.0,         # 资金净流入为正
    "VOL_SURGE_MIN": 1.3,         # 放量

    # ── 评分权重 ──
    "W_DROP": 1.0,        # 跌幅越深越好
    "W_RSI": 0.5,         # 越超卖越好
    "W_FUND_FLOW": 1.0,   # 资金流入越多越好
    "W_VOL": 0.5,         # 放量越大越好
}


def calc_bounce_factors(close_panel, volume_panel, amount_panel,
                        high_panel, low_panel, open_panel=None, **kwargs):
    """
    计算超跌反弹因子
    
    Returns:
        dict: {factor_name: DataFrame(index=date, columns=stock)}
    """
    eps = 1e-10
    
    # 1. 跌幅
    drop_5d = close_panel / (close_panel.shift(5) + eps) - 1
    drop_10d = close_panel / (close_panel.shift(10) + eps) - 1

    # 2. RSI(14)
    delta = close_panel.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / (avg_loss + eps)
    rsi_14 = 100 - (100 / (1 + rs))

    # 3. 资金流(上涨日量/下跌日量, 10日)
    returns = close_panel.pct_change()
    up_days = returns.where(returns > 0).fillna(0)
    down_days = returns.where(returns < 0).fillna(0)
    up_vol = up_days.abs() * volume_panel
    down_vol = down_days.abs() * volume_panel
    up_vol_10 = up_vol.rolling(10).sum()
    down_vol_10 = down_vol.rolling(10).sum()
    fund_flow = up_vol_10 / (down_vol_10 + eps)

    # 4. 量比(当日量/5日均量)
    vol_avg_5 = volume_panel.rolling(5).mean()
    vol_surge = volume_panel / (vol_avg_5 + eps)

    # 5. 止跌信号: 连阴3日后收阳
    is_down = (close_panel < open_panel) if open_panel is not None else (close_panel < close_panel.shift(1))
    streak_down = is_down.rolling(3).sum() if not isinstance(is_down, bool) else pd.DataFrame(0, index=close_panel.index, columns=close_panel.columns)
    if not isinstance(is_down, bool):
        recovery_signal = ((streak_down >= 2) & (~is_down)).astype(float)
    else:
        recovery_signal = pd.DataFrame(0.0, index=close_panel.index, columns=close_panel.columns)

    return {
        'drop_5d': drop_5d,
        'drop_10d': drop_10d,
        'rsi_14': rsi_14,
        'fund_flow': fund_flow,
        'vol_surge': vol_surge,
        'recovery_signal': recovery_signal,
    }


def _score_column(factors, date, factor_name, reverse=False):
    """截面排名打分 (0-1)"""
    if factor_name not in factors or date not in factors[factor_name].index:
        return pd.Series(dtype=float)
    s = factors[factor_name].loc[date].dropna()
    if len(s) == 0:
        return s
    ranked = s.rank(pct=True)
    if reverse:
        ranked = 1.0 - ranked
    return ranked


def select_stocks_v58b(factors, date, current_holdings=None, params=None,
                        sold_recently=None):
    """
    v58b 选股: 超跌+资金承接
    1. 硬筛选: 跌幅+超卖+资金流入+放量+止跌
    2. 评分排序
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    needed = ['drop_10d', 'rsi_14', 'fund_flow', 'vol_surge', 'recovery_signal']
    for f in needed:
        if f not in factors or date not in factors[f].index:
            return []

    # ── 硬筛选 ──
    d10 = factors['drop_10d'].loc[date]
    candidates = list(d10.dropna().index)

    # 10日跌幅范围
    candidates = [c for c in candidates
                  if p["DROP_10D_MIN"] <= d10[c] <= p["DROP_10D_MAX"]]
    if not candidates:
        return []

    # RSI 超卖
    rsi = factors['rsi_14'].loc[date]
    candidates = [c for c in candidates
                  if c in rsi.index and rsi[c] < p["RSI_14_MAX"]]
    if not candidates:
        return []

    # 资金流入为正
    ff = factors['fund_flow'].loc[date]
    candidates = [c for c in candidates
                  if c in ff.index and ff[c] > p["FUND_FLOW_MIN"]]
    if not candidates:
        return []

    # 放量
    vol = factors['vol_surge'].loc[date]
    candidates = [c for c in candidates
                  if c in vol.index and vol[c] > p["VOL_SURGE_MIN"]]
    if not candidates:
        return []

    # 止跌信号
    rec = factors['recovery_signal'].loc[date]
    candidates = [c for c in candidates
                  if c in rec.index and rec[c] == 1]
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

    # 跌幅: 越深越好(负值排名+反转)
    scores += _score_column(factors, date, 'drop_10d', reverse=True).reindex(candidates).fillna(0) * p["W_DROP"]

    # RSI: 越低越好(反转)
    scores += _score_column(factors, date, 'rsi_14', reverse=True).reindex(candidates).fillna(0) * p["W_RSI"]

    # 资金流: 越高越好
    scores += _score_column(factors, date, 'fund_flow', reverse=False).reindex(candidates).fillna(0) * p["W_FUND_FLOW"]

    # 放量: 越大越好
    scores += _score_column(factors, date, 'vol_surge', reverse=False).reindex(candidates).fillna(0) * p["W_VOL"]

    scores = scores.sort_values(ascending=False)
    selected = scores.index[:p["MAX_DAILY_BUY"]]
    return [(code, scores[code]) for code in selected]
