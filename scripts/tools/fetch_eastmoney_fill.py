#!/usr/bin/env python3
"""
用东方财富接口拉取 zz800 全量历史数据 (2021-01-01 ~ 2026-06-10)
补全之前未拉完的股票
"""
import sys, time, os, shutil, requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

sys.path.insert(0, '/root/a-share-quant-sim')
sys.path.insert(0, '/root/a-share-quant-sim/scripts')
from scripts.update_daily_data import get_stock_list
from core.db import get_conn

DB_PATH = '/root/data/quant.db'
START_DATE = '2021-01-01'
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

print("东方财富全量补拉 (2021-01-01 ~ 2026-06-10)", flush=True)

# 找出还没拉全的股票：2021 年数据少于 200 条的
with get_conn() as conn:
    # 统计每只股票 2021 年数据条数
    rows = conn.execute("""
        SELECT code, COUNT(*) as cnt FROM daily_kline 
        WHERE date >= '2021-01-01' AND date < '2022-01-01'
        GROUP BY code
    """).fetchall()
    has_2021 = {r[0]: r[1] for r in rows}
    
    all_codes = get_stock_list()
    # 需要补拉的：2021 年数据少于 100 条的
    need_fetch = [c for c in all_codes if has_2021.get(c, 0) < 100]
    
print(f"全部: {len(all_codes)} 只", flush=True)
print(f"需补拉: {len(need_fetch)} 只 (2021年数据<100条)", flush=True)

if not need_fetch:
    print("无需补拉!", flush=True)
    sys.exit(0)

t0 = time.time()
all_records = []
success_count = fail_count = 0

with ThreadPoolExecutor(max_workers=8) as pool:
    futures = {pool.submit(fetch_one, code): code for code in need_fetch}
    done = 0
    for future in as_completed(futures):
        done += 1
        code, records = future.result()
        if records:
            all_records.extend(records)
            success_count += 1
        else:
            fail_count += 1
        if done % 50 == 0 or done == len(need_fetch):
            elapsed = time.time() - t0
            rate = done / elapsed if elapsed > 0 else 0
            eta = (len(need_fetch) - done) / rate if rate > 0 else 0
            print(f"[{done}/{len(need_fetch)}] ok={success_count} fail={fail_count} rec={len(all_records)} ({rate:.1f}/s ETA {eta:.0f}s)", flush=True)

t1 = time.time()
print(f"\n拉取: {t1-t0:.1f}s, 成功={success_count}, 失败={fail_count}, 记录={len(all_records)}", flush=True)

if all_records:
    print("写 DB...", flush=True)
    BATCH = 50000
    with get_conn() as conn:
        for i in range(0, len(all_records), BATCH):
            batch = all_records[i:i+BATCH]
            conn.executemany(
                "INSERT OR REPLACE INTO daily_kline (code,date,open,high,low,close,volume,amount) VALUES (?,?,?,?,?,?,?,?)",
                batch)
            conn.commit()
    print(f"写入 {len(all_records)} 条", flush=True)

# 验证
print(f"\n{'='*60}", flush=True)
print("DB 状态:", flush=True)
with get_conn() as conn:
    min_d, max_d, cnt, nc = [conn.execute(
        f"SELECT {'MIN(date)' if i==0 else 'MAX(date)' if i==1 else 'COUNT(*)' if i==2 else 'COUNT(DISTINCT code)'} FROM daily_kline"
    ).fetchone()[0] for i in range(4)]
    print(f"  {min_d} ~ {max_d}, {cnt} 条, {nc} 只", flush=True)
    
    for y in range(2020, 2027):
        yc = conn.execute("SELECT COUNT(*) FROM daily_kline WHERE date>=? AND date<?", (f'{y}-01-01',f'{y+1}-01-01')).fetchone()[0]
        ync = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_kline WHERE date>=? AND date<?", (f'{y}-01-01',f'{y+1}-01-01')).fetchone()[0]
        if yc: print(f"  {y}: {yc} 条, {ync} 只", flush=True)

print("\n✅ 完成", flush=True)
