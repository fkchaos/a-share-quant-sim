#!/usr/bin/env python3
"""域分割器 - 按市值分割股票池为多个域"""

import sqlite3
import pandas as pd
import numpy as np
from typing import Dict, List, Optional
from datetime import datetime


class DomainSplitter:
    """
    域分割器
    
    将股票池按市值分割为多个域：
    - 大盘域：市值 > 500亿（沪深300级别）
    - 中盘域：市值 100-500亿（中证500级别）
    - 小盘域：市值 < 100亿（中证1000级别）
    """
    
    # 默认域配置
    DEFAULT_DOMAINS = {
        'large': {
            'name': '大盘域',
            'min_cap': 500e8,  # 500亿
            'max_cap': float('inf'),
            'typical_pool': 'hs300',
        },
        'mid': {
            'name': '中盘域',
            'min_cap': 100e8,  # 100亿
            'max_cap': 500e8,
            'typical_pool': 'zz500',
        },
        'small': {
            'name': '小盘域',
            'min_cap': 0,
            'max_cap': 100e8,  # 100亿
            'typical_pool': 'zz1000',
        },
    }
    
    def __init__(self, domains: Optional[Dict] = None, db_path: str = 'data/quant_stocks.db'):
        """
        初始化域分割器
        
        Args:
            domains: 域配置字典，None则使用默认配置
            db_path: 数据库路径
        """
        self.domains = domains or self.DEFAULT_DOMAINS
        self.db_path = db_path
        self._cap_cache = {}  # 缓存市值数据
    
    def _get_market_cap(self, date: Optional[str] = None) -> pd.Series:
        """
        获取股票市值数据
        
        Args:
            date: 日期，None则使用最新日期
            
        Returns:
            pd.Series: code -> market_cap
        """
        conn = sqlite3.connect(self.db_path, timeout=15)
        
        # 获取流通股本
        fs_df = pd.read_sql_query(
            'SELECT code, float_shares FROM stock_pool_zz1800',
            conn, index_col='code'
        )
        
        # 获取最新收盘价
        if date:
            price_df = pd.read_sql_query(
                f"SELECT code, close FROM daily_kline WHERE date <= '{date}' ORDER BY date DESC",
                conn
            )
        else:
            price_df = pd.read_sql_query(
                "SELECT code, close FROM daily_kline ORDER BY date DESC",
                conn
            )
        
        conn.close()
        
        # 去重，取最新价
        price_df = price_df.drop_duplicates(subset='code', keep='first')
        price_map = price_df.set_index('code')['close']
        
        # 计算市值 = 价格 × 流通股本
        fs_series = fs_df['float_shares'].reindex(price_map.index).fillna(0)
        market_cap = price_map * fs_series
        
        return market_cap
    
    def split(self, codes: List[str], date: Optional[str] = None) -> Dict[str, List[str]]:
        """
        按市值分割股票池
        
        Args:
            codes: 股票代码列表
            date: 日期，None则使用最新日期
            
        Returns:
            Dict: {
                'large': ['600519', '601318', ...],
                'mid': ['002415', '300750', ...],
                'small': ['002049', '300059', ...]
            }
        """
        # 获取市值数据
        market_cap = self._get_market_cap(date)
        
        # 初始化结果
        result = {domain: [] for domain in self.domains}
        
        # 分割
        for code in codes:
            if code not in market_cap.index:
                # 无市值数据的放入小盘域
                result['small'].append(code)
                continue
            
            cap = market_cap[code]
            assigned = False
            
            for domain_name, config in self.domains.items():
                if config['min_cap'] <= cap < config['max_cap']:
                    result[domain_name].append(code)
                    assigned = True
                    break
            
            if not assigned:
                # 未匹配的放入小盘域
                result['small'].append(code)
        
        return result
    
    def get_domain(self, code: str, date: Optional[str] = None) -> str:
        """
        获取单只股票所属域
        
        Args:
            code: 股票代码
            date: 日期
            
        Returns:
            str: 域名称（'large'/'mid'/'small'）
        """
        market_cap = self._get_market_cap(date)
        
        if code not in market_cap.index:
            return 'small'
        
        cap = market_cap[code]
        
        for domain_name, config in self.domains.items():
            if config['min_cap'] <= cap < config['max_cap']:
                return domain_name
        
        return 'small'
    
    def get_domain_stats(self, codes: List[str], date: Optional[str] = None) -> Dict:
        """
        获取各域统计信息
        
        Args:
            codes: 股票代码列表
            date: 日期
            
        Returns:
            Dict: {
                'large': {'count': 150, 'avg_cap': 800e8, 'codes': [...]},
                'mid': {'count': 200, 'avg_cap': 250e8, 'codes': [...]},
                'small': {'count': 450, 'avg_cap': 50e8, 'codes': [...]}
            }
        """
        market_cap = self._get_market_cap(date)
        domains = self.split(codes, date)
        
        stats = {}
        for domain_name, domain_codes in domains.items():
            caps = [market_cap.get(c, 0) for c in domain_codes if c in market_cap.index]
            
            stats[domain_name] = {
                'name': self.domains[domain_name]['name'],
                'count': len(domain_codes),
                'avg_cap': np.mean(caps) if caps else 0,
                'median_cap': np.median(caps) if caps else 0,
                'min_cap': min(caps) if caps else 0,
                'max_cap': max(caps) if caps else 0,
                'codes': domain_codes[:10],  # 只返回前10个示例
            }
        
        return stats
    
    def print_stats(self, codes: List[str], date: Optional[str] = None):
        """打印各域统计信息"""
        stats = self.get_domain_stats(codes, date)
        
        print("\n" + "="*60)
        print("域分割统计")
        print("="*60)
        
        for domain_name, info in stats.items():
            print(f"\n{info['name']} ({domain_name}):")
            print(f"  股票数量: {info['count']}")
            print(f"  平均市值: {info['avg_cap']/1e8:.1f}亿")
            print(f"  中位市值: {info['median_cap']/1e8:.1f}亿")
            print(f"  市值范围: {info['min_cap']/1e8:.1f}亿 ~ {info['max_cap']/1e8:.1f}亿")
            print(f"  示例股票: {', '.join(info['codes'][:5])}")
        
        print("\n" + "="*60)


# 便捷函数
def split_by_market_cap(codes: List[str], date: Optional[str] = None, 
                        db_path: str = 'data/quant_stocks.db') -> Dict[str, List[str]]:
    """
    按市值分割股票池（便捷函数）
    
    Args:
        codes: 股票代码列表
        date: 日期
        db_path: 数据库路径
        
    Returns:
        Dict: 域分割结果
    """
    splitter = DomainSplitter(db_path=db_path)
    return splitter.split(codes, date)


def get_domain_for_stock(code: str, date: Optional[str] = None,
                         db_path: str = 'data/quant_stocks.db') -> str:
    """
    获取单只股票所属域（便捷函数）
    
    Args:
        code: 股票代码
        date: 日期
        db_path: 数据库路径
        
    Returns:
        str: 域名称
    """
    splitter = DomainSplitter(db_path=db_path)
    return splitter.get_domain(code, date)


if __name__ == '__main__':
    # 测试代码
    import sys
    sys.path.insert(0, '/root/a-share-quant-sim')
    
    from core.db import get_stock_pool
    
    # 获取股票池
    codes = get_stock_pool('zz1800')
    print(f"股票池大小: {len(codes)}")
    
    # 创建分割器
    splitter = DomainSplitter()
    
    # 打印统计信息
    splitter.print_stats(codes)
