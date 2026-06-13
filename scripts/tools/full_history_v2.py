#!/usr/bin/env python3
"""修复 fetch_tencent_kline 的大天数解析问题 + 全量拉取"""
import sys, time, os, shutil, sqlite3
from datetime import datetime

sys.path.insert(0, '/root/a-share-quant-sim')
sys.path.insert(0, '/root/a-share-quant-sim/scripts')
from scripts.update_daily_data import HEADERS, get_stock_list
from core.db import get_conn
import requests, json

DB_PATH = '/root/data/quant.db'

def fetch_full(code):
    """直接用 requests 拉取全量历史（最大天数），绕过 fetch_tencent_kline 的解析问题"""
    if code.startswith('6') or code.startswith('9'):
        tx_code = f"sh{code}"
    else:
        tx_code = f"sz{code}"
    
    url = 'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
    # days=5000 返回不同格式，用 2000 刚好拿满
    params = {'param': f'{tx_code},day,,,2000,qfq'}
    
    r = requests.get(url, params=params, headers=HEADERS, timeout=15)
    data = r.json()
    
    if isinstance(data.get('data'), list):
        # 大天数返回 list 格式
        stock_list = data['data']
        for item in stock_list:
            qfqday = item.get('qfqday') or item.get('day')
            if qfqday:
                tx_code_clean = tx_code.replace('sh', '').replace('sz', '')
                return qfqday, tx_code_clean
        return None, None
    elif isinstance(data.get('data'), dict):
        # 正常 dict 格式
        stock_data = data['data'].get(tx_code) or data['data'].get(code)
        if stock_data is None:
            return None, None
        
        key = 'qfqday' if 'qfqday' in stock_data else 'day'
        return stock_data.get(key), tx_code
    return None, None

print("=" * 60)
print("全量拉取 zz800 历史数据（最大可用天数）")
print("=" * 60)

stocks = get_stock_list()
print(f"股票池: {len(stocks)} 只")

t0 = time.time()
success = 0
fail = 0
total = 0
fail_list = []

for i, code in enumerate(stocks):
    if (i + 1) % 100 == 0:
        elapsed = time.time() - t0
        rate = (i + 1) / elapsed if elapsed > 0 else 0
        eta = (len(stocks) - i - 1) / rate if rate > 0 else 0
        print(f"  [{i+1}/{len(stocks)}] ok={success} fail={fail} records={total} ({rate:.1f}/s, ETA {eta:.0f}s)")
    
    try:
        klines, tx_code = fetch_full(code)
        if klines is None or len(klines) == 0:
            fail += 1
            if fail <= 3:
                fail_list.append(code)
            continue
        
        records = []
        for k in klines:
            if len(k) < 6:
                continue
            date_str = k[0]
            if date_str < '2005-01-01':
                continue
            try:
                records.append((
                    code, date_str,
                    float(k[1]), float(k[3]), float(k[4]), float(k[2]),  # o h l c
                    float(k[5]),  # volume
                    0.0,  # amount placeholder
                ))
            except (ValueError, IndexError):
                continue
        
        if records:
            # 估算 amount = vwap * volume
            for idx, rec in enumerate(records):
                o, h, l, c, v = rec[2], rec[3], rec[4], rec[5], rec[6]
                vwap = (o + c + h + l) / 4
                amount = vwap * v
                records[idx] = (rec[0], rec[1], rec[2], rec[3], rec[4], rec[5], rec[6], amount)
            
            with get_conn() as conn:
                conn.executemany("""
                    INSERT OR REPLACE INTO daily_kline 
                    (code, date, open, high, low, close, volume, amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, records)
                conn.commit()
            total += len(records)
        success += 1
    except Exception as e:
        fail += 1
        if len(fail_list) < 5:
            fail_list.append(f"{code}: {e}")

elapsed = time.time() - t0
print(f"\n{'='*60}")
print(f"全量拉取完成: {elapsed:.1f}s")
print(f"  成功: {success}/{len(stocks)}")
print(f"  失败: {fail}")
print(f"  写入: {total} 条")
if fail_list:
    print(f"  失败示例: {fail_list}")

# 验证
print(f"\n{'='*60}")
print("DB 状态:")
with get_conn() as conn:
    min_d = conn.execute("SELECT MIN(date) FROM daily_kline").fetchone()[0]
    max_d = conn.execute("SELECT MAX(date) FROM daily_kline").fetchone()[0]
    cnt = conn.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
    codes = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_kline").fetchone()[0]
    
    print(f"  范围: {min_d} ~ {max_d}")
    print(f"  记录: {cnt} 条, {codes} 只股票")
    
    print(f"  按年:")
    for year in range(2005, 2027):
        yc = conn.execute("SELECT COUNT(*) FROM daily_kline WHERE date >= ? AND date < ?", 
                          (f'{year}-01-01', f'{year+1}-01-01')).fetchone()[0]
        if yc > 0:
            print(f"    {year}: {yc} 条")
    
    # amount 质量
    stats = conn.execute('''
        SELECT COUNT(*) as total,
               COUNT(CASE WHEN amount/close/volume BETWEEN 0.5 AND 2.0 THEN 1 END) as normal,
               COUNT(CASE WHEN amount/close/volume > 5 THEN 1 END) as high,
               COUNT(CASE WHEN amount/close/volume < 0.1 THEN 1 END) as low
        FROM daily_kline WHERE volume > 0 AND close > 0 AND amount > 0
    ''').fetchone()
    print(f"\n  amount 质量:")
    print(f"    正常 (0.5-2.0): {stats['normal']}/{stats['total']} ({stats['normal']/stats['total']*100:.1f}%)")
    print(f"    异常高 (>5): {stats['high']}")
    print(f"    异常低 (<0.1): {stats['low']}")
    
    # 样本
    samples = conn.execute("SELECT code, date, close, volume, amount FROM daily_kline ORDER BY date ASC LIMIT 3").fetchall()
    print(f"\n  最早数据样本:")
    for r in samples:
        print(f"    {r['code']} {r['date']}: C={r['close']} V={r['volume']} A={r['amount']}")

# 备份 2025 及之前的数据
print(f"\n{'='*60}")
print("备份 2025 及之前的数据...")

# 1. 整库备份
backup_db = '/root/data/quant_backup_2025.db'
shutil.copy2(DB_PATH, backup_db)
print(f"  整库备份: {backup_db}")

# 2. 导出 2025 及之前为 SQL
backup_sql = '/root/data/daily_kline_backup_2025.sql'
pre2026_count = 0
with sqlite3.connect(DB_PATH) as conn:
    rows = conn.execute("SELECT * FROM daily_kline WHERE date <= '2025-12-31' ORDER BY code, date").fetchall()
    pre2026_count = len(rows)
    
    with open(backup_sql, 'w') as f:
        f.write("-- daily_kline 2025及之前数据备份\n")
        f.write(f"-- 导出时间: {datetime.now()}\n")
        f.write(f"-- 记录数: {pre2026_count}\n\n")
        f.write("CREATE TABLE IF NOT EXISTS daily_kline (\n")
        f.write("  code TEXT, date TEXT, open REAL, high REAL, low REAL,\n")
        f.write("  close REAL, volume REAL, amount REAL,\n")
        f.write("  PRIMARY KEY (code, date)\n);\n\n")
        
        for row in rows:
            vals = ','.join([repr(v) for v in row])
            f.write(f"INSERT INTO daily_kline VALUES ({vals});\n")

backup_size = os.path.getsize(backup_sql)
print(f"  SQL 备份: {backup_sql} ({backup_size/1024/1024:.1f} MB, {pre2026_count} 条)")

print(f"\n✅ 全部完成")
