import sys, json, requests

sys.path.insert(0, '/root/a-share-quant-sim')
sys.path.insert(0, '/root/a-share-quant-sim/scripts')
from scripts.update_daily_data import HEADERS

url = 'http://web.ifzq.gtimg.cn/appstock/app/fqkline/get'

for days in [500, 1000, 2000, 5000, 10000]:
    params = {'param': f'sz000001,day,,,{days},qfq'}
    r = requests.get(url, params=params, headers=HEADERS, timeout=15)
    data = r.json()
    
    keys = list(data.get('data', {}).keys())
    print(f'\ndays={days}: keys={keys}')
    
    if keys:
        key = keys[0]
        val = data['data'][key]
        if isinstance(val, list):
            print(f'  key={key}, type=list, len={len(val)}')
            if len(val) > 0:
                print(f'  first: {val[0][:1][0] if isinstance(val[0], list) else val[0]}')
                print(f'  last:  {val[-1][:1][0] if isinstance(val[-1], list) else val[-1]}')
        elif isinstance(val, dict):
            for k2 in val:
                v2 = val[k2]
                if isinstance(v2, list) and len(v2) > 0:
                    print(f'  {key}.{k2}: list len={len(v2)}')
                    print(f'    first date: {v2[0][0]}')
                    print(f'    last date:  {v2[-1][0]}')
                elif isinstance(v2, list):
                    print(f'  {key}.{k2}: empty list')
                else:
                    print(f'  {key}.{k2}: type={type(v2).__name__}, value={str(v2)[:100]}')
