"""
指数趋势模块
==============

缓存 HS300/CSI500/CSI1000/SSE50/SZ_COMP/CHINEXT 的日线，
计算 MA20/MA60/MA120 趋势状态，用于日报展示。

使用方法：
  from indices import IndexBenchmarkService, get_index_trends

  trends = get_index_trends("data/cache/indices")
  print(IndexBenchmarkService.format_trends(trends))
"""
from __future__ import annotations

import os
import time
import json
import requests
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime, timedelta


# ──────────────────────────────────────────────
# 指数代码映射（腾讯格式）
# ──────────────────────────────────────────────
INDEX_CODES = {
    "HS300":   "sh000300",
    "CSI500":  "sh000905",
    "CSI1000": "sh000852",
    "SSE50":   "sh000016",
    "SZ_COMP": "sz399001",
    "CHINEXT": "sz399006",
}

INDEX_NAMES = {
    "HS300": "沪深300", "CSI500": "中证500", "CSI1000": "中证1000",
    "SSE50": "上证50", "SZ_COMP": "深证成指", "CHINEXT": "创业板指",
}

TX_KLINE_URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'http://stockapp.finance.qq.com/',
}


def fetch_index_kline(tx_code: str, days: int = 250) -> pd.DataFrame | None:
    """从腾讯接口获取指数日K线（前复权）"""
    params = {'param': f"{tx_code},day,,,{days},qfq"}
    try:
        r = requests.get(TX_KLINE_URL, params=params, headers=HEADERS, timeout=15)
        data = r.json()
        if data.get('code') != 0:
            return None
        stock_data = data.get('data', {}).get(tx_code.replace('sh', '').replace('sz', ''), None)
        if stock_data is None:
            stock_data = data.get('data', {}).get(tx_code, None)
        if stock_data is None:
            return None
        qfq_key = 'qfqday' if 'qfqday' in stock_data else 'day'
        klines = stock_data.get(qfq_key, [])
        if not klines or len(klines) == 0:
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
                'volume': float(k[5]),
            })
        df = pd.DataFrame(records)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        return df
    except Exception:
        return None


@dataclass
class IndexTrend:
    code: str
    name: str = ""
    last_close: float = 0
    ma20: float | None = None
    ma60: float | None = None
    ma120: float | None = None
    pct_5d: float | None = None
    pct_20d: float | None = None
    trend_ma20: str = "unknown"
    trend_ma60: str = "unknown"
    trend_score: int = 0
    last_date: str = ""


def _trend_status(close: float, ma: float | None) -> str:
    if ma is None or pd.isna(ma) or ma <= 0:
        return "unknown"
    pct = (close - ma) / ma
    if abs(pct) < 0.005:
        return "near"
    return "above" if close > ma else "below"


class IndexBenchmarkService:
    """指数趋势缓存与计算服务"""

    def __init__(self, cache_dir: str = "data/cache/indices"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    def _cache_path(self, code: str) -> str:
        return os.path.join(self.cache_dir, f"{code}.csv")

    def _load_cache(self, code: str) -> pd.DataFrame | None:
        path = self._cache_path(code)
        if os.path.exists(path):
            try:
                df = pd.read_csv(path, index_col='date', parse_dates=True)
                if len(df) > 0:
                    return df
            except Exception:
                pass
        return None

    def _save_cache(self, code: str, df: pd.DataFrame):
        df.to_csv(self._cache_path(code))

    def fetch(self, code: str, days: int = 250, force_refresh: bool = False) -> pd.DataFrame | None:
        if not force_refresh:
            cached = self._load_cache(code)
            if cached is not None and len(cached) > 120:
                try:
                    if cached.index[-1] >= pd.Timestamp.now() - timedelta(days=7):
                        return cached
                except Exception:
                    pass
        tx_code = INDEX_CODES.get(code, code)
        df = fetch_index_kline(tx_code, days)
        if df is not None and len(df) > 0 and not df.empty:
            self._save_cache(code, df)
        return df

    def get_trend(self, code: str) -> IndexTrend | None:
        name = INDEX_NAMES.get(code, code)
        df = self.fetch(code)
        if df is None or len(df) < 120:
            return None
        close = df['close'].iloc[-1]
        ma20 = df['close'].rolling(20).mean().iloc[-1]
        ma60 = df['close'].rolling(60).mean().iloc[-1]
        ma120 = df['close'].rolling(120).mean().iloc[-1]
        pct_5d = (close / df['close'].iloc[-5] - 1) if len(df) >= 5 else None
        pct_20d = (close / df['close'].iloc[-20] - 1) if len(df) >= 20 else None
        t20 = _trend_status(close, ma20)
        t60 = _trend_status(close, ma60)
        t120 = _trend_status(close, ma120)
        score = sum(1 for t in [t20, t60, t120] if t == "above")
        return IndexTrend(
            code=code, name=name,
            last_close=round(close, 2),
            ma20=round(ma20, 2) if not pd.isna(ma20) else None,
            ma60=round(ma60, 2) if not pd.isna(ma60) else None,
            ma120=round(ma120, 2) if not pd.isna(ma120) else None,
            pct_5d=round(pct_5d, 4) if pct_5d is not None else None,
            pct_20d=round(pct_20d, 4) if pct_20d is not None else None,
            trend_ma20=t20, trend_ma60=t60,
            trend_score=score,
            last_date=str(df.index[-1].date()),
        )

    def get_all_trends(self) -> list[IndexTrend]:
        trends = []
        for code in INDEX_CODES:
            t = self.get_trend(code)
            if t:
                trends.append(t)
            time.sleep(0.2)
        return trends

    @staticmethod
    def format_trends(trends: list[IndexTrend]) -> str:
        lines = [f"\n  {'指数趋势':=^60}"]
        lines.append(f"  {'指数':<10} {'现价':>8} {'MA20':>8} {'MA60':>8} {'MA120':>8} {'5日':>8} {'20日':>8} {'趋势':>4}")
        lines.append("  " + "-" * 72)
        for t in trends:
            trend_icon = "📈" if t.trend_score >= 2 else ("➡️" if t.trend_score == 1 else "📉")
            pct5 = f"{t.pct_5d:+.1%}" if t.pct_5d else "-"
            pct20 = f"{t.pct_20d:+.1%}" if t.pct_20d else "-"
            ma20_s = f"{t.ma20:.2f}" if t.ma20 else "-"
            ma60_s = f"{t.ma60:.2f}" if t.ma60 else "-"
            ma120_s = f"{t.ma120:.2f}" if t.ma120 else "-"
            lines.append(
                f"  {t.name:<10} {t.last_close:>8.2f} "
                f"{ma20_s:>8} {ma60_s:>8} {ma120_s:>8} "
                f"{pct5:>8} {pct20:>8} {trend_icon}"
            )
        return "\n".join(lines)


def get_index_trends(cache_dir: str = "data/cache/indices") -> list[IndexTrend]:
    """获取所有指数趋势（便捷入口）"""
    svc = IndexBenchmarkService(cache_dir)
    return svc.get_all_trends()


if __name__ == "__main__":
    trends = get_index_trends()
    print(IndexBenchmarkService.format_trends(trends))
