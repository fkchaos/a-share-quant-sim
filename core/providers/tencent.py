"""腾讯行情数据源 Provider

特点：
- 免费、无需注册
- K线接口 (web.ifzq.gtimg.cn/appstock/app/fqkline/get)：前复权历史K线
- 分时接口 (web.ifzq.gtimg.cn/appstock/app/minute/query)：盘中当天数据（同域名不同时路径，WAF可能只拦K线）


- 无换手率、无停牌/ST标记
- 成交量单位：手
- 代码格式：sh600519 → 需转换为 600519
"""
import requests
import pandas as pd
import numpy as np
from typing import Optional, List
from core.data_provider import DataProvider

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://web.ifzq.gtimg.cn',
}
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
REALTIME_URL = "http://qt.gtimg.cn/q="  # 独立域名，不受web.ifzq WAF影响


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
        self.timeout = timeout
        self.retry = retry

    @property
    def name(self) -> str:
        return 'tencent'

    # ── K线接口 ──────────────────────────────────────────────
    def _fetch_kline(self, code: str, days: int = 500) -> Optional[pd.DataFrame]:
        """从腾讯K线接口获取历史数据（前复权）

        返回 DataFrame: index=date(YYYY-MM-DD), columns=[open,high,low,close,volume]
        """
        tx_code = _to_tencent_code(code)
        params = {'param': f"{tx_code},day,,,{days},qfq"}

        for attempt in range(self.retry):
            try:
                r = requests.get(KLINE_URL, params=params, headers=HEADERS, timeout=self.timeout)

                # WAF拦截 → 直接抛异常，让上层fallback
                if r.status_code != 200 or 'waf.tencent.com' in r.text:
                    raise ConnectionError(f"Tencent K-line WAF blocked (status={r.status_code})")

                data = r.json()
                if data.get('code') != 0:
                    return None

                stock_data = data.get('data', {}).get(tx_code, None)
                if stock_data is None:
                    stock_data = data.get('data', {}).get(tx_code.replace('sh', '').replace('sz', ''), None)
                if stock_data is None:
                    return None

                klines = stock_data.get('qfqday') or stock_data.get('day')
                if not klines:
                    return None

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
                        'volume': int(float(k[5])),
                    })

                df = pd.DataFrame(records)
                if df.empty:
                    return None
                df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
                df = df.set_index('date').sort_index()
                return df

            except ConnectionError:
                raise  # WAF异常直接抛，不重试
            except Exception as e:
                if attempt < self.retry - 1:
                    continue
                raise e

        return None

    # ── 盘中实时行情（独立域名，不受WAF影响） ──────────────────
    def _fetch_minute(self, code: str) -> Optional[pd.DataFrame]:
        """从腾讯实时行情接口获取当天盘中OHLCV

        qt.gtimg.cn 字段（~分隔）：
        [3]=最新价 [5]=今开 [6]=成交量(手) [33]=最高 [34]=最低 [37]=成交额(万)
        [30]=时间戳(如20260825120546)
        """
        tx_code = _to_tencent_code(code)
        try:
            r = requests.get(REALTIME_URL + tx_code, timeout=self.timeout)
            if r.status_code != 200:
                return None

            text = r.text.strip()
            if not text or '~' not in text:
                return None

            fields = text.split('~')
            if len(fields) < 38:
                return None

            def safe_float(idx):
                try:
                    v = float(fields[idx]) if fields[idx] else 0
                    return v if v > 0 else 0
                except (ValueError, IndexError):
                    return 0

            close = safe_float(3)
            open_price = safe_float(5)
            high = safe_float(33)
            low = safe_float(34)
            volume = int(safe_float(6))  # 手
            amount = safe_float(37) * 10000  # 万→元

            if close <= 0 or volume <= 0:
                return None

            # 从时间戳字段[30]提取日期
            raw_date = fields[30][:8] if len(fields[30]) >= 8 else time.strftime('%Y%m%d')
            trade_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"

            row = {
                'open': open_price, 'high': high, 'low': low,
                'close': close, 'volume': volume, 'amount': amount,
            }
            df = pd.DataFrame([row], index=[trade_date])
            df.index.name = 'date'
            return df

        except Exception:
            return None

    # ── 标准接口 ──────────────────────────────────────────────
    def get_daily_kline(
        self,
        codes: List[str],
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """获取日K线数据

        策略：
        1. K线接口获取历史数据（含当天）
        2. 如果K线接口被WAF拦截 → 抛ConnectionError，让ProviderManager fallback
        3. 如果K线有数据但缺少今天（非交易日/数据延迟） → 用分时接口补今天
        """
        from datetime import datetime
        today = datetime.now().strftime('%Y-%m-%d')

        all_records = []
        kline_blocked = False  # K线接口被WAF拦截的标记

        for code in codes:
            try:
                df = pd.DataFrame()

                # 1. K线接口获取历史数据
                if not kline_blocked:
                    try:
                        kline_df = self._fetch_kline(code, days=min(
                            (datetime.strptime(end_date, '%Y-%m-%d') - datetime.strptime(start_date, '%Y-%m-%d')).days + 60,
                            500
                        ))
                        if kline_df is not None and not kline_df.empty:
                            mask = (kline_df.index >= start_date) & (kline_df.index <= end_date)
                            df = kline_df.loc[mask]
                    except ConnectionError:
                        kline_blocked = True  # 后续股票跳过K线，只用分时

                # 2. 分时接口补当天数据（无论K线是否成功，都尝试）
                if end_date >= today:
                    minute_df = self._fetch_minute(code)
                    if minute_df is not None and not minute_df.empty:
                        m_date = minute_df.index[0]
                        if m_date >= start_date and m_date <= end_date:
                            df = pd.concat([df, minute_df])
                            df = df[~df.index.duplicated(keep='last')].sort_index()

                if df.empty:
                    continue

                for date_idx, row in df.iterrows():
                    all_records.append({
                        'date': date_idx,
                        'code': _normalize_code(code),
                        'open': row['open'],
                        'high': row['high'],
                        'low': row['low'],
                        'close': row['close'],
                        'volume': int(row['volume']),
                        'amount': 0,
                        'turnover': None,
                        'tradestatus': 1,
                        'pct_change': None,
                        'is_st': 0,
                    })

            except Exception:
                continue

        # K线被WAF拦截 + 分时也没数据 → 抛错触发fallback
        if kline_blocked and not all_records:
            raise ConnectionError("Tencent K-line WAF blocked and no minute data available")

        if not all_records:
            return pd.DataFrame(columns=['code', 'date'] + [
                'open', 'high', 'low', 'close', 'volume', 'amount',
                'turnover', 'tradestatus', 'pct_change', 'is_st'
            ])

        df_out = pd.DataFrame(all_records)
        df_out = df_out.sort_values(['code', 'date'])
        df_out['pct_change'] = df_out.groupby('code')['close'].pct_change() * 100

        return df_out

    def get_float_shares(self, codes=None, date=None) -> pd.DataFrame:
        return pd.DataFrame(columns=['code', 'float_shares'])

    def get_index_components(self, index_code: str, date=None) -> List[str]:
        raise NotImplementedError("腾讯API不支持指数成分股查询")

    def health_check(self) -> bool:
        """健康检查：用实时行情接口（最稳定）"""
        try:
            df = self._fetch_minute('000001')
            return df is not None and len(df) > 0
        except Exception:
            return False
