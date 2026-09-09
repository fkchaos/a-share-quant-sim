#!/usr/bin/env python3
"""
拉取上证/深证/创业板指数历史数据并存入 daily_kline 表。
上证指数: sh000001
深证成指: sz399001
创业板指: sz399006

数据源: 通过 ProviderManager（primary=腾讯 → backup=baostock）
"""
import sys, os, time

# Add project root to path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from core.provider_manager import ProviderManager
from core.providers.tencent import TencentProvider
from core.providers.baostock import BaoStockProvider
from core.db import get_conn

INDICES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
}
START_DATE = "2020-01-01"


def fetch_all_indices():
    """通过 ProviderManager 拉取所有指数数据（带 fallback）"""
    pm = ProviderManager()
    pm.register('tencent', TencentProvider())
    pm.register('baostock', BaoStockProvider())
    from datetime import datetime
    end_date = datetime.now().strftime('%Y-%m-%d')

    codes = list(INDICES.keys())
    print(f"Fetching indices: {', '.join(codes)}")

    try:
        df = pm.get_daily_kline(codes, START_DATE, end_date, is_index=True)
    except RuntimeError as e:
        print(f"All providers failed: {e}")
        return []

    if df is None or df.empty:
        print("No data returned")
        return []

    # Convert DataFrame to records for DB insert
    records = []
    for _, row in df.iterrows():
        records.append({
            "code": row["code"],
            "date": str(row["date"])[:10],
            "open": float(row.get("open", 0) or 0),
            "high": float(row.get("high", 0) or 0),
            "low": float(row.get("low", 0) or 0),
            "close": float(row.get("close", 0) or 0),
            "volume": float(row.get("volume", 0) or 0),
        })

    # Upsert to daily_kline
    with get_conn() as conn:
        for r in records:
            conn.execute(
                "INSERT OR REPLACE INTO daily_kline(code,date,open,high,low,close,volume) "
                "VALUES(?,?,?,?,?,?,?)",
                (r["code"], r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]),
            )

    # Summary per index
    for code, name in INDICES.items():
        count = sum(1 for r in records if r["code"] == code)
        if count > 0:
            latest = max(r["date"] for r in records if r["code"] == code)
            print(f"  {name} ({code}): {count} records, latest={latest}")
        else:
            print(f"  {name} ({code}): no data")

    return records


def get_latest_index_points():
    """获取所有指数最新点数"""
    result = {}
    with get_conn() as conn:
        for code, name in INDICES.items():
            row = conn.execute(
                "SELECT close FROM daily_kline WHERE code=? ORDER BY date DESC LIMIT 1",
                (code,)
            ).fetchone()
            if row:
                result[name] = row["close"]
    return result


def main():
    print(f"拉取指数历史数据 (via ProviderManager)...")
    t0 = time.time()
    records = fetch_all_indices()
    if not records:
        print("拉取失败")
        return
    print(f"拉取 {len(records)} 条，耗时 {time.time()-t0:.1f}s")

    # 显示最新点数
    points = get_latest_index_points()
    if points:
        print("\n最新指数点数:")
        for name, close in points.items():
            print(f"  {name}: {close:.2f}")


if __name__ == "__main__":
    main()
