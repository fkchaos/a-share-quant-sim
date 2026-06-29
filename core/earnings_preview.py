"""
P1_7: 业绩预告事件因子 — 计算信号

逻辑：
1. 读取 DB 中 earnings_preview 表的业绩预告事件
2. 正面预告（预增/预盈）→ 漂移效应（预告后短期看涨）
3. 负面预告（预减/预亏）→ 规避（预告后短期下跌压力）
4. 信号衰减：预告日 +N 天内逐步衰减

核心函数：
    compute_earnings_signal(panel) → pd.Series
        返回每只股票的业绩预告信号（正=利好，负=利好，0=无事件）
    earnings_signal_mask(panel, lookback=30) → pd.Series
        返回 bool Series，标识需要规避的个股
"""

import sys
import os
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'quant_stocks.db')


def _ensure_table(conn):
    """确保 earnings_preview 表存在"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS earnings_preview (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            announce_date TEXT NOT NULL,
            title TEXT,
            category TEXT DEFAULT '业绩预告',
            sentiment TEXT DEFAULT 'neutral',
            is_positive INTEGER DEFAULT 0,
            is_negative INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(code, announce_date, title)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_earnings_code ON earnings_preview(code)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_earnings_date ON earnings_preview(announce_date)")
    conn.commit()


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    _ensure_table(conn)
    return conn


def load_earnings_preview(codes: list = None, start_date: str = None, days: int = 90) -> pd.DataFrame:
    """
    从 DB 读取业绩预告数据。
    
    Args:
        codes: 股票代码列表，None 表示全部
        start_date: 起始日期 'YYYY-MM-DD'，None 表示用 days 参数
        days: 回看天数（默认90天）
    
    Returns:
        DataFrame with columns: code, announce_date, title, sentiment, is_positive, is_negative
    """
    conn = _get_conn()
    
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    
    end_date = datetime.now().strftime('%Y-%m-%d')
    
    query = "SELECT code, announce_date, title, sentiment, is_positive, is_negative FROM earnings_preview WHERE announce_date >= ? AND announce_date <= ?"
    params = [start_date, end_date]
    
    if codes is not None and len(codes) > 0:
        placeholders = ','.join(['?'] * len(codes))
        query += f" AND code IN ({placeholders})"
        params = list(params) + list(codes)
    
    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def compute_earnings_signal(panel: pd.DataFrame, date_col: str = 'date', current_date: str = None) -> pd.Series:
    """
    计算业绩预告事件信号。
    
    对于每只股票，找到最近的业绩预告事件，根据距离预告日的天数
    计算衰减信号：
    - 正面向好：预告后 1~20天内为正信号，按日衰减
    - 负面向坏：预告后 1~10天内为负信号，需要规避
    
    Args:
        panel: 含日期索引的面板数据（index=股票代码，或列为 date/code/close 等）
        date_col: 日期列名（如果 panel index 不是日期）
        current_date: 当前日期 'YYYY-MM-DD'，None 自动从 panel 推断或取今天
    
    Returns:
        pd.Series，index=股票代码，value=信号值（正=利好，负=利空，0=无事件）
    """
    if panel.empty:
        return pd.Series(dtype=float)
    
    # 获取股票代码列表
    if hasattr(panel, 'index') and panel.index.dtype == 'object':
        codes = panel.index.tolist()
    elif 'code' in panel.columns:
        codes = panel['code'].tolist()
    else:
        return pd.Series(dtype=float)
    
    # 确定当前日期
    if current_date is not None:
        current_date = pd.to_datetime(current_date)
    elif date_col in panel.columns:
        current_date = pd.to_datetime(panel[date_col]).max()
    elif hasattr(panel.index, 'date'):
        current_date = pd.to_datetime(panel.index).max()
    else:
        current_date = datetime.now()
    
    # 加载最近90天的业绩预告
    ep = load_earnings_preview(days=90)
    if ep.empty:
        return pd.Series(0.0, index=codes)
    
    # 去重：同一股票取最近一条预告
    ep = ep.sort_values('announce_date', ascending=False).drop_duplicates('code', keep='first')
    ep_dict = ep.set_index('code').to_dict('index')
    
    signals = []
    for code in codes:
        if code not in ep_dict:
            signals.append(0.0)
            continue
        
        event = ep_dict[code]
        announce_date = pd.to_datetime(event['announce_date'])
        days_since = (current_date - announce_date).days
        
        if days_since < 0:
            # 预告日在未来（数据延迟），不强信号
            signals.append(0.0)
            continue
        
        if event['is_positive'] == 1 and days_since <= 20:
            # 正面预告：20天内，衰减信号 1.0 → 0.05
            strength = max(0.05, 1.0 - days_since * 0.0475)
            signals.append(strength)
        elif event['is_negative'] == 1 and days_since <= 10:
            # 负面预告：10天内，负信号 -1.0 → -0.1
            strength = min(-0.1, -1.0 + days_since * 0.09)
            signals.append(strength)
        else:
            signals.append(0.0)
    
    return pd.Series(signals, index=codes)


def earnings_signal_mask(panel: pd.DataFrame, lookback: int = 30, negative_only: bool = True) -> pd.Series:
    """
    业绩预告规避掩码——返回 True 的股票应被排除在选股之外。
    
    用 lookback 窗口内的"累计信号"来判定：如果某只股票在 lookback 天内
    有过负面预告且仍在规避期内，则 mask=True。
    
    Args:
        panel: 面板数据
        lookback: 回看天数
        negative_only: True=只规避负面，False=正面和负面都响应
    
    Returns:
        pd.Series，index=股票代码，value=True 表示应排除
    """
    if panel.empty:
        return pd.Series(dtype=bool)
    
    if hasattr(panel, 'index') and panel.index.dtype == 'object':
        codes = panel.index.tolist()
    elif 'code' in panel.columns:
        codes = panel['code'].tolist()
    else:
        return pd.Series(False, index=[])
    
    ep = load_earnings_preview(days=lookback)
    if ep.empty:
        return pd.Series(False, index=codes)
    
    current_date = datetime.now()
    
    mask = pd.Series(False, index=codes)
    
    for _, event in ep.iterrows():
        code = event['code']
        if code not in codes:
            continue
        
        announce_date = pd.to_datetime(event['announce_date'])
        days_since = (current_date - announce_date).days
        
        # 负面预告：10天内规避
        if event['is_negative'] == 1 and days_since <= 10:
            mask[code] = True
        
        # 如果 negative_only=False，正面预告也不买（避免追高）
        if not negative_only and event['is_positive'] == 1 and days_since <= 3:
            # 正面预告后3天内"不追涨"（等回调）
            # 实际上很少触发，因为预增后趋势通常是继续的
            pass
    
    return mask


def get_recent_positive_earnings(codes: list = None, days: int = 20, limit: int = 50) -> list:
    """
    获取最近 N 天内有正面业绩预告的股票列表（按信号强度排序）。
    
    Args:
        codes: 过滤特定股票
        days: 回看天数
        limit: 返回数量上限
    
    Returns:
        list of (code, signal_strength, announce_date, title)
    """
    conn = _get_conn()
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    end_date = datetime.now().strftime('%Y-%m-%d')
    
    query = """
        SELECT code, announce_date, title, sentiment 
        FROM earnings_preview 
        WHERE is_positive = 1 AND announce_date >= ? AND announce_date <= ?
        ORDER BY announce_date DESC
        LIMIT ?
    """
    params = [start_date, end_date, limit]
    
    if codes is not None and len(codes) > 0:
        placeholders = ','.join(['?'] * len(codes))
        query = f"""
            SELECT code, announce_date, title, sentiment 
            FROM earnings_preview 
            WHERE is_positive = 1 AND announce_date >= ? AND announce_date <= ? AND code IN ({placeholders})
            ORDER BY announce_date DESC
            LIMIT ?
        """
        params = [start_date, end_date] + list(codes) + [limit]
    
    cursor = conn.execute(query, params)
    results = []
    current = datetime.now()
    for row in cursor.fetchall():
        announce = pd.to_datetime(row[1])
        days_since = (current - announce).days
        strength = max(0.05, 1.0 - days_since * 0.0475)
        results.append((row[0], strength, row[1], row[2]))
    
    conn.close()
    return results


def update_earnings_preview():
    """命令行入口：增量更新业绩预告数据"""
    from scripts.data.download_earnings_preview import main as download_main
    download_main()


if __name__ == '__main__':
    # 快速验证
    from core.db import load_panel_from_db
    panels, codes = load_panel_from_db(pool='zz1800', start_date='2024-01-01')
    close = panels[0]
    
    signal = compute_earnings_signal(close)
    mask = earnings_signal_mask(close)
    
    print(f'总股票数: {len(codes)}')
    print(f'有正面预告信号: {(signal > 0).sum()}')
    print(f'有负面预告信号: {(signal < 0).sum()}')
    print(f'应规避（负面10天内）: {mask.sum()}')
    
    if (signal > 0).sum() > 0:
        print('\n正面信号 Top5:')
        sig_series = pd.Series(signal) if not isinstance(signal, pd.Series) else signal
        top5 = sig_series[sig_series > 0].nlargest(5)
        for code, val in top5.items():
            print(f'  {code}: {val:.3f}')
