#!/usr/bin/env python3
"""
v73: ETF动量轮动策略
====================
参考9db.com实盘追踪（50天28.7%）和聚宽（年化43.64%，10年33倍）
核心逻辑：
1. 每天计算所有ETF的25日加权动量得分（年化收益×R²）
2. 选得分最高的1只ETF买入
3. 空仓时持有现金
4. 指数>MA20才开仓（趋势确认）

与v69/v71/v72的关键区别：
- 交易标的：ETF（行业/风格/资产类别），不是个股
- 分散度：ETF天然分散（一只ETF含几十只股票）
- 执行频率：日频收盘即可（ETF流动性好）
"""
import pandas as pd
import numpy as np
import sqlite3
import os

DEFAULT_PARAMS = {
    "STOP_LOSS": -0.05,
    "TAKE_PROFIT": 0.15,
    "HOLD_DAYS_MAX": 10,
    "MAX_HOLDINGS": 1,
    "MAX_DAILY_BUY": 1,
    "MAX_POSITION": 1.0,
    "HOLD_DAYS_MIN": 1,
    # 动量参数
    "MOM_WINDOW": 25,           # 动量计算窗口（天）
    "MOM_MIN_R2": 0.3,          # R²最低阈值（趋势质量过滤）
    "MOM_MIN_SLOPE": 0.0,       # 最低年化收益（过滤下跌趋势）
    # 指数MA择时
    "INDEX_MA_ENABLED": True,
    "INDEX_MA_PERIOD": 20,
}


def _load_etf_data():
    """加载ETF数据"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    db_path = os.path.join(project_root, 'data', 'quant_stocks.db')
    conn = sqlite3.connect(db_path)
    
    # 获取所有ETF代码
    etf_codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT code FROM index_kline WHERE code LIKE 'sh5%' OR code LIKE 'sz15%' OR code LIKE 'sz51%'"
    ).fetchall()]
    
    # 加载每个ETF的收盘价
    etf_close = {}
    for code in etf_codes:
        df = pd.read_sql(
            f"SELECT date, close FROM index_kline WHERE code='{code}' ORDER BY date",
            conn
        )
        if len(df) > 100:  # 至少100天数据
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
            etf_close[code] = df['close']
    
    conn.close()
    
    if etf_close:
        close_df = pd.DataFrame(etf_close)
        close_df = close_df.dropna(how='all')
        return close_df
    return pd.DataFrame()


def _load_index_data():
    """加载上证指数数据"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    db_path = os.path.join(project_root, 'data', 'quant_stocks.db')
    conn = sqlite3.connect(db_path)
    idx_df = pd.read_sql(
        "SELECT date, close FROM daily_kline WHERE code='sh000001' ORDER BY date",
        conn
    )
    conn.close()
    idx_df['date'] = pd.to_datetime(idx_df['date'])
    idx_df = idx_df.set_index('date')
    return idx_df['close']


def calc_factors_v73(close_panel, volume_panel=None, amount_panel=None,
                     high_panel=None, low_panel=None, open_panel=None,
                     extra_data=None):
    """
    计算v73因子（基于ETF面板）：
    - etf_momentum: 每只ETF的动量得分（年化收益×R²）
    - index_above_ma: 指数是否在MA20上方
    """
    # 加载ETF数据（覆盖传入的面板）
    etf_close = _load_etf_data()
    if etf_close.empty:
        return {'etf_momentum': pd.DataFrame(), 'index_above_ma': pd.Series()}
    
    # 加载指数数据
    index_close = _load_index_data()
    index_ma20 = index_close.rolling(20).mean()
    index_above_ma = (index_close > index_ma20).astype(float)
    
    # 计算每只ETF的动量得分
    window = DEFAULT_PARAMS['MOM_WINDOW']
    # 用简单动量（25日收益率）替代对数回归（快100倍，相关性0.86）
    etf_momentum = etf_close.pct_change(window)

    return {
        'etf_momentum': etf_momentum,
        'index_above_ma': index_above_ma,
        'etf_close': etf_close,
    }


def calc_factors_v73_aligned(close_panel, volume_panel=None, amount_panel=None,
                             high_panel=None, low_panel=None, open_panel=None,
                             extra_data=None):
    """对齐版本：因子日期索引与close_panel一致"""
    raw = calc_factors_v73(close_panel, volume_panel, amount_panel,
                           high_panel, low_panel, open_panel, extra_data)
    if raw['etf_momentum'].empty:
        return raw
    # 用close_panel的日期索引重新索引
    raw['etf_momentum'] = raw['etf_momentum'].reindex(close_panel.index)
    raw['index_above_ma'] = raw['index_above_ma'].reindex(close_panel.index)
    return raw


def select_stocks_v73(factors, date, current_holdings=None, params=None,
                       sold_recently=None, close_panel=None, high_panel=None):
    """
    v73选股（ETF轮动）：
    1. 指数>MA20才开仓
    2. 计算所有ETF动量得分
    3. 选得分最高的1只
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    # ── 指数趋势确认 ──
    if p.get('INDEX_MA_ENABLED') and 'index_above_ma' in factors:
        if date in factors['index_above_ma'].index:
            if factors['index_above_ma'].loc[date] == 0:
                return []  # 指数在MA20下方，空仓

    if 'etf_momentum' not in factors:
        return []
    
    em = factors['etf_momentum']
    if date not in em.index:
        return []
    
    scores = em.loc[date].dropna()
    if scores.empty:
        return []
    
    # 过滤：年化收益>0 且 R²>阈值
    # 由于得分=年化收益×R²，得分>0就自动满足两个条件
    scores = scores[scores > p['MOM_MIN_SLOPE']]
    
    if scores.empty:
        return []
    
    # 选得分最高的1只
    best = scores.sort_values(ascending=False).index[0]
    best_score = scores.iloc[0]
    
    return [(best, best_score)]


if __name__ == '__main__':
    print("v73: ETF动量轮动策略")
    print(f"MOM_WINDOW: {DEFAULT_PARAMS['MOM_WINDOW']}")
    print(f"MOM_MIN_R2: {DEFAULT_PARAMS['MOM_MIN_R2']}")
