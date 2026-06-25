#!/usr/bin/env python3
"""
scripts/strategies/v43_small_cap_rotation.py — v43 小市值轮动策略
====================================================
基于A股小市值效应（2025年超额+21%），结合动量+质量因子筛选。

核心逻辑：
- 选股池：全A小市值（5-50亿），排除ST/新股/涨停/停牌
- 调仓频率：每周二（降低交易成本）
- 风控：个股-7%止损，大盘趋势止损，1/4月空仓
- 因子：动量(mom_5) + 质量(ROE) + 活跃度(turnover_rate) + 规模(market_cap)

参考：
- 聚宽"低开买入小市值策略"（214%年化）
- 9db"小市值轮动避险策略"
- 知乎"白马+国九条小市值"（年化35%）

版本历史：
- v43a: 初始版本，默认参数
"""
import pandas as pd
import numpy as np
from scripts.strategies.v39c_pv_resonance import _score_column

DEFAULT_PARAMS = {
    # ── 风控参数 ──
    "STOP_LOSS": -0.08,
    "TAKE_PROFIT": 0.10,          # 10%止盈（更容易触发）
    "HOLD_DAYS_MAX": 14,           # 最长持有14天（2周）
    "HOLD_DAYS_EXTEND": 7,         # 盈利可延长7天
    "HOLD_DAYS_EXTEND_PNL": 0.03,  # 盈利3%可延长
    "MAX_DAILY_BUY": 3,            # 每天最多买3只
    "MAX_POSITION": 0.125,         # 单只上限12.5%
    "MAX_HOLDINGS": 8,             # 最多持有8只
    "COOLDOWN_DAYS": 0,
    "MAX_SAME_PREFIX": 0,

    # ── 选股池参数 ──
    "MARKET_CAP_MIN": 2e9,          # 最小市值20亿
    "MARKET_CAP_MAX": 1e10,         # 最大市值100亿（中小盘）
    "EXCLUDE_ST": True,
    "EXCLUDE_NEW": True,           # 排除上市<60天
    "EXCLUDE_LIMIT_UP": True,      # 排除涨停
    "EXCLUDE_SUSPENDED": True,     # 排除停牌

    # ── 调仓参数 ──
    "REBALANCE_DAY": "Tuesday",    # 每周二调仓
    "MONTHS_EMPTY": [1, 4],        # 1月、4月空仓（财报期）

    # ── 评分权重 ──
    "W_MOM": 0.40,                # 动量
    "W_ROE": 0.30,                # 质量（ROE）
    "W_TURNOVER_RATE": 0.10,      # 活跃度（真实换手率）
    "W_SIZE": 0.20,               # 规模（越小越好）

    # ── 因子参数 ──
    "MOM_PERIOD": 5,               # 5日动量
    "ROE_MIN": 0.15,              # ROE最低15%
    "TURNOVER_CLIP_MAX": 0.10,    # 换手率截断10%
    "SIZE_CLIP_MAX": 2e10,        # 市值截断200亿（与选股范围上限一致）
}


def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None, params=None, extra_data=None):
    """
    v43 因子计算：小市值轮动所需的因子。

    参数：
        close_panel: DataFrame — 收盘价面板（date × code）
        volume_panel: DataFrame — 成交量面板
        amount_panel: DataFrame — 成交额面板
        high_panel: DataFrame — 最高价面板
        low_panel: DataFrame — 最低价面板
        open_panel: DataFrame — 开盘价面板（可选）
        params: dict — 策略参数（含 float_shares_map 等）

    返回：
        factors: dict — {factor_name: {date: Series(index=code)}}
    """
    p = params or {}
    factors = {}

    # ── 因子1: mom_5（5日动量）──
    if close_panel is not None and len(close_panel) > 0:
        ret_5d = close_panel.pct_change(periods=5)
        factors['mom_5'] = ret_5d

    # ── 因子2: roe（质量因子）──
    # 尝试从 extra_data 传入真实 ROE，否则用 _roe_ 占位（与 mom_5 反向）
    # 注意：v43 设计偏动量+规模，ROE 权重不宜过高
    if extra_data and 'roe' in extra_data:
        roe_df = extra_data['roe']
        factors['roe'] = roe_df
    else:
        # 占位：用 20日动量（与5日动量区分，捕捉中期质量趋势）
        if close_panel is not None and len(close_panel) > 0:
            factors['roe'] = close_panel.pct_change(periods=20)

    # ── 因子3: turnover_rate（真实换手率 = volume / float_shares）──
    float_shares_map = p.get('float_shares_map', {})
    if volume_panel is not None and len(volume_panel) > 0:
        if float_shares_map:
            # 用 Series 直接对齐 close_panel 的列（代码）
            float_series = pd.Series(float_shares_map).reindex(volume_panel.columns).fillna(0)
            # 腾讯 volume 单位是股，float_shares 单位也是股
            turnover = volume_panel / float_series.replace(0, np.nan)
            factors['turnover_rate'] = turnover
        else:
            # 无 float_shares：用近似换手率 = volume * close / amount
            if amount_panel is not None and close_panel is not None:
                approx_turnover = volume_panel * close_panel / amount_panel.replace(0, np.nan)
                factors['turnover_rate'] = approx_turnover
            else:
                factors['turnover_rate'] = volume_panel

    # ── 因子4: market_cap（市值 = close * float_shares）──
    if close_panel is not None and len(close_panel) > 0:
        if float_shares_map:
            float_series = pd.Series(float_shares_map).reindex(close_panel.columns).fillna(0)
            factors['market_cap'] = close_panel.mul(float_series, axis=1)
        else:
            # 无 float_shares：用 close * sqrt(volume) 作为市值近似排序
            if volume_panel is not None:
                factors['market_cap'] = close_panel * np.sqrt(volume_panel)
            else:
                factors['market_cap'] = close_panel

    return factors


def select_stocks_v43(factors, date, current_holdings=None, params=None,
                      sold_recently=None, extra_data=None):
    """
    v43 选股：小市值轮动

    输入：
        factors: dict — 因子数据 {factor_name: {date: Series}}
        date: 当前日期
        current_holdings: list — 当前持仓代码
        params: dict — 策略参数
        sold_recently: set — 近期卖出的股票
        extra_data: dict — 额外数据 {'float_shares': {code: shares}, 'roe': {code: roe}}

    输出：
        list of (code, score) — 选中的股票和评分
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    # DEBUG
    import sys
    print(f"DEBUG called: date={date}, type={type(date)}", file=sys.stderr)
    if 'mom_5' in factors:
        m5_idx = factors['mom_5'].index
        print(f"DEBUG mom_5 index type={type(m5_idx)}, len={len(m5_idx)}, [0]={m5_idx[0] if len(m5_idx)>0 else 'empty'}", file=sys.stderr)
        print(f"DEBUG date in mom_5: {date in m5_idx}", file=sys.stderr)
    else:
        print(f"DEBUG mom_5 NOT in factors! keys={list(factors.keys())}", file=sys.stderr)

    # ── 空仓检查：1月、4月返回空 ──
    if hasattr(date, 'month') and date.month in p["MONTHS_EMPTY"]:
        print(f"DEBUG: empty month return", file=sys.stderr)
        return []

    # ── 获取候选池 ──
    if 'mom_5' not in factors or date not in factors['mom_5'].index:
        return []

    mom = factors['mom_5'].loc[date].dropna()

    # ── 硬筛选（反转逻辑：选动量回调过多的股票，超卖反弹）──
    # 1. 动量 < -5%（近期超跌，等待反弹）
    mom_filtered = mom[mom < -0.05]
    candidates = list(mom_filtered.index)

    # 2. 排除流动性极差（换手率<0.2%）
    if 'turnover_rate' in factors and date in factors['turnover_rate'].index:
        tr = factors['turnover_rate'].loc[date].dropna()
        candidates = [c for c in candidates if c in tr.index and tr[c] >= 0.002]

    # ── 市值过滤 ──
    if 'market_cap' in factors and date in factors['market_cap'].index:
        cap = factors['market_cap'].loc[date].dropna()
        candidates = [c for c in candidates if c in cap.index]
        candidates = [c for c in candidates
                      if p["MARKET_CAP_MIN"] <= cap[c] <= p["MARKET_CAP_MAX"]]

    # ── ROE过滤（如果extra_data提供）──
    if extra_data and 'roe' in extra_data:
        roe_map = extra_data['roe']
        candidates = [c for c in candidates if c in roe_map and roe_map[c] >= p["ROE_MIN"]]

    # ── 排除ST/涨停/停牌（通过extra_data标记）──
    if extra_data and 'invalid' in extra_data:
        invalid = extra_data['invalid']
        candidates = [c for c in candidates if c not in invalid]

    # ── 排除当前持仓 ──
    if current_holdings:
        candidates = [c for c in candidates if c not in current_holdings]
    if sold_recently:
        candidates = [c for c in candidates if c not in sold_recently]

    if not candidates:
        # DEBUG
        import sys
        print(f"DEBUG: early return - no candidates at date={date}", file=sys.stderr)
        return []

    # ── 评分 ──
    scores = pd.Series(0.0, index=candidates)

    # 动量因子（反转：超跌反弹，动量越负分越高）
    if p.get("W_MOM", 0) > 0 and 'mom_5' in factors:
        mom_scores = _score_column(factors, date, 'mom_5', clip_min=0)
        # 反向：动量越小（越负）分越高 → 用 (1 - clip_normalize)
        scores += (1 - mom_scores.reindex(candidates).fillna(0)) * p["W_MOM"]

    # ROE因子
    if p.get("W_ROE", 0) > 0 and 'roe' in factors:
        roe_scores = _score_column(factors, date, 'roe', clip_min=0)
        scores += roe_scores.reindex(candidates).fillna(0) * p["W_ROE"]

    # 换手率因子（真实换手率）
    if p.get("W_TURNOVER_RATE", 0) > 0 and 'turnover_rate' in factors:
        tr_scores = _score_column(factors, date, 'turnover_rate',
                                   clip_min=0, clip_max=p["TURNOVER_CLIP_MAX"])
        scores += tr_scores.reindex(candidates).fillna(0) * p["W_TURNOVER_RATE"]

    # 规模因子（越小越好，所以用负权重或反向评分）
    if p.get("W_SIZE", 0) > 0 and 'market_cap' in factors:
        cap_scores = _score_column(factors, date, 'market_cap',
                                    clip_min=0, clip_max=p["SIZE_CLIP_MAX"])
        # 反向：市值越小分越高 → 用 (1 - clip_normalize)
        scores += (1 - cap_scores.reindex(candidates).fillna(0)) * p["W_SIZE"]

    # ── 排序取前N ──
    scores = scores.sort_values(ascending=False)
    n_buy = min(p["MAX_DAILY_BUY"], len(scores))
    selected = scores.index[:n_buy]

    # DEBUG
    import sys
    print(f"DEBUG select_stocks_v43: date={date}, candidates={len(candidates)}, scores={len(scores)}, selected={len(selected)}", file=sys.stderr)
    if len(selected) > 0:
        print(f"  Top 3: {scores.index[:3].tolist()}, scores: {scores.head(3).to_dict()}", file=sys.stderr)

    return [(code, scores[code]) for code in selected]
