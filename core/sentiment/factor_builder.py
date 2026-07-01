#!/usr/bin/env python3
"""舆情因子构建模块 - 基于新闻情绪构建选股因子"""

import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from datetime import datetime, timedelta


class SentimentFactorBuilder:
    """舆情因子构建器"""
    
    def __init__(self, db_path: str = 'data/sentiment.db'):
        """
        初始化
        
        Args:
            db_path: 舆情数据库路径
        """
        self.db_path = db_path
    
    def build_stock_sentiment_factor(self, codes: List[str], date: str, 
                                      days: int = 7) -> pd.Series:
        """
        构建个股舆情因子
        
        Args:
            codes: 股票代码列表
            date: 日期
            days: 回溯天数
            
        Returns:
            pd.Series: 情绪得分（-1到1）
        """
        from core.sentiment.data_fetcher import SentimentDataFetcher
        
        fetcher = SentimentDataFetcher(self.db_path)
        
        scores = {}
        for code in codes:
            try:
                score = fetcher.get_news_sentiment(code, days)
                scores[code] = score
            except Exception as e:
                scores[code] = 0.0
        
        return pd.Series(scores)
    
    def build_news_heat_factor(self, codes: List[str], date: str, 
                                days: int = 7) -> pd.Series:
        """
        构建新闻热度因子
        
        Args:
            codes: 股票代码列表
            date: 日期
            days: 回溯天数
            
        Returns:
            pd.Series: 新闻热度（log(新闻数量+1)）
        """
        import sqlite3
        
        conn = sqlite3.connect(self.db_path, timeout=15)
        
        cutoff_date = (datetime.strptime(date, '%Y-%m-%d') - timedelta(days=days)).strftime('%Y-%m-%d')
        
        heat_scores = {}
        for code in codes:
            try:
                cursor = conn.execute(
                    'SELECT COUNT(*) FROM stock_news WHERE code = ? AND publish_time >= ?',
                    (code, cutoff_date)
                )
                count = cursor.fetchone()[0]
                heat_scores[code] = np.log(count + 1)
            except Exception as e:
                heat_scores[code] = 0.0
        
        conn.close()
        return pd.Series(heat_scores)
    
    def build_sentiment_momentum_factor(self, codes: List[str], date: str,
                                         short_days: int = 3, long_days: int = 7) -> pd.Series:
        """
        构建情绪动量因子（短期情绪 - 长期情绪）
        
        Args:
            codes: 股票代码列表
            date: 日期
            short_days: 短期天数
            long_days: 长期天数
            
        Returns:
            pd.Series: 情绪动量
        """
        short_sentiment = self.build_stock_sentiment_factor(codes, date, short_days)
        long_sentiment = self.build_stock_sentiment_factor(codes, date, long_days)
        
        return short_sentiment - long_sentiment
    
    def build_all_factors(self, codes: List[str], date: str) -> Dict[str, pd.Series]:
        """
        构建所有舆情因子
        
        Args:
            codes: 股票代码列表
            date: 日期
            
        Returns:
            Dict: 因子字典
        """
        return {
            'sentiment_score': self.build_stock_sentiment_factor(codes, date, days=7),
            'news_heat': self.build_news_heat_factor(codes, date, days=7),
            'sentiment_momentum': self.build_sentiment_momentum_factor(codes, date, 
                                                                       short_days=3, long_days=7),
        }
    
    def get_factor_ic(self, codes: List[str], dates: List[str], 
                      forward_returns: pd.DataFrame) -> Dict[str, float]:
        """
        计算舆情因子IC
        
        Args:
            codes: 股票代码列表
            dates: 日期列表
            forward_returns: 未来收益率面板
            
        Returns:
            Dict: 因子IC值
        """
        from scipy.stats import spearmanr
        
        ic_results = {}
        
        for date in dates:
            factors = self.build_all_factors(codes, date)
            
            for factor_name, factor_values in factors.items():
                if date not in forward_returns.index:
                    continue
                
                fwd_ret = forward_returns.loc[date]
                
                # 计算Rank IC
                common_codes = list(set(factor_values.index) & set(fwd_ret.index))
                if len(common_codes) < 10:
                    continue
                
                ic, _ = spearmanr(factor_values[common_codes], fwd_ret[common_codes])
                
                if factor_name not in ic_results:
                    ic_results[factor_name] = []
                ic_results[factor_name].append(ic)
        
        # 计算平均IC
        return {k: np.mean(v) if v else 0.0 for k, v in ic_results.items()}


if __name__ == '__main__':
    # 测试代码
    builder = SentimentFactorBuilder()
    
    # 测试股票池
    codes = ['000001', '000002', '600519', '601318']
    date = '2026-06-30'
    
    print("测试舆情因子构建...")
    print("="*60)
    
    factors = builder.build_all_factors(codes, date)
    
    for factor_name, factor_values in factors.items():
        print(f"\n{factor_name}:")
        print(factor_values)
