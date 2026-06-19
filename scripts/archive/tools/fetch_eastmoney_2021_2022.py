#!/usr/bin/env python3
"""
东方财富补拉：只拉 2021-01-01 ~ 2022-12-31 的缺失数据
"""
import sys, time, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from scripts.update_daily_data import get_stock_list
from core.db import get_conn

START_DATE = '2021-01-01'
END_DATE = '2022-12-31'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://quote.eastmoney.com/',
}

def fetch_one(code):
    secid = f'1.{code}' if code.startswith('6') or code.startswith('9') else f'0.{code}'
    url = 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
    params = {
        'secid': secid,
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61',
        'klt': '101', 'fqt': '1', 'end': '20221231', 'lmt': '5000',
    }
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        klines = r.json().get('data', {}).get('klines', [])
        records = []
        for k in klines:
            f = k.split(',')
            if len(f) < 7: continue
            date_str = f[0]
            if date_str < START_DATE or date_str > END_DATE: continue
            try:
                o, c, h, l, vol_lots = float(f[1]), float(f[2]), float(f[3]), float(f[4]), float(f[5])
                vol_shares = vol_lots * 100
                vwap = (o + c + h + l) / 4
                amt = vwap * vol_shares
                records.append((code, date_str, o, h, l, c, vol_shares, amt))
            except (ValueError, IndexError):
                continue
        return code, records
    except Exception as e:
        return code, []

print("东方财富补拉 2021-2022")
stocks = get_stock_list()
print(f"{len(stocks)} 只, 并发=8")

t0 = time.time()
all_records = []
success_count = fail_count = 0

with ThreadPoolExecutor(max_workers=8) as pool:
    futures = {pool.submit(fetch_one, code): code for code in stocks}
    done = 0
    for future in as_completed(futures):
        done += 1
        code, records = future.result()
        if records:
            all_records.extend(records)
            success_count += 1
        else:
            fail_count += 1
        if done % 100 == 0:
            elapsed = time.time() - t0
            print(f"[{done}/{len(stocks)}] ok={success_count} fail={fail_count} rec={len(all_records)} ({elapsed:.0f}s)")

t1 = time.time()
print(f"\n拉取: {t1-t0:.1f}s, 成功={success_count}, 失败={fail_count}, 记录={len(all_records)}")

if all_records:
    print("写 DB...")
    with get_conn() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_kline (code,date,open,high,low,close,volume,amount) VALUES (?,?,?,?,?,?,?,?)",
            all_records)
        conn.commit()
    print(f"写入 {len(all_records)} 条")

# 验证
print(f"\nDB:")
with get_conn() as conn:
    for y in range(2021, 2023):
        yc = conn.execute("SELECT COUNT(*) FROM daily_kline WHERE date>=? AND date<?", (f'{y}-01-01',f'{y+1}-01-01')).fetchone()[0]
        ync = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_kline WHERE date>=? AND date<?", (f'{y}-01-01',f'{y+1}-01-01')).fetchone()[0]
        print(f"  {y}: {yc} 条, {ync} 只")
    total = conn.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
    print(f"  总计: {total}")

print("\n✅ 完成")
