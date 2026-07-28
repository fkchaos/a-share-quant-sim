#!/usr/bin/env python3
"""
v70: 极端集中动量策略
====================
核心改变（相对v69）：
- 只选Top1最热行业（极度集中）
- 最多持2-3只股票（集中押注最强者）
- 每天只买1-2只
- 止损-3%（快速砍），止盈+20%（让利润跑）
- 指数>MA20硬性门槛（趋势确认）
- 5日动量>5%（只买强势股）
"""
import pandas as pd
import numpy as np
import sqlite3
import os

_INDUSTRY_MAP_CACHE = None

DEFAULT_PARAMS = {
    # 风控
    "STOP_LOSS": -0.03,
    "TAKE_PROFIT": 0.20,
    "HOLD_DAYS_MAX": 3,
    "MAX_HOLDINGS": 3,
    "MAX_DAILY_BUY": 1,
    "MAX_POSITION": 0.40,
    "HOLD_DAYS_MIN": 1,
    "HOLD_DAYS_EXTEND": 5,
    "HOLD_DAYS_EXTEND_PNL": 0.08,

    # 行业动量
    "TOP_INDUSTRIES": 1,
    "W_MOM_5D": 0.60,
    "W_MOM_10D": 0.25,
    "W_MOM_20D": 0.15,

    # 个股强势度
    "W_STOCK_MOM": 0.60,
    "W_STOCK_VOL": 0.25,
    "W_STOCK_AMT": 0.15,
    "STOCK_MOM_MIN": 0.05,

    # 指数趋势确认
    "INDEX_MA_ENABLED": True,
    "INDEX_MA_PERIOD": 20,

    # 情绪择时
    "SENTIMENT_ENABLED": True,
    "SENTIMENT_THRESHOLD": 15,

    # 过滤
    "EXCLUDE_LIMIT_UP": True,
    "MIN_AMOUNT": 3e7,
}


def _load_industry_map():
    global _INDUSTRY_MAP_CACHE
    if _INDUSTRY_MAP_CACHE is not None:
        return _INDUSTRY_MAP_CACHE
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    db_path = os.path.join(project_root, 'data', 'quant_stocks.db')
    conn = sqlite3.connect(db_path)
    rows = conn.execute('SELECT code, industry FROM industry_map').fetchall()
    conn.close()
    _INDUSTRY_MAP_CACHE = {code: ind for code, ind in rows}
    return _INDUSTRY_MAP_CACHE


def calc_factors_v71(close_panel, volume_panel, amount_panel,
                     high_panel=None, low_panel=None, open_panel=None,
                     extra_data=None):
    eps = 1e-10
    returns = close_panel.pct_change()

    # ── 个股因子 ──
    mom_5 = close_panel.pct_change(5)
    vol_5 = volume_panel.rolling(5).mean()
    vol_20 = volume_panel.rolling(20).mean()
    vol_ratio = vol_5 / (vol_20 + eps)
    amt_rank = amount_panel.rolling(20).mean().rank(axis=1, pct=True)

    # ── 行业动量 ──
    industry_map = _load_industry_map()
    industry_groups = {}
    for code in close_panel.columns:
        if code in industry_map:
            ind = industry_map[code]
            if ind not in industry_groups:
                industry_groups[ind] = []
            industry_groups[ind].append(code)

    industry_momentum = pd.DataFrame(0.0, index=close_panel.index,
                                     columns=list(industry_groups.keys()))
    for ind_name, stocks in industry_groups.items():
        if len(stocks) < 3:
            continue
        ind_ret_5 = returns[stocks].mean(axis=1).rolling(5).mean()
        ind_ret_10 = returns[stocks].mean(axis=1).rolling(10).mean()
        ind_ret_20 = returns[stocks].mean(axis=1).rolling(20).mean()
        industry_momentum[ind_name] = (
            0.60 * ind_ret_5 + 0.25 * ind_ret_10 + 0.15 * ind_ret_20
        )

    # ── 每只股票标注所属行业动量 ──
    stock_industry_mom = pd.DataFrame(0.0, index=close_panel.index,
                                      columns=close_panel.columns)
    for ind_name, stocks in industry_groups.items():
        if ind_name in industry_momentum.columns:
            for code in stocks:
                if code in stock_industry_mom.columns:
                    stock_industry_mom[code] = industry_momentum[ind_name]

    # ── 情绪指标 ──
    limit_up = ((returns >= 0.095) & (returns <= 0.105)).astype(float)
    limit_up_count = limit_up.sum(axis=1)

    # ── 指数MA ──
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    db_path = os.path.join(project_root, 'data', 'quant_stocks.db')
    iconn = sqlite3.connect(db_path)
    idx_df = pd.read_sql(
        "SELECT date, close FROM daily_kline WHERE code='sh000001' ORDER BY date",
        iconn
    )
    iconn.close()
    idx_df['date'] = pd.to_datetime(idx_df['date'])
    idx_df = idx_df.set_index('date')
    index_close = idx_df['close'].reindex(close_panel.index)
    index_ma20 = index_close.rolling(20).mean()
    index_above_ma = (index_close > index_ma20).astype(float)

    return {
        'industry_momentum': industry_momentum,
        'stock_industry_mom': stock_industry_mom,
        'mom_5': mom_5,
        'vol_ratio': vol_ratio,
        'amt_rank': amt_rank,
        'limit_up_count': limit_up_count,
        'index_above_ma': index_above_ma,
        'returns': returns,
    }


def select_stocks_v71(factors, date, current_holdings=None, params=None,
                       sold_recently=None, close_panel=None, high_panel=None):
    p = {**DEFAULT_PARAMS, **(params or {})}

    # ── 指数趋势确认（硬性门槛）──
    if p.get('INDEX_MA_ENABLED') and 'index_above_ma' in factors:
        if date in factors['index_above_ma'].index:
            if factors['index_above_ma'].loc[date] == 0:
                return []  # 指数在MA20下方，不开仓

    # ── 情绪过滤 ──
    if p.get('SENTIMENT_ENABLED') and 'limit_up_count' in factors:
        if date in factors['limit_up_count'].index:
            lup = factors['limit_up_count'].loc[date]
            if pd.notna(lup) and lup < p['SENTIMENT_THRESHOLD']:
                return []

    if date not in factors['industry_momentum'].index:
        return []
    if date not in factors['mom_5'].index:
        return []

    ind_mom_raw = factors['industry_momentum'].loc[date].dropna()
    m5 = factors['mom_5'].loc[date].dropna()

    # ── 选最热行业（Top1）──
    ind_sorted = ind_mom_raw.sort_values(ascending=False)
    hot_industry_names = set(ind_sorted.index[:p['TOP_INDUSTRIES']])

    # ── 从最热行业中选强势个股 ──
    industry_map = _load_industry_map()
    candidates = []
    for code in m5.index:
        ind = industry_map.get(code, '')
        if ind not in hot_industry_names:
            continue
        if m5[code] < p['STOCK_MOM_MIN']:
            continue
        candidates.append(code)

    if not candidates:
        return []

    # ── 排除涨停 ──
    if p.get('EXCLUDE_LIMIT_UP') and close_panel is not None and high_panel is not None:
        if date in close_panel.index and date in high_panel.index:
            close_today = close_panel.loc[date]
            high_today = high_panel.loc[date]
            candidates = [c for c in candidates
                         if c in close_today.index and c in high_today.index
                         and not (close_today[c] == high_today[c])]

    if current_holdings:
        candidates = [c for c in candidates if c not in current_holdings]
    if sold_recently:
        candidates = [c for c in candidates if c not in sold_recently]

    if not candidates:
        return []

    # ── 成交额过滤 ──
    if date in factors['amt_rank'].index:
        amt = factors['amt_rank'].loc[date]
        candidates = [c for c in candidates if c in amt.index and amt[c] > 0.05]

    if not candidates:
        return []

    # ── 评分排序 ──
    scores = pd.Series(0.0, index=candidates)

    # 行业动量分
    if date in factors.get('stock_industry_mom', pd.DataFrame()).index:
        im = factors['stock_industry_mom'].loc[date]
        scores += im.reindex(candidates).fillna(0) * 100 * 0.30

    # 个股动量分（高权重）
    scores += m5.reindex(candidates).fillna(0) * 100 * p['W_STOCK_MOM']

    # 量比分
    if date in factors['vol_ratio'].index:
        vr = factors['vol_ratio'].loc[date]
        vr_clipped = vr.reindex(candidates).fillna(1.0).clip(0, 5)
        scores += (vr_clipped / 5.0) * 100 * p['W_STOCK_VOL']

    # 成交额排名分
    if date in factors['amt_rank'].index:
        ar = factors['amt_rank'].loc[date]
        scores += ar.reindex(candidates).fillna(0) * 100 * p['W_STOCK_AMT']

    # ── 只取Top1（极度集中）──
    scores = scores.sort_values(ascending=False)
    selected = scores.index[:p['MAX_DAILY_BUY']]
    return [(code, scores[code]) for code in selected]


if __name__ == '__main__':
    print("v70: 极端集中动量策略")
    print(f"MAX_DAILY_BUY: {DEFAULT_PARAMS['MAX_DAILY_BUY']}")
    print(f"MAX_HOLDINGS: {DEFAULT_PARAMS['MAX_HOLDINGS']}")
    print(f"STOCK_MOM_MIN: {DEFAULT_PARAMS['STOCK_MOM_MIN']}")
