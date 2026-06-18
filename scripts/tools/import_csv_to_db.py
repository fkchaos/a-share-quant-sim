#!/usr/bin/env python3
"""
import_csv_to_db.py — 将 CSV 数据导入 DB，替换东方财富数据
"""
import sys, os, time
import pandas as pd

sys.path.insert(0, os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.environ.get('PROJECT_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))
from core.db import get_conn, init_db

DATA_DIR = os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data"
DAILY_DIR = os.path.join(DATA_DIR, "daily")

def main():
    init_db()
    
    stocks = [f.replace(".csv", "") for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
    print(f"导入 {len(stocks)} 只股票 CSV 数据到 DB...")
    
    t0 = time.time()
    total = 0
    
    for i, code in enumerate(stocks):
        csv_path = os.path.join(DAILY_DIR, f"{code}.csv")
        df = pd.read_csv(csv_path, index_col='date', parse_dates=True).sort_index()
        
        records = []
        for date_idx, row in df.iterrows():
            date_str = str(date_idx)[:10]
            records.append((
                code, date_str,
                float(row.get('open', 0) or 0),
                float(row.get('high', 0) or 0),
                float(row.get('low', 0) or 0),
                float(row.get('close', 0) or 0),
                float(row.get('volume', 0) or 0),
                float(row.get('amount', 0) or 0),
            ))
        
        if records:
            with get_conn() as conn:
                conn.executemany(
                    "INSERT OR REPLACE INTO daily_kline (code,date,open,high,low,close,volume,amount) VALUES (?,?,?,?,?,?,?,?)",
                    records
                )
            total += len(records)
        
        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            print(f"  [{i+1}/{len(stocks)}] {total} 条, {elapsed:.1f}s")
    
    elapsed = time.time() - t0
    print(f"\n完成: {total} 条, {elapsed:.1f}s")
    
    # 统计
    with get_conn() as conn:
        row = conn.execute("SELECT MIN(date), MAX(date), COUNT(*) FROM daily_kline").fetchone()
        print(f"数据范围: {row[0]} ~ {row[1]}, {row[2]} 条")
        nc = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_kline").fetchone()[0]
        print(f"股票数: {nc}")

if __name__ == "__main__":
    main()
