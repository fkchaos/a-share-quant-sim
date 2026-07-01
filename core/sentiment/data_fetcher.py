#!/usr/bin/env python3
"""舆情数据获取模块 - 从东方财富获取新闻数据"""

import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import time
import os


class SentimentDataFetcher:
    """舆情数据获取器"""
    
    def __init__(self, db_path: str = 'data/sentiment.db'):
        """
        初始化
        
        Args:
            db_path: 舆情数据库路径
        """
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """初始化数据库表"""
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.execute('''CREATE TABLE IF NOT EXISTS stock_news (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            title TEXT,
            content TEXT,
            publish_time DATETIME,
            source TEXT,
            url TEXT,
            sentiment_score REAL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(code, title, publish_time)
        )''')
        conn.execute('''CREATE INDEX IF NOT EXISTS idx_news_code_time 
            ON stock_news(code, publish_time)''')
        conn.commit()
        conn.close()
    
    def fetch_stock_news(self, code: str, max_retries: int = 3) -> pd.DataFrame:
        """
        获取个股新闻
        
        Args:
            code: 股票代码
            max_retries: 最大重试次数
            
        Returns:
            pd.DataFrame: 新闻数据
        """
        import akshare as ak
        
        for attempt in range(max_retries):
            try:
                df = ak.stock_news_em(symbol=code)
                if df is not None and len(df) > 0:
                    df['code'] = code
                    return df
                else:
                    return pd.DataFrame()
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(1)  # 等待1秒后重试
                else:
                    print(f"获取{code}新闻失败: {e}")
                    return pd.DataFrame()
    
    def fetch_and_save_news(self, codes: List[str], delay: float = 0.5):
        """
        批量获取并保存新闻
        
        Args:
            codes: 股票代码列表
            delay: 请求间隔（秒）
        """
        conn = sqlite3.connect(self.db_path, timeout=15)
        
        for i, code in enumerate(codes):
            if (i + 1) % 10 == 0:
                print(f"进度: {i+1}/{len(codes)}")
            
            df = self.fetch_stock_news(code)
            if len(df) > 0:
                for _, row in df.iterrows():
                    try:
                        conn.execute('''INSERT OR IGNORE INTO stock_news 
                            (code, title, content, publish_time, source, url)
                            VALUES (?, ?, ?, ?, ?, ?)''',
                            (code, row.get('新闻标题', ''), row.get('新闻内容', ''),
                             row.get('发布时间', ''), row.get('文章来源', ''), row.get('新闻链接', '')))
                    except Exception as e:
                        pass
            
            time.sleep(delay)
        
        conn.commit()
        conn.close()
        print(f"完成! 共处理 {len(codes)} 只股票")
    
    def get_stock_news(self, code: str, days: int = 7) -> pd.DataFrame:
        """
        获取个股近期新闻
        
        Args:
            code: 股票代码
            days: 天数
            
        Returns:
            pd.DataFrame: 新闻数据
        """
        conn = sqlite3.connect(self.db_path, timeout=15)
        
        cutoff_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
        
        df = pd.read_sql_query(f'''
            SELECT * FROM stock_news 
            WHERE code = ? AND publish_time >= ?
            ORDER BY publish_time DESC
        ''', conn, params=(code, cutoff_date))
        
        conn.close()
        return df
    
    def get_news_sentiment(self, code: str, days: int = 7) -> float:
        """
        计算个股新闻情绪得分
        
        Args:
            code: 股票代码
            days: 天数
            
        Returns:
            float: 情绪得分（-1到1，越大越正面）
        """
        from snownlp import SnowNLP
        
        df = self.get_stock_news(code, days)
        if len(df) == 0:
            return 0.0
        
        scores = []
        for _, row in df.iterrows():
            title = row.get('title', '')
            if title:
                try:
                    s = SnowNLP(title)
                    score = s.sentiments  # 0-1
                    scores.append(score)
                except:
                    pass
        
        if not scores:
            return 0.0
        
        # 转换为-1到1
        avg_score = np.mean(scores)
        return (avg_score - 0.5) * 2  # 0-1 -> -1到1
    
    def get_batch_sentiment(self, codes: List[str], days: int = 7) -> Dict[str, float]:
        """
        批量获取情绪得分
        
        Args:
            codes: 股票代码列表
            days: 天数
            
        Returns:
            Dict: code -> sentiment_score
        """
        result = {}
        for code in codes:
            result[code] = self.get_news_sentiment(code, days)
        return result


if __name__ == '__main__':
    # 测试代码
    fetcher = SentimentDataFetcher()
    
    print("测试获取000001新闻...")
    df = fetcher.fetch_stock_news('000001')
    print(f"获取到 {len(df)} 条新闻")
    
    if len(df) > 0:
        print("\n最新3条:")
        print(df.head(3)[['新闻标题', '发布时间']].to_string())
        
        print("\n计算情绪得分...")
        score = fetcher.get_news_sentiment('000001')
        print(f"情绪得分: {score:.2f}")
