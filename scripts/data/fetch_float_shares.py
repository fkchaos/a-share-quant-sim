#!/usr/bin/env python3
"""
从腾讯财经接口获取全A股流通股本，写入 stock_pool_zz1800.float_shares
流通股本 = 流通市值(亿) * 100000000 / close_price

字段索引（腾讯 qt.gtimg.cn 接口，以 ~ 分隔）：
  [1] = name
  [3] = close
  [44] = circ_mv (流通市值，单位：亿元)
"""
import requests
import sqlite3
import time
import sys
import os

DB_PATH = '/root/a-share-quant-sim/data/quant_stocks.db'

def get_zz1800_codes():
    """获取 zz1800 所有股票代码"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("SELECT code FROM stock_pool_zz1800 WHERE is_active = 1 ORDER BY code")
    codes = [r['code'] for r in c.fetchall()]
    conn.close()
    return codes

def fetch_float_shares(codes, delay=0.05):
    """批量获取流通股本"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    session = requests.Session()
    session.headers.update(headers)
    
    results = {}
    failed = []
    total = len(codes)
    
    for i, code in enumerate(codes):
        # 判断交易所前缀
        if code.startswith('6') or code.startswith('9') or code.startswith('1'):
            prefix = 'sh'
        elif code.startswith('0') or code.startswith('3'):
            prefix = 'sz'
        elif code.startswith('68') or code.startswith('69'):
            prefix = 'sh'
        elif code.startswith('8') or code.startswith('4'):
            prefix = 'bj'
        else:
            prefix = 'sz'
        
        tencent_code = f"{prefix}{code}"
        
        try:
            url = f"http://qt.gtimg.cn/q={tencent_code}"
            r = session.get(url, timeout=5)
            r.encoding = 'gbk'
            text = r.text.strip()
            
            if '~' not in text or len(text) < 100:
                failed.append(code)
                continue
            
            parts = text.split('~')
            if len(parts) < 50:
                failed.append(code)
                continue
            
            name = parts[1]
            close = float(parts[3]) if parts[3] else 0
            circ_mv_yi = float(parts[44]) if parts[44] else 0  # 流通市值（亿元）
            
            if close > 0 and circ_mv_yi > 0:
                # 流通市值(亿) → 元 → 除以 close = 流通股本(股)
                float_shares = int(circ_mv_yi * 100_000_000 / close)
                results[code] = float_shares
            else:
                failed.append(code)
            
            if (i + 1) % 100 == 0 or i == len(codes) - 1:
                print(f"  [{i+1}/{total}] ok={len(results)}, failed={len(failed)}", flush=True)
            
            time.sleep(delay)
            
        except Exception as e:
            failed.append(code)
    
    session.close()
    return results, failed

def update_db(results):
    """更新 stock_pool_zz1800 表的 float_shares"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    updated = 0
    for code, float_shares in results.items():
        c.execute("UPDATE stock_pool_zz1800 SET float_shares = ? WHERE code = ?", (float_shares, code))
        updated += c.rowcount
    
    conn.commit()
    conn.close()
    return updated

if __name__ == '__main__':
    print("=" * 60)
    print("获取 zz1800 流通股本")
    print("=" * 60)
    
    codes = get_zz1800_codes()
    print(f"获取到 {len(codes)} 只股票代码")
    
    if len(codes) == 0:
        print("ERROR: 没有找到股票代码，退出")
        sys.exit(1)
    
    print(f"\n开始获取流通股本...")
    results, failed = fetch_float_shares(codes)
    
    print(f"\n{'=' * 60}")
    print(f"结果: 成功 {len(results)}, 失败 {len(failed)}")
    
    if failed:
        print(f"失败代码: {failed[:20]}{'...' if len(failed) > 20 else ''}")
    
    if results:
        updated = update_db(results)
        print(f"更新数据库: {updated} 行")
        
        # 验证
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM stock_pool_zz1800 WHERE float_shares > 0")
        count = c.fetchone()[0]
        c.execute("SELECT code, name, float_shares FROM stock_pool_zz1800 WHERE float_shares > 0 ORDER BY float_shares DESC LIMIT 10")
        top10 = c.fetchall()
        conn.close()
        
        print(f"\n数据库中 float_shares > 0: {count}/{len(codes)}")
        print(f"流通股本 Top 10:")
        for code, name, fs in top10:
            print(f"  {code} {name}: {fs:,.0f} ({fs/100000000:.1f}亿)")
