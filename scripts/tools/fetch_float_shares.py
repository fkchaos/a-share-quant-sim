#!/usr/bin/env python3
"""
scripts/tools/fetch_float_shares.py
====================================
从腾讯行情接口获取流通股本，写入 stock_pool 表。

用法:
    python3 scripts/tools/fetch_float_shares.py          # 全量更新
    python3 scripts/tools/fetch_float_shares.py --check  # 只检查缺失的
"""
import sqlite3
import requests
import time
import sys
import argparse

DB_PATH = "data/quant_stocks.db"
BATCH_SIZE = 50  # 腾讯行情接口每批最多50只
SLEEP_INTERVAL = 0.5  # 批次间隔(秒)，避免被封


def fetch_float_shares_batch(codes):
    """批量获取流通股本，返回 {code: float_shares}"""
    results = {}
    syms = []
    for code in codes:
        prefix = "sh" if code.startswith("6") else "sz"
        syms.append(f"{prefix}{code}")

    url = f"http://qt.gtimg.cn/q={','.join(syms)}"
    try:
        resp = requests.get(url, timeout=10)
        resp.encoding = "gbk"
        for line in resp.text.split(";"):
            if "~" not in line:
                continue
            p = line.split("~")
            if len(p) > 57:
                code = p[2]
                try:
                    price = float(p[3]) if p[3] else 0
                    float_market_cap = float(p[57]) if p[57] else 0  # 万元
                    if price > 0 and float_market_cap > 0:
                        float_shares = float_market_cap * 10000 / price  # 股
                        results[code] = round(float_shares)
                except (ValueError, ZeroDivisionError):
                    pass
    except Exception as e:
        print(f"  请求失败: {e}")
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只更新缺失的")
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 确保 stock_pool 有 float_shares 列
    c.execute("PRAGMA table_info(stock_pool)")
    cols = [r[1] for r in c.fetchall()]
    if "float_shares" not in cols:
        c.execute("ALTER TABLE stock_pool ADD COLUMN float_shares INTEGER DEFAULT 0")
        print("已添加 float_shares 列")

    # 获取需要更新的股票列表
    if args.check:
        c.execute("SELECT code FROM stock_pool WHERE float_shares = 0 OR float_shares IS NULL")
    else:
        c.execute("SELECT code FROM stock_pool WHERE is_active = 1")

    codes = [r[0] for r in c.fetchall()]
    total = len(codes)
    print(f"需要更新: {total} 只股票")

    updated = 0
    failed = 0
    for i in range(0, total, BATCH_SIZE):
        batch = codes[i : i + BATCH_SIZE]
        results = fetch_float_shares_batch(batch)
        for code, float_shares in results.items():
            c.execute(
                "UPDATE stock_pool SET float_shares = ? WHERE code = ?",
                (float_shares, code),
            )
            updated += 1
        failed += len(batch) - len(results)
        conn.commit()
        pct = min(i + BATCH_SIZE, total) / total * 100
        print(f"  [{pct:.0f}%] 已更新 {updated}, 失败 {failed}")
        time.sleep(SLEEP_INTERVAL)

    # 统计
    c.execute("SELECT COUNT(*) FROM stock_pool WHERE float_shares > 0")
    have_data = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM stock_pool")
    total_pool = c.fetchone()[0]
    print(f"\n完成: {updated} 更新, {failed} 失败")
    print(f"流通股本覆盖率: {have_data}/{total_pool} ({have_data/total_pool*100:.1f}%)")

    conn.close()


if __name__ == "__main__":
    main()
