#!/usr/bin/env python3
"""备份 2025 年及之前的 daily_kline 数据"""
import sys, os, shutil
from datetime import datetime

from core.db import get_conn

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)), 'data', 'quant.db'))
BACKUP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)), 'data', 'backups'))
os.makedirs(BACKUP_DIR, exist_ok=True)

timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

# 1. 整库备份
backup_db = os.path.join(BACKUP_DIR, f'quant_backup_{timestamp}.db')
shutil.copy2(DB_PATH, backup_db)
sz = os.path.getsize(backup_db) / 1024 / 1024
print(f"整库备份: {backup_db} ({sz:.1f} MB)")

# 2. 2025 及之前数据 SQL 导出
backup_sql = os.path.join(BACKUP_DIR, f'daily_kline_pre2026_{timestamp}.sql')
with get_conn() as conn:
    rows = conn.execute(
        "SELECT * FROM daily_kline WHERE date <= '2025-12-31' ORDER BY code, date"
    ).fetchall()
    
    with open(backup_sql, 'w') as f:
        f.write(f"-- 备份时间: {datetime.now()}\n")
        f.write(f"-- 记录数: {len(rows)}\n")
        f.write(f"-- 范围: 2025-12-31 及之前\n\n")
        f.write("CREATE TABLE IF NOT EXISTS daily_kline (code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL, PRIMARY KEY(code,date));\n\n")
        for r in rows:
            vals = ','.join(repr(v) for v in r)
            f.write(f"INSERT INTO daily_kline VALUES ({vals});\n")

sz = os.path.getsize(backup_sql) / 1024 / 1024
print(f"SQL 备份: {backup_sql} ({sz:.1f} MB, {len(rows)} 条)")

# 3. 统计
with get_conn() as conn:
    total = conn.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
    pre2026 = conn.execute("SELECT COUNT(*) FROM daily_kline WHERE date <= '2025-12-31'").fetchone()[0]
    nc = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_kline").fetchone()[0]
    min_d = conn.execute("SELECT MIN(date) FROM daily_kline").fetchone()[0]
    max_d = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
    
    print(f"\nDB 统计:")
    print(f"  总记录: {total}")
    print(f"  2025及之前: {pre2026}")
    print(f"  股票数: {nc}")
    print(f"  日期范围: {min_d} ~ {max_d}")
    
    print(f"\n  按年:")
    for y in range(2020, 2027):
        yc = conn.execute("SELECT COUNT(*) FROM daily_kline WHERE date>=? AND date<?", (f'{y}-01-01',f'{y+1}-01-01')).fetchone()[0]
        ync = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_kline WHERE date>=? AND date<?", (f'{y}-01-01',f'{y+1}-01-01')).fetchone()[0]
        if yc:
            print(f"    {y}: {yc} 条, {ync} 只")

print(f"\n✅ 备份完成: {BACKUP_DIR}")
