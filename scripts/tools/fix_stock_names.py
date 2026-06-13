#!/usr/bin/env python3
"""批量补全 stock_pool 中缺失的股票名称"""
import sys, os
sys.path.insert(0, '/root/a-share-quant-sim')
sys.path.insert(0, '/root/a-share-quant-sim/scripts')

import urllib.request, time
from core.db import get_conn

def fetch_names(code_list):
    results = {}
    prefixed = ['sh{}'.format(c) if c.startswith('6') else 'sz{}'.format(c) for c in code_list]
    batch_size = 60
    for i in range(0, len(prefixed), batch_size):
        batch = prefixed[i:i+batch_size]
        url = 'http://qt.gtimg.cn/q={}'.format(','.join(batch))
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            resp = urllib.request.urlopen(req, timeout=10)
            data = resp.read().decode('gbk')
            for line in data.split(';'):
                line = line.strip()
                if '~' not in line:
                    continue
                parts = line.split('~')
                if len(parts) > 1:
                    # 格式: v_sh688002="1~睿创微纳~...
                    # 提取纯数字代码
                    import re
                    m = re.search(r'(\d{6})', parts[0])
                    if m:
                        code_raw = m.group(1)
                        name = parts[1].strip()
                        if name:
                            results[code_raw] = name
        except Exception as e:
            print('  请求失败:', e)
        time.sleep(0.3)
    return results

# ── Main ───────────────────────────────────────────────────
with get_conn() as conn:
    rows = conn.execute('SELECT code FROM stock_pool WHERE name IS NULL OR name=""').fetchall()
    codes = [r['code'] for r in rows]

print(f'名称空缺: {len(codes)} 只')
if not codes:
    print('无需更新')
    exit(0)

names = fetch_names(codes)
print(f'查回名称: {len(names)} 只')

with get_conn() as conn:
    updated = 0
    for code, name in names.items():
        cursor = conn.execute('UPDATE stock_pool SET name=? WHERE code=?', (name, code))
        updated += cursor.rowcount
    print(f'更新成功: {updated} 只')

# 验证
with get_conn() as conn:
    empty = conn.execute('SELECT COUNT(*) FROM stock_pool WHERE name IS NULL OR name=""').fetchone()[0]
    print(f'剩余名称为空: {empty} 只')
    samples = conn.execute(
        'SELECT code, name FROM stock_pool WHERE code IN ("688002","688008","688009","688012")'
    ).fetchall()
    for r in samples:
        print(f'  {r["code"]} {r["name"]}')
