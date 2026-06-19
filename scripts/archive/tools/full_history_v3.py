#!/usr/bin/env python3
"""全量拉取，用 fetch_tencent_kline(days=2000) 最大化历史"""

import sys, time, os, shutil, sqlite3

from scripts.update_daily_data import fetch_tencent_kline, get_stock_list
from core.db import get_conn

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)), 'data', 'quant.db'))

print("全量拉取 zz800 历史 (days=2000) + 备份")
stocks = get_stock_list()
print(f"{len(stocks)} 只")

t0 = time.time()
success = fail = total = 0

for i, code in enumerate(stocks):
    if (i + 1) % 50 == 0:
        print(f"  [{i+1}/{len(stocks)}] ok={success} fail={fail} rec={total} ({time.time()-t0:.0f}s)")
    
    try:
        df = fetch_tencent_kline(code, days=2000)
        if df is None or len(df) == 0:
            fail += 1
            continue
        
        records = []
        for date_idx, row in df.iterrows():
            date_str = str(date_idx)[:10]
            if date_str < '2005-01-01':
                continue
            records.append((code, date_str,
                float(row['open']), float(row['high']), float(row['low']), float(row['close']),
                float(row['volume']), float(row['amount'])))
        
        if records:
            with get_conn() as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO daily_kline (code,date,open,high,low,close,volume,amount) VALUES (?,?,?,?,?,?,?,?)",
                    records)
                conn.commit()
            total += len(records)
        success += 1
    except Exception as e:
        fail += 1
        if fail <= 3: print(f"  ERR {code}: {e}")

t1 = time.time()
print(f"\n拉取完成: {t1-t0:.1f}s, 成功={success}, 失败={fail}, 写入={total}")

# DB 统计
with get_conn() as conn:
    min_d, max_d, cnt, nc = [conn.execute(f"SELECT {'MIN' if i==0 else 'MAX' if i==1 else 'COUNT' if i==2 else 'COUNT(DISTINCT code)'} {'(date)' if i<2 else ''} FROM daily_kline").fetchone()[0] for i in range(4)]
    print(f"DB: {min_d} ~ {max_d}, {cnt} 条, {nc} 只")
    for y in range(min_d[:4], max_d[:4]):
        yc = conn.execute("SELECT COUNT(*) FROM daily_kline WHERE date>=? AND date<?", (f'{y}-01-01',f'{y+1}-01-01')).fetchone()[0]
        if yc: print(f"  {y}: {yc}")

# 备份 2025 及之前
print("\n备份...")
backup_sql = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)), 'data', 'daily_kline_backup_2025.sql'))
rows = []
with get_conn() as conn:
    rows = conn.execute("SELECT * FROM daily_kline WHERE date <= '2025-12-31' ORDER BY code, date").fetchall()

with open(backup_sql, 'w') as f:
    f.write(f"-- 备份时间: {time.ctime()}, 记录数: {len(rows)}\n")
    f.write("CREATE TABLE IF NOT EXISTS daily_kline (code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL, PRIMARY KEY(code,date));\n\n")
    for r in rows:
        f.write(f"INSERT INTO daily_kline VALUES ({','.join(repr(v) for v in r)});\n")

sz = os.path.getsize(backup_sql)
print(f"SQL 备份: {backup_sql} ({sz/1024/1024:.1f}MB, {len(rows)} 条)")
print("✅ 完成")
