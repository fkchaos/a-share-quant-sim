"""数据源标准化接口

所有 provider 必须实现此接口，输出符合标准字段定义的数据。
"""
from abc import ABC, abstractmethod
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, asdict
import pandas as pd


# ── 标准字段定义 ──────────────────────────────────────────────
# 日K线标准字段
KLINE_COLUMNS = [
    'open', 'high', 'low', 'close',  # 元
    'volume',      # 手（与现有SQLite存储单位一致）
    'amount',      # 元
    'turnover',    # 百分比（如 1.5 = 1.5%），None 表示无数据
    'pct_change',  # 百分比（如 3.2 = 3.2%）
    'tradestatus', # 0=停牌, 1=正常
    'is_st',       # 0=正常, 1=ST
]

# 流通股本标准字段
FLOAT_SHARES_COLUMNS = ['code', 'float_shares']  # float_shares 单位：股


@dataclass
class KlineRecord:
    """单条K线记录"""
    code: str           # 6位数字代码
    date: str           # YYYY-MM-DD
    open: float         # 元
    high: float
    low: float
    close: float
    volume: int         # 手
    amount: float       # 元
    turnover: Optional[float] = None   # 百分比
    pct_change: Optional[float] = None # 百分比
    tradestatus: int = 1                # 0=停牌, 1=正常
    is_st: int = 0                      # 0=正常, 1=ST
    
    def to_dict(self) -> dict:
        return asdict(self)


class DataProvider(ABC):
    """数据源抽象接口"""
    
    @property
    @abstractmethod
    def name(self) -> str:
        """数据源名称，如 'tencent', 'baostock'"""
        pass
    
    @abstractmethod
    def get_daily_kline(
        self,
        codes: List[str],
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """获取日K线数据（标准化后）
        
        Args:
            codes: 股票代码列表，6位数字，如 ['600519', '000001']
            start_date: 开始日期，YYYY-MM-DD
            end_date: 结束日期，YYYY-MM-DD
        
        Returns:
            DataFrame，index=(date, code)，columns=KLINE_COLUMNS
            或者 MultiIndex DataFrame：index=date, columns=code, 每个cell是dict
        """
        pass
    
    @abstractmethod
    def get_float_shares(
        self,
        codes: Optional[List[str]] = None,
        date: Optional[str] = None
    ) -> pd.DataFrame:
        """获取流通股本
        
        Args:
            codes: 股票代码列表，None=全部
            date: 指定日期（point-in-time），None=最新
        
        Returns:
            DataFrame: code(str), float_shares(int, 股)
        """
        pass
    
    @abstractmethod
    def get_index_components(
        self,
        index_code: str,
        date: Optional[str] = None
    ) -> List[str]:
        """获取指数成分股
        
        Args:
            index_code: 指数代码，如 '000852'=中证1000
            date: 指定日期（point-in-time），None=最新
        
        Returns:
            股票代码列表（6位数字）
        """
        pass
    
    def health_check(self) -> bool:
        """检查数据源是否可用（默认实现）"""
        try:
            df = self.get_daily_kline(['000001'], '2024-01-02', '2024-01-03')
            return df is not None and len(df) > 0
        except Exception:
            return False
    
    def __repr__(self):
        return f"<{self.__class__.__name__}({self.name})>"
