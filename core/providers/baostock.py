"""BaoStock 数据源 Provider

特点：
- 免费、无需注册
- 有 turn（换手率）、tradestatus（停牌）、isST（ST标记）
- 成交量单位：股（标准单位）
- 代码格式：sh.600519 → 需转换为 600519
"""
import pandas as pd
from typing import Optional, List
from core.data_provider import DataProvider

# 延迟导入 baostock，避免未安装时报错
_bs = None


def _get_bs():
    """懒加载 baostock"""
    global _bs
    if _bs is None:
        import baostock as bs
        _bs = bs
    return _bs


def _normalize_code(code: str) -> str:
    """代码标准化：sh.600519 → 600519"""
    code = str(code).strip()
    if '.' in code:
        return code.split('.')[1]
    return code


def _to_bs_code(code: str) -> str:
    """6位代码转 BaoStock 格式：600519 → sh.600519"""
    code = str(code).strip()
    if '.' in code:
        return code
    if code.startswith('6'):
        return f'sh.{code}'
    elif code.startswith('0') or code.startswith('3'):
        return f'sz.{code}'
    elif code.startswith('4') or code.startswith('8'):
        return f'bj.{code}'
    return f'sh.{code}'


def _safe_float(val, default=None) -> Optional[float]:
    """安全转 float"""
    if val is None or val == '':
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def _safe_int(val, default=None) -> Optional[int]:
    """安全转 int"""
    if val is None or val == '':
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


class BaoStockProvider(DataProvider):
    """BaoStock 数据源实现"""
    
    def __init__(self, cache_ttl: int = 3600):
        """
        Args:
            cache_ttl: 缓存有效期（秒），默认1小时
        """
        self.cache_ttl = cache_ttl
        self._cache = {}
        self._connected = False
    
    @property
    def name(self) -> str:
        return 'baostock'
    
    def _login(self):
        """登录 BaoStock"""
        if not self._connected:
            bs = _get_bs()
            lg = bs.login()
            if lg.error_code != '0':
                raise ConnectionError(f"BaoStock login failed: {lg.error_msg}")
            self._connected = True
    
    def _logout(self):
        """登出 BaoStock"""
        if self._connected:
            bs = _get_bs()
            bs.logout()
            self._connected = False
    
    def get_daily_kline(
        self,
        codes: List[str],
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """获取日K线数据（标准化后）
        
        Returns:
            DataFrame，index=(date, code)，columns=KLINE_COLUMNS
        """
        self._login()
        bs = _get_bs()
        
        fields = "date,code,open,high,low,close,volume,amount,turn,tradestatus,pctChg,isST"
        
        all_records = []
        
        for code in codes:
            bs_code = _to_bs_code(code)
            rs = bs.query_history_k_data_plus(
                bs_code, fields,
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="3"  # 不复权
            )
            
            if rs.error_code != '0':
                continue
            
            while rs.next():
                row = rs.get_row_data()
                # fields: date,code,open,high,low,close,volume,amount,turn,tradestatus,pctChg,isST
                record = {
                    'date': row[0],
                    'code': _normalize_code(row[1]),
                    'open': _safe_float(row[2], 0),
                    'high': _safe_float(row[3], 0),
                    'low': _safe_float(row[4], 0),
                    'close': _safe_float(row[5], 0),
                    'volume': _safe_int(row[6], 0) // 100,  # 股→手（标准单位）
                    'amount': _safe_float(row[7], 0),    # 元（已是标准）
                    'turnover': _safe_float(row[8]),     # 百分比（已是标准）
                    'tradestatus': _safe_int(row[9], 1),  # 0/1
                    'pct_change': _safe_float(row[10]),  # 百分比
                    'is_st': _safe_int(row[11], 0),      # 0/1
                }
                all_records.append(record)
        
        if not all_records:
            return pd.DataFrame(columns=['code', 'date'] + [
                'open', 'high', 'low', 'close', 'volume', 'amount',
                'turnover', 'tradestatus', 'pct_change', 'is_st'
            ])
        
        df = pd.DataFrame(all_records)
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        
        return df
    
    def get_float_shares(
        self,
        codes: Optional[List[str]] = None,
        date: Optional[str] = None
    ) -> pd.DataFrame:
        """获取流通股本
        
        注意：BaoStock 的 query_history_k_data_plus 没有直接返回流通股本，
        需要通过其他方式获取。暂时返回空，后续可扩展。
        """
        # TODO: BaoStock 流通股本需要额外实现
        # 暂时返回空 DataFrame
        return pd.DataFrame(columns=['code', 'float_shares'])
    
    def get_index_components(
        self,
        index_code: str,
        date: Optional[str] = None
    ) -> List[str]:
        """获取指数成分股
        
        支持：上证50(sz50)、沪深300(hs300)、中证500(zz500)
        不支持：中证1800
        """
        self._login()
        bs = _get_bs()
        
        # 映射指数代码
        index_map = {
            '000016': ('query_sz50_stocks', 'sz50'),
            '000300': ('query_hs300_stocks', 'hs300'),
            '000905': ('query_zz500_stocks', 'zz500'),
        }
        
        if index_code not in index_map:
            raise NotImplementedError(
                f"BaoStock 不支持指数 {index_code}，仅支持 {list(index_map.keys())}"
            )
        
        method_name, _ = index_map[index_code]
        method = getattr(bs, method_name)
        rs = method()
        
        if rs.error_code != '0':
            return []
        
        codes = []
        while rs.next():
            row = rs.get_row_data()
            codes.append(_normalize_code(row[1]))
        
        return codes
    
    def health_check(self) -> bool:
        """检查 BaoStock 是否可用"""
        try:
            self._login()
            bs = _get_bs()
            rs = bs.query_history_k_data_plus(
                "sh.600519", "date,close",
                start_date="2024-01-02", end_date="2024-01-03",
                frequency="d"
            )
            if rs.error_code != '0':
                return False
            data = []
            while rs.next():
                data.append(rs.get_row_data())
            return len(data) > 0
        except Exception:
            return False
