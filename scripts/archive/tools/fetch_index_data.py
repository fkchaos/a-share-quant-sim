#!/usr/bin/env python3
"""
拉取上证指数历史数据并存入 daily_kline 表。
上证指数代码: sh000001（腾讯行情格式），存到 DB 时 code='sh000001'

用法:
    PYTHONPATH=/root/a-share-quant-sim python scripts/tools/fetch_index_data.py
"""
import sys, os, time, requests, re
from datetime import datetime

from core.db import get_conn

INDEX_CODE = "sh000001"
INDEX_NAME = "上证指数"
START_DATE = "2020-01-01"

def fetch_index_kline():
    """从腾讯接口拉取上证指数日K线"""
    url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={INDEX_CODE},day,{START_DATE},,1000,qfq"
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
    stock_data = data.get("data", {}).get(INDEX_CODE, {})
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
                "code": INDEX_CODE,
                "date": k[0],
                "open": float(k[1]),
                "close": float(k[2]),
                "high": float(k[3]),
                "low": float(k[4]),
                "volume": float(k[5]) if k[5] else 0,
            })

    return records

def save_to_db(records):
    """存入 daily_kline 表"""
    with get_conn() as conn:
        # 先删除旧数据
        conn.execute("DELETE FROM daily_kline WHERE code=?", (INDEX_CODE,))
        # 插入新数据
        for r in records:
            conn.execute(
                "INSERT OR REPLACE INTO daily_kline(code,date,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?)",
                (r["code"], r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"]),
            )
    print(f"存入 {len(records)} 条上证指数数据")

def main():
    print(f"拉取上证指数({INDEX_CODE}) 历史数据...")
    t0 = time.time()
    records = fetch_index_kline()
    if not records:
        print("拉取失败")
        return
    print(f"拉取 {len(records)} 条，耗时 {time.time()-t0:.1f}s")
    print(f"数据范围: {records[0]['date']} ~ {records[-1]['date']}")
    save_to_db(records)

    # 验证
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) as c FROM daily_kline WHERE code=?", (INDEX_CODE,)).fetchone()
        print(f"DB 中上证指数数据: {row['c']} 条")
        row = conn.execute("SELECT * FROM daily_kline WHERE code=? ORDER BY date DESC LIMIT 3", (INDEX_CODE,)).fetchall()
        for r in row:
            print(f"  {r['date']}: 开{r['open']:.2f} 收{r['close']:.2f} 高{r['high']:.2f} 低{r['low']:.2f}")

if __name__ == "__main__":
    main()
