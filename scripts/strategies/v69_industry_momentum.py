#!/usr/bin/env python3
"""
v69: 行业动量追涨策略
=====================
核心逻辑：
1. 计算申万一级行业的多周期动量（5/10/20日）
2. 选最热门行业（动量排名前20%）
3. 从热门行业中选强势个股（动量+量能）
4. 市场情绪择时（涨停数阈值）
5. 快速止盈止损（短线持有）

与v35的区别：
- v35用成交额分组代理行业，v69用真实申万行业分类
- v69更聚焦"追涨"，v35更偏"均衡配置"
"""
import pandas as pd
import numpy as np
import sqlite3
import os

_INDUSTRY_MAP_CACHE = None

DEFAULT_PARAMS = {
    # 风控
    "STOP_LOSS": -0.06,
    "TAKE_PROFIT": 0.12,
    "HOLD_DAYS_MAX": 5,
    "MAX_HOLDINGS": 5,
    "MAX_DAILY_BUY": 3,
    "MAX_POSITION": 0.25,
    "HOLD_DAYS_MIN": 1,
    "HOLD_DAYS_EXTEND": 7,
    "HOLD_DAYS_EXTEND_PNL": 0.03,

    # 行业动量参数
    "W_MOM_5D": 0.50,
    "W_MOM_10D": 0.30,
    "W_MOM_20D": 0.20,
    "TOP_INDUSTRY_PCT": 0.20,      # 选前20%行业
    "TOP_INDUSTRIES": 3,           # 最多选3个行业

    # 个股强势度权重
    "W_STOCK_MOM": 0.50,
    "W_STOCK_VOL": 0.30,
    "W_STOCK_AMT": 0.20,
    "STOCK_MOM_MIN": 0.02,         # 个股5日动量最低2%

    # 情绪择时
    "SENTIMENT_ENABLED": True,
    "SENTIMENT_THRESHOLD": 20,     # 全市场涨停数阈值

    # 指数均线择时
    "REGIME_ENABLED": True,
    "REGIME_MA_PERIOD": 20,        # 上证指数20日均线
    "REGIME_SLOPE_DAYS": 5,        # 均线斜率窗口
    "REGIME_BULL_ALLOC": 1.0,      # 牛市仓位
    "REGIME_SIDEWAYS_ALLOC": 0.5,  # 震荡仓位
    "REGIME_BEAR_ALLOC": 0.0,      # 熊市仓位

    # 过滤
    "EXCLUDE_LIMIT_UP": True,
    "MIN_AMOUNT": 5e7,             # 最小成交额5000万
}


def _load_industry_map():
    """加载行业映射表"""
    global _INDUSTRY_MAP_CACHE
    if _INDUSTRY_MAP_CACHE is not None:
        return _INDUSTRY_MAP_CACHE
    # 定位DB
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    db_path = os.path.join(project_root, 'data', 'quant_stocks.db')
    conn = sqlite3.connect(db_path)
    rows = conn.execute('SELECT code, industry FROM industry_map').fetchall()
    conn.close()
    _INDUSTRY_MAP_CACHE = {code: ind for code, ind in rows}
    return _INDUSTRY_MAP_CACHE


def _build_industry_groups(industry_map, columns):
    """根据行业映射和面板列名，构建行业分组"""
    groups = {}
    for code in columns:
        if code in industry_map:
            ind = industry_map[code]
            if ind not in groups:
                groups[ind] = []
            groups[ind].append(code)
    return groups


def calc_factors_v69(close_panel, volume_panel, amount_panel,
                     high_panel=None, low_panel=None, open_panel=None,
                     extra_data=None):
    """
    计算v69因子：
    - industry_momentum: 行业动量得分
    - stock_momentum: 个股5日动量
    - vol_ratio: 量比
    - limit_up_count: 全市场涨停数（情绪指标）
    """
    eps = 1e-10
    returns = close_panel.pct_change()

    # ── 个股因子 ──
    mom_5 = close_panel.pct_change(5)
    mom_10 = close_panel.pct_change(10)
    mom_20 = close_panel.pct_change(20)

    # 量比 = 5日均量 / 20日均量
    vol_5 = volume_panel.rolling(5).mean()
    vol_20 = volume_panel.rolling(20).mean()
    vol_ratio = vol_5 / (vol_20 + eps)

    # 成交额排名
    amt_rank = amount_panel.rolling(20).mean().rank(axis=1, pct=True)

    # ── 行业动量 ──
    industry_map = _load_industry_map()
    industry_groups = _build_industry_groups(industry_map, close_panel.columns)

    # 计算每个行业的平均收益率
    industry_momentum = pd.DataFrame(0.0, index=close_panel.index,
                                     columns=list(industry_groups.keys()))

    for ind_name, stocks in industry_groups.items():
        if len(stocks) < 3:
            continue
        # 行业内各周期平均收益率
        ind_ret_5 = returns[stocks].mean(axis=1).rolling(5).mean()
        ind_ret_10 = returns[stocks].mean(axis=1).rolling(10).mean()
        ind_ret_20 = returns[stocks].mean(axis=1).rolling(20).mean()

        # 行业动量 = 多周期加权
        industry_momentum[ind_name] = (
            0.50 * ind_ret_5 + 0.30 * ind_ret_10 + 0.20 * ind_ret_20
        )

    # ── 为每只股票标注所属行业的动量得分 ──
    stock_industry_mom = pd.DataFrame(0.0, index=close_panel.index,
                                      columns=close_panel.columns)
    for ind_name, stocks in industry_groups.items():
        if ind_name in industry_momentum.columns:
            for code in stocks:
                if code in stock_industry_mom.columns:
                    stock_industry_mom[code] = industry_momentum[ind_name]

    # ── 全市场涨停数（情绪指标）──
    limit_up = ((returns >= 0.095) & (returns <= 0.105)).astype(float)
    limit_up_count = limit_up.sum(axis=1)

    # ── 指数行情（上证指数）──
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

    # 指数20日均线
    index_ma20 = index_close.rolling(20).mean()
    # 均线斜率（5日变化率）
    index_ma_slope = index_ma20.pct_change(5)

    # 市场状态：bull(>MA且斜率正) / bear(<MA或斜率负) / sideways(其他)
    regime = pd.Series('sideways', index=close_panel.index)
    bull_mask = (index_close > index_ma20) & (index_ma_slope > 0)
    bear_mask = (index_close < index_ma20) | (index_ma_slope < -0.02)
    regime[bull_mask] = 'bull'
    regime[bear_mask] = 'bear'

    # ── 择时因子2：涨跌家数比（Market Breadth）──
    advance = (returns > 0).sum(axis=1)
    decline = (returns < 0).sum(axis=1)
    ad_ratio = advance / (advance + decline + 1e-10)
    ad_ma5 = ad_ratio.rolling(5).mean()

    # ── 择时因子3：波动率状态（ATR proxy）──
    # 用全市场日收益率标准差的20日均值
    market_vol = returns.std(axis=1)
    vol_ma20 = market_vol.rolling(20).mean()
    vol_ma60 = market_vol.rolling(60).mean()
    # 低波动→有利追涨；高波动→不利
    vol_regime = (vol_ma20 < vol_ma60).astype(float)  # 1=低波, 0=高波

    # ── 择时因子4：成交额趋势 ──
    amt_total = amount_panel.sum(axis=1)
    amt_ma5 = amt_total.rolling(5).mean()
    amt_ma20 = amt_total.rolling(20).mean()
    # 放量=好，缩量=差
    volume_trend = (amt_ma5 > amt_ma20).astype(float)

    # ── 择时因子5：行业离散度（板块分化程度）──
    # 行业收益率截面标准差：高离散=分化严重（轮动快），低离散=普涨/普跌
    ind_ret_cross = industry_momentum.apply(lambda row: row[row != 0].std(), axis=1)
    ind_disperse_ma = ind_ret_cross.rolling(10).mean()

    return {
        'industry_momentum': industry_momentum,
        'stock_industry_mom': stock_industry_mom,
        'mom_5': mom_5,
        'vol_ratio': vol_ratio,
        'amt_rank': amt_rank,
        'limit_up_count': limit_up_count,
        'returns': returns,
        'index_close': index_close,
        'index_ma20': index_ma20,
        'regime': regime,
        'ad_ratio': ad_ma5,
        'vol_regime': vol_regime,
        'volume_trend': volume_trend,
        'ind_disperse': ind_disperse_ma,
    }


def select_stocks_v69(factors, date, current_holdings=None, params=None,
                       sold_recently=None, close_panel=None, high_panel=None):
    """
    v69选股：
    1. 情绪过滤（涨停数阈值）
    2. 选最热行业（行业动量Top N）
    3. 从热行业中选强势个股
    4. 排除涨停/已持有/近期卖出
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    # ── 情绪过滤 ──
    if p.get('SENTIMENT_ENABLED') and 'limit_up_count' in factors:
        if date in factors['limit_up_count'].index:
            lup = factors['limit_up_count'].loc[date]
            if pd.notna(lup) and lup < p['SENTIMENT_THRESHOLD']:
                return []  # 市场情绪太差，不开新仓

    # ── 指数均线择时 ──
    if p.get('REGIME_ENABLED') and 'regime' in factors:
        if date in factors['regime'].index:
            r = factors['regime'].loc[date]
            if r == 'bear':
                return []  # 熊市不开仓
            elif r == 'sideways':
                # 震荡市减半仓位（通过减少选股数实现）
                pass  # 后面通过 MAX_DAILY_BUY 控制

    # ── 涨跌家数比择时 ──
    if p.get('USE_AD_RATIO') and 'ad_ratio' in factors:
        if date in factors['ad_ratio'].index:
            ad = factors['ad_ratio'].loc[date]
            if pd.notna(ad) and ad < 0.40:
                return []  # 涨跌家数比太低，市场弱

    # ── 波动率择时 ──
    if p.get('USE_VOL_REGIME') and 'vol_regime' in factors:
        if date in factors['vol_regime'].index:
            vr = factors['vol_regime'].loc[date]
            if pd.notna(vr) and vr == 0:
                return []  # 高波动期不开仓

    # ── 成交额趋势择时 ──
    if p.get('USE_VOLUME_TREND') and 'volume_trend' in factors:
        if date in factors['volume_trend'].index:
            vt = factors['volume_trend'].loc[date]
            if pd.notna(vt) and vt == 0:
                return []  # 缩量期不开仓

    # ── 基本检查 ──
    if date not in factors['industry_momentum'].index:
        return []
    if date not in factors['mom_5'].index:
        return []

    ind_mom_raw = factors['industry_momentum'].loc[date].dropna()
    m5 = factors['mom_5'].loc[date].dropna()

    # ── 选最热行业 ──
    ind_mom_sorted = ind_mom_raw.sort_values(ascending=False)
    n_top = max(1, int(len(ind_mom_sorted) * p['TOP_INDUSTRY_PCT']))
    n_top = min(n_top, p['TOP_INDUSTRIES'] * 3)
    hot_industry_names = set(ind_mom_sorted.index[:n_top])

    # ── 从热门行业中选个股 ──
    candidates = []
    industry_map = _load_industry_map()
    for code in m5.index:
        # 只选热门行业的股票
        ind = industry_map.get(code, '')
        if ind not in hot_industry_names:
            continue

        # 个股动量过滤
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

    # ── 排除已持有和近期卖出 ──
    if current_holdings:
        candidates = [c for c in candidates if c not in current_holdings]
    if sold_recently:
        candidates = [c for c in candidates if c not in sold_recently]

    if not candidates:
        return []

    # ── 成交额过滤 ──
    if date in factors['amt_rank'].index:
        amt = factors['amt_rank'].loc[date]
        candidates = [c for c in candidates if c in amt.index and amt[c] > 0.1]

    if not candidates:
        return []

    # ── 评分排序 ──
    scores = pd.Series(0.0, index=candidates)

    # 行业动量分
    if date in factors.get('stock_industry_mom', pd.DataFrame()).index:
        im = factors['stock_industry_mom'].loc[date]
        scores += im.reindex(candidates).fillna(0) * 100 * 0.30

    # 个股动量分
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

    # ── 排序选股 ──
    scores = scores.sort_values(ascending=False)
    selected = scores.index[:p['MAX_DAILY_BUY']]
    return [(code, scores[code]) for code in selected]


if __name__ == '__main__':
    print("v69: 行业动量追涨策略")
    print(f"TOP_INDUSTRIES: {DEFAULT_PARAMS['TOP_INDUSTRIES']}")
    print(f"SENTIMENT_THRESHOLD: {DEFAULT_PARAMS['SENTIMENT_THRESHOLD']}")
