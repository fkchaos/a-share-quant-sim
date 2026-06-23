"""
scripts/strategies/v41_vwap_deviation.py — v41 策略：VWAP 偏离 + 净支撑量因子

在 v39c 多因子评分体系基础上，新增两个量价因子：
1. VWAP 偏离因子：close 相对 VWAP 的偏离度（动量方向）
2. 净支撑量因子：趋势资金日的支撑/阻力成交量差

权重设计：
- 保留 v39c 原有 7 因子（权重和=1.0）
- 新增 VWAP_DEV 因子（权重=0.15）
- 新增 NET_SUPPORT 因子（权重=0.10）
- 总权重和 = 1.25

参考：
- WorldQuant Alpha #5, #42（VWAP 偏离）
- 国盛证券"量价淘金"系列（净支撑量）
"""

import pandas as pd
import numpy as np
from typing import Optional

# 导入新因子计算函数
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.factor_trend_support import (
    calc_vwap_deviation,
    calc_net_support_volume,
    remove_extremes,
    zscore_normalize,
)


def calc_factors_v41(close_panel, volume_panel, amount_panel,
                     high_panel=None, low_panel=None, open_panel=None, params=None):
    """
    计算 v41 因子面板。

    在 v39c 因子基础上，新增 VWAP 偏离和净支撑量因子。
    """
    # 先计算 v39c 的因子面板（用 v39c 自己的实现，避免 calc_factors_panel 的拥挤度等慢因子）
    from scripts.strategies.v39c_pv_resonance import calc_factors as calc_factors_v39c
    factors = calc_factors_v39c(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel, params)

    # 新增因子 1：VWAP 偏离
    if high_panel is not None and low_panel is not None:
        vwap_dev_raw = calc_vwap_deviation(close_panel, volume_panel, high_panel, low_panel, lookback=10)
        vwap_dev = zscore_normalize(remove_extremes(vwap_dev_raw))
        factors['vwap_deviation'] = vwap_dev

        # 新增因子 2：净支撑量
        # 简化版趋势资金识别：当日量 > 20日均量 * 1.5
        vol_ma20 = volume_panel.rolling(20, min_periods=5).mean()
        is_trend = volume_panel > vol_ma20 * 1.5
        net_support_raw = calc_net_support_volume(close_panel, volume_panel, high_panel, low_panel,
                                                   is_trend, lookback=20)
        net_support = zscore_normalize(remove_extremes(net_support_raw))
        factors['net_support'] = net_support

    return factors


def score_stocks_v41(factors, date, params, codes=None):
    """
    v41 多因子评分。

    在 v39c 评分基础上，加入 VWAP 偏离和净支撑量。
    """
    p = {
        # v39c 原有权重
        "W_MOM": 0.20,
        "W_PV_CORR": 0.05,
        "W_TURNOVER": 0.10,
        "W_SIZE": 0.10,
        "W_FUND_FLOW": 0.15,
        "W_GAP": 0.10,
        "W_ILLIQ": 0.10,
        # 新增因子权重
        "W_VWAP_DEV": 0.15,
        "W_NET_SUPPORT": 0.10,
    }
    if params:
        p.update(params)

    if date not in factors.get('mom_5', pd.DataFrame()).index:
        return pd.Series(dtype=float)

    all_scores = pd.Series(0.0, index=factors['mom_5'].columns)

    # ① 动量评分
    if 'mom_5' in factors and date in factors['mom_5'].index:
        mom_scores = _score_column(factors, date, 'mom_5')
        all_scores += mom_scores.reindex(all_scores.index).fillna(0) * p["W_MOM"]

    # ② 量价共振评分
    if 'pv_corr_20' in factors and date in factors['pv_corr_20'].index:
        pv_scores = _score_column(factors, date, 'pv_corr_20')
        all_scores += pv_scores.reindex(all_scores.index).fillna(0) * p["W_PV_CORR"]

    # ③ 换手率评分
    if 'turnover_avg' in factors and date in factors['turnover_avg'].index:
        to_scores = _score_column(factors, date, 'turnover_avg', clip_min=0, clip_max=0.05)
        all_scores += to_scores.reindex(all_scores.index).fillna(0) * p["W_TURNOVER"]

    # ④ 市值弹性评分
    if 'size_factor' in factors and date in factors['size_factor'].index:
        sf_scores = _score_column(factors, date, 'size_factor')
        all_scores += sf_scores.reindex(all_scores.index).fillna(0) * p["W_SIZE"]

    # ⑤ 资金流强度评分
    if 'fund_flow' in factors and date in factors['fund_flow'].index:
        ff_scores = _score_column(factors, date, 'fund_flow', clip_min=0.5, clip_max=3.0)
        all_scores += ff_scores.reindex(all_scores.index).fillna(0) * p["W_FUND_FLOW"]

    # ⑥ 跳空评分
    if 'gap_ratio' in factors and date in factors['gap_ratio'].index:
        gap_scores = _score_column(factors, date, 'gap_ratio', clip_min=0, clip_max=0.05)
        all_scores += gap_scores.reindex(all_scores.index).fillna(0) * p["W_GAP"]

    # ⑦ 非流动性评分
    if 'illiq' in factors and date in factors['illiq'].index:
        illiq_scores = _score_column(factors, date, 'illiq')
        all_scores += illiq_scores.reindex(all_scores.index).fillna(0) * p["W_ILLIQ"]

    # ⑧ VWAP 偏离评分（新增）
    if 'vwap_deviation' in factors and date in factors['vwap_deviation'].index:
        vwap_scores = _score_column(factors, date, 'vwap_deviation')
        all_scores += vwap_scores.reindex(all_scores.index).fillna(0) * p["W_VWAP_DEV"]

    # ⑨ 净支撑量评分（新增）
    if 'net_support' in factors and date in factors['net_support'].index:
        ns_scores = _score_column(factors, date, 'net_support')
        all_scores += ns_scores.reindex(all_scores.index).fillna(0) * p["W_NET_SUPPORT"]

    # 去掉全零行
    all_scores = all_scores[all_scores > 0]

    if codes is not None:
        valid_codes = [c for c in codes if c in all_scores.index]
        return all_scores.loc[valid_codes]
    return all_scores


def _score_column(factors, date, col, clip_min=None, clip_max=None):
    """横截面 zscore 归一化到 [0, 1]"""
    if date not in factors[col].index:
        return pd.Series(dtype=float)
    s = factors[col].loc[date].dropna()
    if clip_min is not None:
        s = s.clip(lower=clip_min)
    if clip_max is not None:
        s = s.clip(upper=clip_max)
    if s.max() == s.min():
        return pd.Series(0.5, index=s.index)
    return (s - s.min()) / (s.max() - s.min())


def select_stocks_v41(factors, date, current_holdings=None, params=None,
                       sold_recently=None):
    """
    v41 选股：v39c 选股逻辑 + 新增因子评分。
    """
    p = {
        "MOM_THRESHOLD": 0.03,
        "PV_CORR_10_MIN": -0.5,
        "PV_CORR_20_MIN": 0.0,
        "BOLL_W_MIN": 0.0,
        "MAX_DAILY_BUY": 4,
    }
    if params:
        p.update(params)

    if date not in factors.get('mom_5', pd.DataFrame()).index:
        return []

    # 门槛过滤
    mom_t = factors['mom_5'].loc[date]
    threshold = p.get("MOM_THRESHOLD", 0.03)
    candidates = [c for c in mom_t.index if mom_t[c] > threshold]

    # PV_CORR_10 门槛
    if 'pv_corr_10' in factors and date in factors['pv_corr_10'].index:
        pv_t = factors['pv_corr_10'].loc[date]
        pv_min = p.get("PV_CORR_10_MIN", -0.5)
        candidates = [c for c in candidates if c in pv_t.index and pv_t[c] > pv_min]

    # 排除已持有
    hold_set = set(current_holdings.keys()) if current_holdings else set()
    candidates = [c for c in candidates if c not in hold_set]
    if sold_recently:
        candidates = [c for c in candidates if c not in sold_recently]

    if not candidates:
        return []

    # 评分排序
    all_scores = score_stocks_v41(factors, date, p, codes=candidates)
    all_scores = all_scores.sort_values(ascending=False)

    max_daily_buy = p["MAX_DAILY_BUY"]
    selected = all_scores.index[:max_daily_buy]

    return [(code, all_scores[code]) for code in selected]
