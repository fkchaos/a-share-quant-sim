#!/usr/bin/env python3
"""
scripts/tools/fetch_etf_kline.py — 拉取行业ETF K线数据
=====================================================
从腾讯行情接口拉取23只行业ETF的日K线，写入 index_kline 表。

腾讯行情接口格式：
  http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh512880,day,2019-01-01,2026-06-24,320,qfq

返回 JSONP 格式，内含.day/open/close/low/high/volume 数组。
"""
import os
import sys
import time
import json
import sqlite3
import urllib.request
import urllib.parse

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "quant_stocks.db")

# ETF 列表
ETF_LIST = [
    ("sz512480", "半导体ETF", "科技"),
    ("sz512500", "5GETF", "科技"),
    ("sz512760", "科创50ETF", "科技"),
    ("sz512660", "军工ETF", "军工"),
    ("sz512810", "军工行业ETF", "军工"),
    ("sz512510", "芯片ETF", "科技"),
    ("sz512590", "光伏ETF", "新能源"),
    ("sz516160", "新能源ETF", "新能源"),
    ("sz516880", "光伏ETF2", "新能源"),
    ("sz512030", "医药ETF", "医药"),  # 医药ETF（正确代码）
    ("sz515030", "新能源车ETF", "新能源"),
    ("sz512100", "有色ETF", "周期"),
    ("sz512200", "化工ETF", "周期"),
    ("sz512300", "保险ETF", "金融"),
    ("sz512690", "证券ETF", "金融"),
    ("sz513130", "银行ETF", "金融"),
    ("sz512800", "地产ETF", "地产"),
    ("sz512010", "食品饮料ETF", "消费"),
    ("sz512260", "电子ETF", "科技"),
    ("sz513030", "医美ETF", "消费"),
    ("sh512880", "红利ETF", "策略"),  # 只有这个用 sh 前缀
    ("sz512020", "家电ETF", "消费"),  # 补充：家电
    ("sz512640", "游戏ETF", "科技"),  # 补充：游戏
]

START_DATE = "2018-01-01"
END_DATE = "2026-06-24"
KLINE_TYPE = "day"
DAYS = "360"  # 腾讯接口限制：单次最多返回约360条
QTYPE = "qfq"   # 前复权


def fetch_kline_range(code, start, end):
    """从腾讯行情接口拉取一段K线数据"""
    param = f"{code},{KLINE_TYPE},{start},{end},{DAYS},{QTYPE}"
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={param}"
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")
    req.add_header("Referer", "http://stock.qq.com/")

    try:
        resp = urllib.request.urlopen(req, timeout=15)
        raw = resp.read().decode("utf-8")
        data = json.loads(raw)

        d = data.get("data", {})
        if isinstance(d, dict):
            if code not in d:
                return []
            kline_data = d[code]
        elif isinstance(d, list):
            if len(d) == 0 or not isinstance(d[0], dict) or code not in d[0]:
                return []
            kline_data = d[0][code]
        else:
            return []

        if "day" in kline_data:
            rows = kline_data["day"]
        elif "qfqday" in kline_data:
            rows = kline_data["qfqday"]
        else:
            return []

        result = []
        for row in rows:
            # 腾讯行情格式: [date, open, close, high, low, volume]
            date_str = row[0]
            open_p = float(row[1])
            close_p = float(row[2])
            high_p = float(row[3])
            low_p = float(row[4])
            vol = float(row[5]) if len(row) > 5 else 0
            amount = 0
            result.append((code, date_str, open_p, high_p, low_p, close_p, vol, amount))
        return result
    except Exception as e:
        print(f"  ✘ {code} range {start}~{end}: {e}")
        return []


def fetch_kline(code, market):
    """分段拉取K线数据（腾讯接口单次最多~360条）"""
    from datetime import datetime, timedelta
    all_rows = []
    current = datetime.strptime(START_DATE, "%Y-%m-%d")
    final = datetime.strptime(END_DATE, "%Y-%m-%d")
    chunk = 300  # 每次拉300天（安全值）

    while current < final:
        next_date = min(current + timedelta(days=chunk), final)
        start_str = current.strftime("%Y-%m-%d")
        end_str = next_date.strftime("%Y-%m-%d")
        rows = fetch_kline_range(code, start_str, end_str)
        if rows:
            all_rows.extend(rows)
            print(f"    {start_str} ~ {end_str}: {len(rows)} rows")
        time.sleep(0.2)
        current = next_date + timedelta(days=2)  # +2 避免与下一段date重叠

    # 去重（按 code + date）
    seen = {}
    for row in all_rows:
        seen[(row[0], row[1])] = row  # key = (code, date)
    return sorted(seen.values(), key=lambda r: (r[0], r[1]))


def save_to_db(rows):
    """写入 index_kline 表"""
    if not rows:
        return 0
    conn = sqlite3.connect(_DB_PATH)
    cursor = conn.cursor()
    # 删除全部旧数据（index_kline 只保留最新数据）
    cursor.execute("DELETE FROM index_kline")
    # 插入新数据
    cursor.executemany(
        "INSERT INTO index_kline (code, date, open, high, low, close, volume, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return len(rows)


def main():
    print(f"拉取 {len(ETF_LIST)} 只行业ETF的K线数据")
    print(f"  区间: {START_DATE} ~ {END_DATE}")
    print(f"  数据库: {_DB_PATH}")
    print()

    all_rows = []
    success = 0
    failed = 0

    for i, (code, name, sector) in enumerate(ETF_LIST, 1):
        market = "sh" if code.startswith("sh") else "sz"
        print(f"[{i:02d}] {code} ({name}, {sector}) ...", end=" ", flush=True)
        rows = fetch_kline(code, market)
        if rows:
            print(f"✓ {len(rows)} rows")
            all_rows.extend(rows)
            success += 1
        else:
            print("✘")
            failed += 1
        time.sleep(0.3)  # 限速

    print(f"\n拉取完成: 成功 {success}, 失败 {failed}, 共 {len(all_rows)} rows")

    if all_rows:
        n = save_to_db(all_rows)
        print(f"写入数据库: {n} rows")

        # 验证
        conn = sqlite3.connect(_DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT code, COUNT(*) FROM index_kline GROUP BY code")
        print("\n当前 index_kline 数据量:")
        for code, cnt in cursor.fetchall():
            print(f"  {code}: {cnt} rows")
        conn.close()


if __name__ == "__main__":
    main()
