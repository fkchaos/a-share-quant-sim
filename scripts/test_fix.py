#!/usr/bin/env python3
import sys, os
sys.path.insert(0, '/root/a-share-quant-sim')
sys.path.insert(0, '/root/a-share-quant-sim/scripts')

import urllib.request
from core.db import get_conn

# 测试 4 个代码
codes = ['688002', '688008', '688009', '688012']
prefixed = ['sh{}'.format(c) if c.startswith('6') else 'sz{}'.format(c) for c in codes]
url = 'http://qt.gtimg.cn/q={}'.format(','.join(prefixed))
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
resp = urllib.request.urlopen(req, timeout=10)
data = resp.read().decode('gbk')

results = {}
for line in data.split(';'):
    line = line.strip()
    if '~' not in line:
        continue
    parts = line.split('~')
    if len(parts) > 1:
        raw = parts[0]
        code_raw = raw.split('=')[0].split('_')[-1]
        name = parts[1].strip()
        print(f'raw=[{raw}] code_raw=[{code_raw}] name=[{name}]')
        if code_raw and name:
            results[code_raw] = name

print()
print('results:', results)

# 更新
with get_conn() as conn:
    for code, name in results.items():
        cursor = conn.execute('UPDATE stock_pool SET name=? WHERE code=?', (name, code))
        print(f'UPDATE {code} -> [{name}] affected={cursor.rowcount}')

# 验证
with get_conn() as conn:
    for code in codes:
        r = conn.execute('SELECT name FROM stock_pool WHERE code=?', (code,)).fetchone()
        print(f'{code}: [{r["name"]}]')
