#!/usr/bin/env python3
"""
拉取上证/深证/创业板指数历史数据并存入 daily_kline 表。
上证指数: sh000001
深证成指: sz399001
创业板指: sz399006

数据源: 优先腾讯API，失败时降级为baostock
"""
import sys, os, time, requests, re
from datetime import datetime

from core.db import get_conn

INDICES = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
}
START_DATE = "2020-01-01"

def fetch_index_kline(code):
    """从腾讯接口拉取指数日K线"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,{START_DATE},,1000,qfq"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.encoding = "utf-8"
    text = resp.text

    # 提取 JSON 数据
    m = re.search(r'"data":\s*(\{.*\})\s*\}', text, re.DOTALL)
    if not m:
        print(f"解析失败: {text[:200]}")
        return []

    import json
    data = json.loads(text)

    # 提取日K线数据
    klines = None
    stock_data = data.get("data", {}).get(code, {})
    for key in ["qfqday", "day"]:
        if key in stock_data:
            klines = stock_data[key]
            break

    if not klines:
        print(f"未找到K线数据: {list(stock_data.keys())}")
        return []

    records = []
    for k in klines:
        # k = [date, open, close, high, low, volume]
        if len(k) >= 6:
            records.append({
                "code": code,
                "date": k[0],
                "open": float(k[1]),
                "close": float(k[2]),
                "high": float(k[3]),
                "low": float(k[4]),
                "volume": float(k[5]) if k[5] else 0,
            })
    return records


def fetch_index_kline_baostock(code):
    """从baostock拉取指数日K线（腾讯WAF封时的备用方案）"""
    # Convert code format: sh000001 -> sh.000001
    bs_code = code[:2] + '.' + code[2:]
    
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != '0':
            print(f"  baostock login failed: {lg.error_msg}")
            return []
        
        rs = bs.query_history_k_data_plus(bs_code,
            fields='date,open,high,low,close,volume',
            start_date=START_DATE,
            frequency='d')
        
        records = []
        while rs.next():
            row = rs.get_row_data()
            try:
                records.append({
                    "code": code,
                    "date": row[0],
                    "open": float(row[1]) if row[1] else 0,
                    "high": float(row[2]) if row[2] else 0,
                    "low": float(row[3]) if row[3] else 0,
                    "close": float(row[4]) if row[4] else 0,
                    "volume": float(row[5]) if row[5] else 0,
                })
            except (ValueError, IndexError):
                continue
        
        bs.logout()
        return records
    except Exception as e:
        print(f"  baostock error: {e}")
        return []


def save_to_db(records):
    """存入 daily_kline 表"""
    with get_conn() as conn:
        for r in records:
            conn.execute(
                "INSERT OR REPLACE INTO daily_kline(code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
                (r["code"], r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]),
            )

def fetch_all_indices():
    """拉取所有指数数据（优先腾讯，失败降级baostock）"""
    all_records = []
    for code, name in INDICES.items():
        print(f"📈 更新{name}...")
        records = fetch_index_kline(code)
        if not records:
            # Tencent failed, try baostock
            print(f"  ⚠️ 腾讯接口失败，尝试baostock...")
            records = fetch_index_kline_baostock(code)
        if records:
            save_to_db(records)
            all_records.extend(records)
            print(f"  存入 {len(records)} 条{name}数据")
        else:
            print(f"  ❌ {name}更新失败（所有数据源均失败）")
    return all_records

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
    print(f"拉取指数历史数据...")
    t0 = time.time()
    records = fetch_all_indices()
    if not records:
        print("拉取失败")
        return
    print(f"拉取 {len(records)} 条，耗时 {time.time()-t0:.1f}s")

    # 显示最新点数
    points = get_latest_index_points()
    if points:
        print("\n📊 最新指数点数:")
        for name, close in points.items():
            print(f"  {name}: {close:.2f}")

if __name__ == "__main__":
    main()
