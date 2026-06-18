#!/usr/bin/env python3
"""
用东方财富接口拉取 zz800 全量历史数据 (2021-01-01 ~ 2026-06-10)
并发版本，带速率控制

东方财富字段：[日期,开盘,收盘,最高,最低,成交量(手),成交额(元),...]
DB 单位：volume=股(×100), amount=vwap×volume(股)
"""
import sys, time, os, shutil, sqlite3, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from scripts.update_daily_data import get_stock_list
from core.db import get_conn

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)), 'data', 'quant.db'))
START_DATE = '2021-01-01'
END_DATE = '2026-06-10'
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
        'klt': '101', 'fqt': '1', 'end': '20260610', 'lmt': '5000',
    }
    
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        klines = r.json().get('data', {}).get('klines', [])
        if not klines:
            return code, []
        
        records = []
        for k in klines:
            f = k.split(',')
            if len(f) < 7: continue
            date_str = f[0]
            if date_str < START_DATE: continue
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

print("东方财富全量历史 (2021-01-01 ~ 2026-06-10)")
stocks = get_stock_list()
print(f"{len(stocks)} 只, 并发=8")

import random
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
            rate = done / elapsed
            eta = (len(stocks) - done) / rate
            print(f"[{done}/{len(stocks)}] ok={success_count} fail={fail_count} rec={len(all_records)} ({rate:.1f}/s ETA {eta:.0f}s)")

t1 = time.time()
print(f"\n拉取: {t1-t0:.1f}s, 成功={success_count}, 失败={fail_count}, 记录={len(all_records)}")

# 写 DB（分批）
print("写 DB...")
BATCH = 50000
with get_conn() as conn:
    for i in range(0, len(all_records), BATCH):
        batch = all_records[i:i+BATCH]
        conn.executemany(
            "INSERT OR REPLACE INTO daily_kline (code,date,open,high,low,close,volume,amount) VALUES (?,?,?,?,?,?,?,?)",
            batch)
        conn.commit()
        if i % 200000 == 0:
            print(f"  {i}/{len(all_records)}")

# 验证
print(f"\n{'='*60}")
print("DB:")
with get_conn() as conn:
    min_d, max_d, cnt, nc = [conn.execute(
        f"SELECT {'MIN(date)' if i==0 else 'MAX(date)' if i==1 else 'COUNT(*)' if i==2 else 'COUNT(DISTINCT code)'} FROM daily_kline"
    ).fetchone()[0] for i in range(4)]
    print(f"  {min_d} ~ {max_d}, {cnt} 条, {nc} 只")
    
    for y in range(int(min_d[:4]), int(max_d[:4])+1):
        yc = conn.execute("SELECT COUNT(*) FROM daily_kline WHERE date>=? AND date<?", (f'{y}-01-01',f'{y+1}-01-01')).fetchone()[0]
        if yc: print(f"  {y}: {yc}")

# 备份
print(f"\n备份 2025 及之前...")
backup_sql = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)), 'data', 'daily_kline_backup_2025.sql'))
with get_conn() as conn:
    rows = conn.execute("SELECT * FROM daily_kline WHERE date <= '2025-12-31' ORDER BY code, date").fetchall()
with open(backup_sql, 'w') as f:
    f.write(f"-- 备份: {datetime.now()}, {len(rows)} 条\n")
    f.write("CREATE TABLE IF NOT EXISTS daily_kline (code TEXT,date TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,amount REAL,PRIMARY KEY(code,date));\n\n")
    for r in rows:
        f.write(f"INSERT INTO daily_kline VALUES ({','.join(repr(v) for v in r)});\n")
sz = os.path.getsize(backup_sql)
print(f"SQL: {backup_sql} ({sz/1024/1024:.1f}MB)")

shutil.copy2(DB_PATH, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)), 'data', 'quant_pre_backup.db')))
print("整库备份完成")
print("\n✅ 完成")
