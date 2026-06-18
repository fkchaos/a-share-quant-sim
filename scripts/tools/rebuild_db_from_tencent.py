#!/usr/bin/env python3
"""
rebuild_db_from_tencent.py — 用腾讯 qfq 接口全量重建 DB 数据
覆盖东方财富数据，确保和 CSV 一致

用法:
  python scripts/rebuild_db_from_tencent.py          # 全量重建
  python scripts/rebuild_db_from_tencent.py --check  # 只检查
"""
import sys, time, os
from concurrent.futures import ThreadPoolExecutor, as_completed

from scripts.update_daily_data import fetch_tencent_kline, get_stock_list
from core.db import get_conn, init_db

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)), 'data', 'quant.db'))

print("=" * 60)
print("重建 DB 数据 — 腾讯 qfq 接口")
print("=" * 60)

stocks = get_stock_list()
print(f"股票池: {len(stocks)} 只")

# 先清空 daily_kline 表
init_db()
with get_conn() as conn:
    conn.execute("DELETE FROM daily_kline")
    conn.commit()
    print("✅ 已清空 daily_kline 表")

# 并发拉取 (腾讯接口最大约2000天，超过返回空)
CONCURRENCY = 16
DAYS = 2000
print(f"\n开始并发拉取 (并发数={CONCURRENCY}, days={DAYS})...")

t0 = time.time()
success = fail = total = 0
fail_list = []

def fetch_and_store(code):
    try:
        df = fetch_tencent_kline(code, days=DAYS)
        if df is None or len(df) == 0:
            return code, [], "no data"
        
        records = []
        for date_idx, row in df.iterrows():
            date_str = str(date_idx)[:10]
            records.append((
                code, date_str,
                float(row['open']),
                float(row['high']),
                float(row['low']),
                float(row['close']),
                float(row['volume']),
                float(row['amount']),
            ))
        return code, records, None
    except Exception as e:
        return code, [], str(e)

with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
    futures = {pool.submit(fetch_and_store, code): code for code in stocks}
    done = 0
    for future in as_completed(futures):
        done += 1
        code, records, err = future.result()
        if err:
            fail += 1
            if len(fail_list) < 5:
                fail_list.append(f"{code}: {err}")
        else:
            if records:
                with get_conn() as conn:
                    conn.executemany(
                        "INSERT OR REPLACE INTO daily_kline (code,date,open,high,low,close,volume,amount) VALUES (?,?,?,?,?,?,?,?)",
                        records
                    )
                    conn.commit()
                total += len(records)
            success += 1
        
        if done % 100 == 0 or done == len(stocks):
            elapsed = time.time() - t0
            rate = done / elapsed
            eta = (len(stocks) - done) / rate if rate > 0 else 0
            print(f"  [{done}/{len(stocks)}] ok={success} fail={fail} rec={total} ({rate:.1f}/s, ETA {eta:.0f}s)")
            # 释放内存
            import gc
            gc.collect()

elapsed = time.time() - t0
print(f"\n{'='*60}")
print(f"拉取完成: {elapsed:.1f}s")
print(f"  成功: {success}/{len(stocks)}")
print(f"  失败: {fail}")
print(f"  写入: {total} 条")
if fail_list:
    print(f"  失败示例: {fail_list[:3]}")

# 验证
print(f"\n{'='*60}")
print("DB 状态:")
with get_conn() as conn:
    row = conn.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM daily_kline").fetchone()
    print(f"  范围: {row[0]} ~ {row[1]}")
    print(f"  记录: {row[2]} 条")
    nc = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_kline").fetchone()[0]
    print(f"  股票: {nc} 只")
