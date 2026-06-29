#!/usr/bin/env python3
"""
v61: BigQuant 换手率3因子低流动性溢价策略
复现: https://bigquant.com/square/ai/5c58cce8-fba7-e78e-c579-0c7340a07d2b
- 3个换手率因子: turn_5 (5日均换手率), turn_20 (20日均换手率), turn_std_20 (20日换手率波动)
- 负向: 低换手率 = 好机会
- 等权评分, 选前5只
- 5日调仓
- BigQuant: 年化38.24%, 夏普1.16, 回撤31.20%
"""

DEFAULT_PARAMS = {
    'MAX_HOLDINGS': 5,
    'REBALANCE_DAYS': 5,
    'BUY_SIGNAL_THRESHOLD': 0.0,
}

def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None, extra_data=None):
    """计算换手率因子"""
    # 获取 float_shares
    import sqlite3, pandas as pd
    conn = sqlite3.connect('data/quant_stocks.db')
    fs = pd.read_sql_query('SELECT code, float_shares FROM stock_pool_zz1800', conn, index_col='code')['float_shares']
    conn.close()

    codes = close_panel.columns.tolist()
    fs_arr = fs.reindex(codes).fillna(fs.median())

    # turnover = volume / float_shares (日换手率)
    turnover = volume_panel.div(fs_arr, axis=1)

    # 3个因子 (负向: 低=好)
    factors = pd.DataFrame(index=close_panel.index, columns=codes, dtype=float)
    factors['turnover_5d'] = turnover.rolling(5, min_periods=3).mean().iloc[-1]
    factors['turnover_20d'] = turnover.rolling(20, min_periods=10).mean().iloc[-1]
    factors['turnover_std_20d'] = turnover.rolling(20, min_periods=10).std().iloc[-1]

    # 等权评分: 所有因子 rank 越小越好, 越小排名越高
    scores = pd.Series(0.0, index=codes)
    for col in ['turnover_5d', 'turnover_20d', 'turnover_std_20d']:
        ranked = factors[col].rank(ascending=True, pct=True)  # 低换手率=高分
        scores += (1 - ranked)  # 取反: 低排名=高分

    return scores.sort_values(ascending=False)


def select_stocks(factors, date, close_panel, volume_panel, amount_panel,
                  high_panel, low_panel, current_holdings, params=None):
    """选股: 等权评分后选前N只"""
    p = params or DEFAULT_PARAMS
    n = p.get('MAX_HOLDINGS', 5)

    # factors 是 Series, index=code, values=score
    candidates = factors.head(n).index.tolist()

    # 排除已持有
    held = set(current_holdings.keys()) if current_holdings else set()
    buy_list = [c for c in candidates if c not in held]

    return [(code, 1.0) for code in buy_list[:n]]
