#!/usr/bin/env python3
"""多线程拉取全市场PE/PB数据（腾讯API）

第一步：拉实时PE/PB存入DB
第二步：用季度EPS计算历史PE
"""
import sys
sys.path.insert(0, "/root/a-share-quant-sim")

import sqlite3
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
from datetime import datetime


def get_stock_codes():
    """获取股票池"""
    conn = sqlite3.connect('/root/a-share-quant-sim/data/quant_stocks.db')
    codes = [r[0] for r in conn.execute(
        "SELECT code FROM stock_pool_zz1800 WHERE is_active=1"
    ).fetchall()]
    conn.close()
    return codes


def code_to_tencent(code):
    """股票代码转腾讯格式"""
    if code.startswith('6'):
        return f'sh{code}'
    else:
        return f'sz{code}'


def fetch_one(code):
    """拉取单只股票PE/PB"""
    tc = code_to_tencent(code)
    url = f'http://qt.gtimg.cn/q={tc}'
    try:
        r = requests.get(url, timeout=5)
        data = r.text.split('~')
        if len(data) > 46:
            return {
                'code': code,
                'name': data[1],
                'close': float(data[3]) if data[3] else None,
                'pe_ttm': float(data[39]) if data[39] else None,
                'pb': float(data[46]) if data[46] else None,
                'total_mv': float(data[45]) if data[45] else None,
                'circ_mv': float(data[44]) if data[44] else None,
            }
    except Exception as e:
        pass
    return None


def create_table(conn):
    """创建估值表"""
    conn.execute('''CREATE TABLE IF NOT EXISTS valuation_daily (
        code TEXT,
        date TEXT,
        pe_ttm REAL,
        pb REAL,
        total_mv REAL,
        circ_mv REAL,
        PRIMARY KEY (code, date)
    )''')
    conn.commit()


def main():
    print("=" * 60)
    print("多线程拉取全市场PE/PB（腾讯API）")
    print("=" * 60)
    
    codes = get_stock_codes()
    print(f"\n股票池: {len(codes)}只")
    
    # 创建DB
    db_path = '/root/a-share-quant-sim/data/quant_stocks.db'
    conn = sqlite3.connect(db_path)
    create_table(conn)
    
    today = datetime.now().strftime('%Y-%m-%d')
    
    # 多线程拉取
    print(f"\n开始拉取（20线程）...")
    t0 = time.time()
    results = []
    
    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(fetch_one, code): code for code in codes}
        done = 0
        for future in as_completed(futures):
            done += 1
            result = future.result()
            if result and result['pe_ttm'] is not None:
                results.append(result)
            if done % 200 == 0:
                elapsed = time.time() - t0
                print(f"  进度: {done}/{len(codes)} ({done/len(codes)*100:.1f}%) "
                      f"成功: {len(results)} 耗时: {elapsed:.1f}秒")
    
    elapsed = time.time() - t0
    print(f"\n拉取完成: {len(results)}/{len(codes)} 成功, 耗时 {elapsed:.1f}秒")
    
    # 写入DB
    print(f"\n写入数据库...")
    conn.executemany(
        "INSERT OR REPLACE INTO valuation_daily (code, date, pe_ttm, pb, total_mv, circ_mv) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(r['code'], today, r['pe_ttm'], r['pb'], r['total_mv'], r['circ_mv']) for r in results]
    )
    conn.commit()
    conn.close()
    
    print(f"完成! 数据已存入 {db_path} 的 valuation_daily 表")
    
    # 统计
    pe_vals = [r['pe_ttm'] for r in results if r['pe_ttm'] and r['pe_ttm'] > 0]
    pb_vals = [r['pb'] for r in results if r['pb'] and r['pb'] > 0]
    print(f"\n统计:")
    print(f"  PE>0: {len(pe_vals)}只, 中位数: {sorted(pe_vals)[len(pe_vals)//2]:.2f}")
    print(f"  PB>0: {len(pb_vals)}只, 中位数: {sorted(pb_vals)[len(pb_vals)//2]:.2f}")


if __name__ == "__main__":
    main()
