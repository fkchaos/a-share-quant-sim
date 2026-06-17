#!/usr/bin/env python3
"""
update_daily_data_async.py — 并发数据更新
策略：请求到数据后直接 upsert 到 SQLite（天然增量，INSERT OR REPLACE）
      CSV 作为可选备份（--csv 开启），默认不写
      从任务调度层控制频率（一天上午+下午两次）
"""
import os, sys, time, asyncio, argparse
from datetime import datetime, timedelta
import pandas as pd

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
sys.path.insert(0, PROJECT_ROOT)

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

from update_daily_data import (
    get_stock_list,
    fetch_tencent_kline,
)


async def async_update_all(write_csv=False):
    """并发更新所有股票，直接 upsert DB"""
    stocks = get_stock_list()
    print(f"📋 股票数量: {len(stocks)}")
    print(f"🔄 开始并发更新（并发数=30，直接写DB{' + CSV' if write_csv else ''}）...")

    t0 = time.time()

    # ── 并发请求所有股票 ──
    CONCURRENCY = 30
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def fetch_one(code):
        async with semaphore:
            loop = asyncio.get_event_loop()
            try:
                df = await loop.run_in_executor(None, fetch_tencent_kline, code, 10)
                return code, df, None
            except Exception as e:
                return code, None, str(e)

    tasks = [fetch_one(code) for code in stocks]
    results = await asyncio.gather(*tasks)

    t_fetch = time.time() - t0
    ok_fetch = sum(1 for _, df, _ in results if df is not None)
    fail_fetch = sum(1 for _, df, _ in results if df is None)
    print(f"  请求完成: {len(results)} 只, {t_fetch:.1f}s (成功{ok_fetch} 失败{fail_fetch})")

    # ── 直接 upsert DB（天然增量） ──
    t1 = time.time()
    db_success = 0
    db_fail = 0
    csv_success = 0

    from core.db import upsert_kline_batch, upsert_stock, get_stock_name_map
    name_map = get_stock_name_map()

    # 先批量 upsert 所有股票池信息
    for code in stocks:
        upsert_stock(code, name=name_map.get(code, ""))

    # 按股票逐个处理：请求到的数据直接 upsert
    all_records = []
    for code, df, err in results:
        if df is None or len(df) == 0:
            db_fail += 1
            continue
        try:
            for date_idx, row in df.iterrows():
                date_str = str(date_idx)[:10]
                all_records.append((
                    code, date_str,
                    float(row.get("open", 0) or 0),
                    float(row.get("high", 0) or 0),
                    float(row.get("low", 0) or 0),
                    float(row.get("close", 0) or 0),
                    float(row.get("volume", 0) or 0),
                    float(row.get("amount", 0) or 0),
                ))
            db_success += 1
        except Exception:
            db_fail += 1

    if all_records:
        upsert_kline_batch(all_records)

    t_db = time.time() - t1
    print(f"  DB写入: {db_success} 只股票, {len(all_records)} 条K线, {t_db:.1f}s (失败{db_fail})")

    # ── 可选：写 CSV 备份 ──
    if write_csv:
        t2 = time.time()
        os.makedirs(DAILY_DIR, exist_ok=True)
        for code, df, err in results:
            if df is None or len(df) == 0:
                continue
            try:
                csv_file = os.path.join(DAILY_DIR, f"{code}.csv")
                df.to_csv(csv_file)
                csv_success += 1
            except Exception:
                pass
        t_csv = time.time() - t2
        print(f"  CSV备份: {csv_success} 只, {t_csv:.1f}s")

    total = time.time() - t0
    print(f"  ─────────────────────────────")
    print(f"  总耗时: {total:.1f}s")

    # ── 更新上证指数 ──
    try:
        from fetch_index_data import fetch_index_kline, save_to_db
        print(f"\n📈 更新上证指数...")
        idx_records = fetch_index_kline()
        if idx_records:
            save_to_db(idx_records)
    except Exception as e:
        print(f"  ⚠️ 上证指数更新失败: {e}")

    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A股日频数据更新（并发直写DB）")
    parser.add_argument("--csv", action="store_true", help="同时写CSV备份")
    parser.add_argument("--check", action="store_true", help="只检查不更新")
    args = parser.parse_args()

    if args.check:
        from update_daily_data import check_status
        check_status()
    else:
        asyncio.run(async_update_all(write_csv=args.csv))
