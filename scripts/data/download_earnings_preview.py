#!/usr/bin/env python3
"""
下载业绩预告数据并存储到 quant_stocks.db 的 earnings_preview 表。

数据源：巨潮资讯 disclosure（category='业绩预告'）
接口：akshare stock_zh_a_disclosure_report_cninfo

用法：
    python3 scripts/data/download_earnings_preview.py                    # 增量更新（最近7天）
    python3 scripts/data/download_earnings_preview.py --full            # 全量（最近6个月）
    python3 scripts/data/download_earnings_preview.py --start 20260101   # 指定起始日期
"""

import sys
import os
import sqlite3
import argparse
import time
import re
import logging
from datetime import datetime, timedelta

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import akshare as ak
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'quant_stocks.db')

# 业绩预告分类关键词（基于巨潮资讯公告标题）
POSITIVE_KEYWORDS = ['预增', '预盈', '扭亏', '续盈', '大幅增长', '大幅增加', '业绩预增', '业绩预盈']
NEGATIVE_KEYWORDS = ['预减', '预亏', '续亏', '大幅下降', '大幅减少', '预亏公告', '业绩预减', '业绩预亏']
NEUTRAL_LABELS = ['业绩预告']  # 无明确方向的为中性预告


def init_earnings_table(conn):
    """创建 earnings_preview 表（如果不存在）"""
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


def classify_sentiment(title: str) -> tuple:
    """
    根据公告标题判断业绩预告情绪。
    
    Returns:
        (sentiment: str, is_positive: int, is_negative: int)
    """
    if not title:
        return 'neutral', 0, 0
    
    # 检查正面关键词
    for kw in POSITIVE_KEYWORDS:
        if kw in title:
            return 'positive', 1, 0
    
    # 检查负面关键词
    for kw in NEGATIVE_KEYWORDS:
        if kw in title:
            return 'negative', 0, 1
    
    return 'neutral', 0, 0


def fetch_earnings_preview(start_date: str, end_date: str) -> pd.DataFrame:
    """
    从巨潮资讯拉取业绩预告数据。
    由于接口 symbol='' 返回全量分页，对长时间窗口可能很慢（153页约60秒+）。
    建议增量模式用 --days 7，季度窗口用 --full（最近90天约3页）。
    """
    logger.info(f'拉取业绩预告: {start_date} ~ {end_date}')
    
    all_dfs = []
    
    # 分页拉取（接口支持分页，symbol='' 全量）
    # 从第1页开始，直到空或超时
    page = 1
    max_pages = 50  # 安全防护：最多50页（约1000条）
    while page <= max_pages:
        try:
            # akshare 接口通过 pagination 参数隐式分页
            # 实际上 stock_zh_a_disclosure_report_cninfo 不支持 page 参数
            # 需要检查接口签名
            df = ak.stock_zh_a_disclosure_report_cninfo(
                symbol='',
                market='沪深京',
                keyword='',
                category='业绩预告',
                start_date=start_date,
                end_date=end_date
            )
            if df is not None and not df.empty:
                all_dfs.append(df)
                logger.debug(f'单次查询返回 {len(df)} 条')
            break  # akshare 接口一次返回全量，不需要循环分页
        except Exception as e:
            logger.error(f'接口调用失败: {e}')
            break
    
    if not all_dfs:
        logger.warning('未获取到数据')
        return pd.DataFrame()
    
    df = pd.concat(all_dfs, ignore_index=True)
    
    # 标准化列名
    df = df.rename(columns={
        '代码': 'code',
        '简称': 'name',
        '公告标题': 'title',
        '公告时间': 'announce_time',
    })
    
    # 提取日期部分
    df['announce_date'] = pd.to_datetime(df['announce_time']).dt.strftime('%Y-%m-%d')
    
    # 分类情绪
    sentiments = df['title'].apply(classify_sentiment)
    df['sentiment'] = sentiments.apply(lambda x: x[0])
    df['is_positive'] = sentiments.apply(lambda x: x[1])
    df['is_negative'] = sentiments.apply(lambda x: x[2])
    df['category'] = '业绩预告'
    
    # 去重：同一 stock + 同一天 + 同一标题
    df = df.drop_duplicates(subset=['code', 'announce_date', 'title'])
    
    logger.info(f'获取 {len(df)} 条业绩预告，覆盖 {df["code"].nunique()} 只股票')
    return df


def save_to_db(df: pd.DataFrame, conn) -> int:
    """保存业绩预告数据到数据库，忽略重复（用 executemany 批量写入）"""
    if df.empty:
        return 0
    
    before = conn.execute("SELECT COUNT(*) FROM earnings_preview").fetchone()[0]
    
    rows = []
    for _, row in df.iterrows():
        rows.append((
            str(row['code']),
            str(row['announce_date']),
            str(row.get('title', '')),
            str(row.get('category', '业绩预告') or '业绩预告'),
            str(row.get('sentiment', 'neutral') or 'neutral'),
            int(row.get('is_positive', 0) or 0),
            int(row.get('is_negative', 0) or 0),
        ))
    
    conn.executemany("""
        INSERT OR IGNORE INTO earnings_preview 
        (code, announce_date, title, category, sentiment, is_positive, is_negative)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    
    after = conn.execute("SELECT COUNT(*) FROM earnings_preview").fetchone()[0]
    saved = after - before
    logger.info(f'新增 {saved} 条，重复 {len(rows) - saved} 条')
    return saved


def get_existing_dates(conn) -> set:
    """获取已有数据的日期集合，避免重复拉取"""
    cursor = conn.execute("SELECT DISTINCT announce_date FROM earnings_preview")
    return {row[0] for row in cursor.fetchall()}


def main():
    parser = argparse.ArgumentParser(description='下载业绩预告数据')
    parser.add_argument('--full', action='store_true', help='全量更新（最近90天，约3页查询）')
    parser.add_argument('--start', type=str, help='起始日期 (YYYYMMDD)')
    parser.add_argument('--end', type=str, help='结束日期 (YYYYMMDD)')
    parser.add_argument('--days', type=int, default=7, help='增量更新天数（默认7）')
    args = parser.parse_args()
    
    conn = sqlite3.connect(DB_PATH)
    init_earnings_table(conn)
    
    if args.start:
        start_date = args.start
    elif args.full:
        start_date = (datetime.now() - timedelta(days=90)).strftime('%Y%m%d')
    else:
        start_date = (datetime.now() - timedelta(days=args.days)).strftime('%Y%m%d')
    
    end_date = args.end or datetime.now().strftime('%Y%m%d')
    
    logger.info(f'日期范围: {start_date} ~ {end_date}')
    
    # 检查已有日期（仅增量模式）
    if not args.full:
        existing = get_existing_dates(conn)
        logger.info(f'数据库已有 {len(existing)} 个日期的数据')
    
    df = fetch_earnings_preview(start_date, end_date)
    saved = save_to_db(df, conn)
    
    # 统计
    total = conn.execute("SELECT COUNT(*) FROM earnings_preview").fetchone()[0]
    logger.info(f'数据库累计: {total} 条业绩预告')
    
    # 情绪分布
    pos = conn.execute("SELECT COUNT(*) FROM earnings_preview WHERE is_positive=1").fetchone()[0]
    neg = conn.execute("SELECT COUNT(*) FROM earnings_preview WHERE is_negative=1").fetchone()[0]
    neu = conn.execute("SELECT COUNT(*) FROM earnings_preview WHERE sentiment='neutral'").fetchone()[0]
    logger.info(f'情绪分布: 正面={pos}, 负面={neg}, 中性={neu}')
    
    conn.close()
    return saved


if __name__ == '__main__':
    main()
