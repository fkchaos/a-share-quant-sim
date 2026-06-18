#!/usr/bin/env python3
"""全量拉取 zz800 历史 (days=2000) - 串行版，简单可靠"""

import sys, time, os, shutil, sqlite3
from datetime import datetime

sys.path.insert(0, os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.environ.get('PROJECT_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
from scripts.update_daily_data import fetch_tencent_kline, get_stock_list
from core.db import get_conn

DB_PATH = os.path.join(os.environ.get('PROJECT_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'quant.db')

print("=" * 60)
print("全量拉取 zz800 历史 (days=2000)")
print("=" * 60)

stocks = get_stock_list()
print(f"股票池: {len(stocks)} 只\n")

t0 = time.time()
success = fail = total = 0

for i, code in enumerate(stocks):
    if (i + 1) % 50 == 0:
        elapsed = time.time() - t0
        rate = (i + 1) / elapsed
        eta = (len(stocks) - i - 1) / rate
        print(f"[{i+1}/{len(stocks)}] ok={success} fail={fail} rec={total} ({rate:.1f}/s, ETA {eta:.0f}s)")
    
    try:
        df = fetch_tencent_kline(code, days=2000)
        if df is None or len(df) == 0:
            fail += 1
            continue
        
        records = []
        for date_idx, row in df.iterrows():
            date_str = str(date_idx)[:10]
            records.append((
                code, date_str,
                float(row['open']), float(row['high']), float(row['low']), float(row['close']),
                float(row['volume']), float(row['amount'])
            ))
        
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
        if fail <= 3:
            print(f"  ERR {code}: {e}")

t1 = time.time()
print(f"\n{'='*60}")
print(f"拉取完成: {t1-t0:.1f}s")
print(f"  成功: {success}/{len(stocks)}")
print(f"  失败: {fail}")
print(f"  写入: {total} 条")

# DB 统计
print(f"\n{'='*60}")
print("DB 状态:")
with get_conn() as conn:
    min_d = conn.execute("SELECT MIN(date) FROM daily_kline").fetchone()[0]
    max_d = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
    cnt = conn.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
    nc = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_kline").fetchone()[0]
    
    print(f"  范围: {min_d} ~ {max_d}")
    print(f"  记录: {cnt} 条, {nc} 只股票")
    
    print(f"  按年分布:")
    for y in range(int(min_d[:4]) if min_d else 2023, (int(max_d[:4]) if max_d else 2026) + 1):
        yc = conn.execute("SELECT COUNT(*) FROM daily_kline WHERE date >= ? AND date < ?", 
                          (f'{y}-01-01', f'{y+1}-01-01')).fetchone()[0]
        if yc:
            print(f"    {y}: {yc}")

# 备份 2025 及之前
print(f"\n{'='*60}")
print("备份 2025 及之前的数据...")

backup_sql = os.path.join(os.environ.get('PROJECT_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'daily_kline_backup_2025.sql')
with get_conn() as conn:
    rows = conn.execute("SELECT * FROM daily_kline WHERE date <= '2025-12-31' ORDER BY code, date").fetchall()

with open(backup_sql, 'w') as f:
    f.write(f"-- daily_kline 2025及之前数据备份\n")
    f.write(f"-- 导出时间: {datetime.now()}\n")
    f.write(f"-- 记录数: {len(rows)}\n\n")
    f.write("CREATE TABLE IF NOT EXISTS daily_kline (\n")
    f.write("  code TEXT, date TEXT, open REAL, high REAL, low REAL,\n")
    f.write("  close REAL, volume REAL, amount REAL,\n")
    f.write("  PRIMARY KEY (code, date)\n);\n\n")
    for r in rows:
        f.write(f"INSERT INTO daily_kline VALUES ({','.join(repr(v) for v in r)});\n")

sz = os.path.getsize(backup_sql)
print(f"  SQL 备份: {backup_sql} ({sz/1024/1024:.1f} MB, {len(rows)} 条)")

# 整库备份
backup_db = os.path.join(os.environ.get('PROJECT_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'quant_pre_backup.db')
shutil.copy2(DB_PATH, backup_db)
print(f"  整库备份: {backup_db}")
print(f"\n✅ 全部完成")
