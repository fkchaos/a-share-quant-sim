#!/usr/bin/env python3
"""
update_daily_data_async.py — 并发数据更新（完整版）
策略：不做任何跳过检查，每次运行都强制全量更新。
      从任务调度层控制频率（一天上午+下午两次）。
"""
import os, sys, time, asyncio, argparse
from datetime import datetime, timedelta
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

from update_daily_data import (
    get_stock_list,
    get_local_latest_date,
    fetch_tencent_kline,
)


async def async_update_all():
    """强制并发更新所有股票，不做任何跳过检查"""
    stocks = get_stock_list()
    print(f"📋 本地股票数量: {len(stocks)}")
    print(f"🔄 开始并发更新（强制全量，并发数=30）...")

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

    # ── 写入（读本地 → 合并新数据 → 写回） ──
    t1 = time.time()
    success = 0
    fail = 0

    for code, df, err in results:
        if df is None or len(df) == 0:
            fail += 1
            continue

        csv_file = os.path.join(DAILY_DIR, f"{code}.csv")
        try:
            local_latest = get_local_latest_date(code)
            if local_latest is not None:
                new_data = df[df.index > local_latest]
                if len(new_data) == 0:
                    # 已是最新，touch一下文件更新时间
                    os.utime(csv_file, None)
                    continue
                old_df = pd.read_csv(csv_file, index_col='date', parse_dates=True)
                combined = pd.concat([old_df, new_data])
                combined = combined[~combined.index.duplicated(keep='last')]
                combined = combined.sort_index()
                combined.to_csv(csv_file)
            else:
                df.to_csv(csv_file)
            success += 1
        except Exception:
            fail += 1

    t_write = time.time() - t1
    total = time.time() - t0
    print(f"  写入完成: 新增{success} 失败{fail}, {t_write:.1f}s")
    print(f"  ─────────────────────────────")
    print(f"  总耗时: {total:.1f}s (请求{t_fetch:.1f}s + 写入{t_write:.1f}s)")

    # ── 同步到数据库（增量：只写今天的） ──
    try:
        t2 = time.time()
        from core.db import upsert_kline_batch, upsert_stock, get_latest_date, get_stock_name_map
        name_map = get_stock_name_map()
        today = get_latest_date()
        records = []
        if today:
            for code in stocks:
                csv_file = os.path.join(DAILY_DIR, f"{code}.csv")
                if not os.path.exists(csv_file):
                    continue
                try:
                    df = pd.read_csv(csv_file, index_col='date', parse_dates=True)
                    row = df.loc[df.index == today]
                    if len(row) > 0:
                        r = row.iloc[0]
                        records.append((
                            code, today,
                            float(r.get("open", 0) or 0),
                            float(r.get("high", 0) or 0),
                            float(r.get("low", 0) or 0),
                            float(r.get("close", 0) or 0),
                            float(r.get("volume", 0) or 0),
                            float(r.get("amount", 0) or 0),
                        ))
                except Exception:
                    pass
        if records:
            upsert_kline_batch(records)
            for code in stocks:
                upsert_stock(code, name=name_map.get(code, ""))
            t_db = time.time() - t2
            print(f"  数据库同步: {len(records)} 条K线({today}), {t_db:.1f}s")
    except Exception as e:
        print(f"  数据库同步失败: {e}")

    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="A股日频数据更新（并发强制版）")
    parser.add_argument("--check", action="store_true", help="只检查不更新")
    args = parser.parse_args()

    if args.check:
        from update_daily_data import check_status
        check_status()
    else:
        asyncio.run(async_update_all())
