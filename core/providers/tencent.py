"""腾讯行情数据源 Provider

特点：
- 免费、无需注册
- 有 turnover=None（无换手率）、无停牌/ST标记
- 成交量单位：股（标准单位，与 BaoStock 一致）
- 代码格式：sh600519 → 需转换为 600519
"""
import os
import requests
import pandas as pd
import numpy as np
from typing import Optional, List
from core.data_provider import DataProvider

# 腾讯行情接口配置
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'http://web.ifzq.gtimg.cn',
}
BASE_URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def _normalize_code(code: str) -> str:
    """代码标准化：sh600519 → 600519"""
    code = str(code).strip()
    if code.startswith('sh') or code.startswith('sz'):
        return code[2:]
    return code


def _to_tencent_code(code: str) -> str:
    """6位代码转腾讯格式：600519 → sh600519"""
    code = str(code).strip()
    if code.startswith('sh') or code.startswith('sz'):
        return code
    if code.startswith('6') or code.startswith('9'):
        return f'sh{code}'
    elif code.startswith('0') or code.startswith('3') or code.startswith('2'):
        return f'sz{code}'
    return f'sz{code}'


class TencentProvider(DataProvider):
    """腾讯行情数据源实现"""
    
    def __init__(self, timeout: int = 15, retry: int = 3):
        """
        Args:
            timeout: 请求超时时间（秒）
            retry: 重试次数
        """
        self.timeout = timeout
        self.retry = retry
    
    @property
    def name(self) -> str:
        return 'tencent'
    
    def _fetch_kline(self, code: str, days: int = 500) -> Optional[pd.DataFrame]:
        """从腾讯接口获取K线数据"""
        tx_code = _to_tencent_code(code)
        params = {'param': f"{tx_code},day,,,{days},qfq"}
        
        for attempt in range(self.retry):
            try:
                r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=self.timeout)
                data = r.json()
                
                if data.get('code') != 0:
                    return None
                
                # 提取股票数据（key 去掉市场前缀）
                stock_key = tx_code.replace('sh', '').replace('sz', '')
                stock_data = data.get('data', {}).get(stock_key, None)
                if stock_data is None:
                    stock_data = data.get('data', {}).get(tx_code, None)
                if stock_data is None:
                    return None
                
                # 优先取前复权数据
                klines = stock_data.get('qfqday') or stock_data.get('day')
                if not klines:
                    return None
                
                # 解析
                records = []
                for k in klines:
                    if len(k) < 6:
                        continue
                    records.append({
                        'date': k[0],
                        'open': float(k[1]),
                        'close': float(k[2]),
                        'high': float(k[3]),
                        'low': float(k[4]),
                        'volume': int(float(k[5])),  # 手（标准单位，腾讯原始返回）
                    })
                
                df = pd.DataFrame(records)
                if df.empty:
                    return None
                
                df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                df = df.set_index('date')
                df = df.sort_index()
                
                # 估算成交额（腾讯接口不提供）
                vwap = (df['open'] + df['close'] + df['high'] + df['low']) / 4
                df['amount'] = vwap * df['volume']
                
                return df
                
            except Exception as e:
                if attempt < self.retry - 1:
                    continue
                raise e
        
        return None
    
    def get_daily_kline(
        self,
        codes: List[str],
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """获取日K线数据（标准化后）
        
        注意：腾讯API只返回最近N天数据，不支持指定历史日期范围。
        如果需要精确的历史数据，请使用 BaoStock provider。
        
        Returns:
            DataFrame，index=(date, code)，columns=标准字段
        """
        all_records = []
        
        for code in codes:
            try:
                # 估算需要的天数（历史日期范围可能较长）
                from datetime import datetime, timedelta
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                days_needed = (end_dt - start_dt).days + 30  # 多取30天缓冲
                
                df = self._fetch_kline(code, days=min(days_needed, 500))
                if df is None or df.empty:
                    continue
                
                # 过滤日期范围
                mask = (df.index >= start_date) & (df.index <= end_date)
                df = df.loc[mask]
                
                if df.empty:
                    continue
                
                # 转换为标准格式
                for date_idx, row in df.iterrows():
                    record = {
                        'date': date_idx,
                        'code': _normalize_code(code),
                        'open': row['open'],
                        'high': row['high'],
                        'low': row['low'],
                        'close': row['close'],
                        'volume': int(row['volume']),
                        'amount': row['amount'],
                        'turnover': None,      # 腾讯无此字段
                        'tradestatus': 1,       # 默认正常交易
                        'pct_change': None,     # 需计算
                        'is_st': 0,             # 腾讯无此字段
                    }
                    all_records.append(record)
                
            except Exception as e:
                continue
        
        if not all_records:
            return pd.DataFrame(columns=['code', 'date'] + [
                'open', 'high', 'low', 'close', 'volume', 'amount',
                'turnover', 'tradestatus', 'pct_change', 'is_st'
            ])
        
        df = pd.DataFrame(all_records)
        
        # 计算涨跌幅
        if len(df) > 0:
            df = df.sort_values(['code', 'date'])
            df['pct_change'] = df.groupby('code')['close'].pct_change() * 100
        
        return df
    
    def get_float_shares(
        self,
        codes: Optional[List[str]] = None,
        date: Optional[str] = None
    ) -> pd.DataFrame:
        """腾讯无此数据"""
        return pd.DataFrame(columns=['code', 'float_shares'])
    
    def get_index_components(
        self,
        index_code: str,
        date: Optional[str] = None
    ) -> List[str]:
        """腾讯无此数据"""
        raise NotImplementedError("腾讯API不支持指数成分股查询")
    
    def health_check(self) -> bool:
        """检查腾讯API是否可用"""
        try:
            df = self._fetch_kline('000001', days=5)
            return df is not None and len(df) > 0
        except Exception:
            return False
