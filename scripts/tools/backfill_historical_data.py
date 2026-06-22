#!/usr/bin/env python3
"""
backfill_historical_data.py — 历史K线数据回补 v2
按年分段拉取 2020-01-01 ~ 2023-10-25 的历史数据，写入 SQLite
跳过已有完整数据（>900天），带进度输出
"""
import os, sys, time, asyncio
import requests
import sqlite3

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "quant_stocks.db")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'http://stockapp.finance.qq.com/',
}

SEGMENTS = [
    ('2020-01-01', '2020-12-31'),
    ('2021-01-01', '2021-12-31'),
    ('2022-01-01', '2022-12-31'),
    ('2023-01-01', '2023-10-25'),
]

def get_tx_code(code):
    if code.startswith('6') or code.startswith('9'):
        return f"sh{code}"
    return f"sz{code}"

def fetch_kline_range(code, start, end):
    tx_code = get_tx_code(code)
    url = 'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
    params = {'param': f'{tx_code},day,{start},{end},2000,qfq'}
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=30)
            data = r.json()
            if data.get('code') != 0:
                return []
            stock_data = data.get('data', {}).get(code, None)
            if stock_data is None:
                stock_data = data.get('data', {}).get(tx_code, None)
            if stock_data is None:
                return []
            qfq_key = 'qfqday' if 'qfqday' in stock_data else 'day'
            return stock_data.get(qfq_key, [])
        except Exception:
            if attempt < 2:
                time.sleep(2)
    return []

def kline_to_records(code, klines):
    records = []
    for k in klines:
        if len(k) < 6:
            continue
        date_str = k[0]
        try:
            o, c, h, l, v = float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])
        except (ValueError, IndexError):
            continue
        if c <= 0 or o <= 0 or h < l or c > h or c < l or v <= 0:
            continue
        a = (o + c + h + l) / 4 * v
        records.append((code, date_str, o, h, l, c, v, a))
    return records

def get_existing_count(code):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM daily_kline 
        WHERE code=? AND date BETWEEN '2020-01-01' AND '2023-10-25'
    """, (code,))
    cnt = cur.fetchone()[0]
    conn.close()
    return cnt

def upsert_records(records):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executemany("""
        INSERT OR REPLACE INTO daily_kline (code, date, open, high, low, close, volume, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, records)
    conn.commit()
    conn.close()

def get_stocks_need_backfill():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT code FROM stock_pool WHERE is_active=1 ORDER BY code")
    all_codes = [r[0] for r in cur.fetchall()]
    conn.close()
    
    need = []
    skip = 0
    for code in all_codes:
        cnt = get_existing_count(code)
        if cnt >= 900:
            skip += 1
        else:
            need.append(code)
    return need, skip

async def backfill_one(code, semaphore, counter, total, lock):
    async with semaphore:
        all_klines = []
        for start, end in SEGMENTS:
            klines = fetch_kline_range(code, start, end)
            if klines:
                all_klines.extend(klines)
            await asyncio.sleep(0.02)
        
        if not all_klines:
            async with lock:
                counter[0] += 1
                counter[1] += 1  # fail
                pct = counter[0] / total * 100
                print(f'  [{counter[0]}/{total}] {code} FAIL ({pct:.0f}%)', flush=True)
            return code, 'fail', 0
        
        seen = set()
        unique = []
        for k in all_klines:
            if k[0] not in seen:
                seen.add(k[0])
                unique.append(k)
        unique.sort(key=lambda x: x[0])
        
        records = kline_to_records(code, unique)
        if records:
            upsert_records(records)
        
        async with lock:
            counter[0] += 1
            counter[2] += 1  # ok
            pct = counter[0] / total * 100
            print(f'  [{counter[0]}/{total}] {code} ok {len(records)}条 ({pct:.0f}%)', flush=True)
        
        return code, 'ok', len(records)

async def main():
    need, skip = get_stocks_need_backfill()
    total = len(need)
    print(f"📋 需回补: {total} 只（跳过{skip}只已有完整数据）")
    print(f"🎯 回补区间: 2020-01-01 ~ 2023-10-25")
    print(f"⏳ 开始并发回补（并发数=10）...\n")
    
    t0 = time.time()
    semaphore = asyncio.Semaphore(10)
    lock = asyncio.Lock()
    counter = [0, 0, 0]  # done, fail, ok
    
    tasks = [backfill_one(code, semaphore, counter, total, lock) for code in need]
    results = await asyncio.gather(*tasks)
    
    elapsed = time.time() - t0
    
    ok_count = sum(1 for _, s, _ in results if s == 'ok')
    fail_count = sum(1 for _, s, _ in results if s == 'fail')
    total_records = sum(c for _, s, c in results if s == 'ok')
    
    print(f"\n{'='*50}")
    print(f"✅ 回补完成: {elapsed:.1f}s")
    print(f"  成功: {ok_count} 只, 写入 {total_records} 条K线")
    print(f"  失败: {fail_count} 只")
    
    if fail_count > 0:
        fails = [code for code, s, _ in results if s == 'fail']
        print(f"  失败列表: {fails[:30]}")
    
    print(f"\n📊 回补后数据分布:")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for year in ['2020', '2021', '2022', '2023', '2024', '2025', '2026']:
        cur.execute(f"SELECT COUNT(DISTINCT code) FROM daily_kline WHERE date LIKE '{year}%'")
        cnt = cur.fetchone()[0]
        cur.execute(f"SELECT COUNT(*) FROM daily_kline WHERE date LIKE '{year}%'")
        rows_cnt = cur.fetchone()[0]
        print(f"  {year}: {cnt}只股票, {rows_cnt}条K线")
    conn.close()

if __name__ == "__main__":
    asyncio.run(main())
