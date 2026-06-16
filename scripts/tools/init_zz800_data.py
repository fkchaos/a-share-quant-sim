"""
中证800成分股日线数据初始化
============================
从腾讯接口下载中证800成分股（排除科创板/ST）的日K线数据。
只下载本地不存在的股票（增量更新）。

用法:
  python scripts/init_zz800_data.py          # 下载全部缺失股票
  python scripts/init_zz800_data.py --check  # 只检查缺失数量
"""

import os, sys, time, argparse
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")
os.makedirs(DAILY_DIR, exist_ok=True)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'http://stockapp.finance.qq.com/',
}

# 起始日期
START_DATE = "2020-01-01"  # 多取一年，确保回测起始日(2021-01-01)前有足够数据


def tx_code(code):
    code = str(code).zfill(6)
    if code.startswith('6') or code.startswith('9'):
        return f"sh{code}"
    else:
        return f"sz{code}"


def fetch_tencent_kline(code, start_date=START_DATE):
    """从腾讯接口获取日K线数据（分段获取全部）"""
    tc = tx_code(code)
    url = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    
    # 按年分段获取
    start_year = int(start_date[:4])
    end_year = datetime.now().year
    
    all_records = []
    
    for year in range(start_year, end_year + 1):
        seg_start = f"{year}-01-01"
        seg_end = f"{year}-12-31"
        
        params = {'param': f"{tc},day,{seg_start},{seg_end},500,qfq"}
        
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
            data = r.json()
            
            if data.get('code') != 0:
                continue
            
            outer = data.get('data', {})
            if isinstance(outer, list):
                continue
            
            inner = outer.get(code) or outer.get(tc)
            if inner is None:
                continue
            
            qfq_key = 'qfqday' if 'qfqday' in inner else 'day'
            klines = inner.get(qfq_key, [])
            
            for kline in klines:
                if isinstance(kline, str):
                    parts = kline.split(',')
                elif isinstance(kline, list):
                    parts = kline
                else:
                    continue
                if len(parts) >= 6:
                    all_records.append({
                        'date': parts[0],
                        'open': float(parts[1]),
                        'close': float(parts[2]),
                        'high': float(parts[3]),
                        'low': float(parts[4]),
                        'volume': float(parts[5]),
                        'amount': float(parts[2]) * float(parts[5]),  # 收盘价×成交量（近似成交额）
                    })
        except Exception:
            continue
    
    if not all_records:
        return None
    
    df = pd.DataFrame(all_records)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date')
    df = df.sort_index()
    df = df[~df.index.duplicated(keep='last')]
    return df


def download_stock(code, force=False):
    """下载单只股票数据"""
    csv_path = os.path.join(DAILY_DIR, f"{code}.csv")
    
    if not force and os.path.exists(csv_path):
        return ('exists', code)
    
    df = fetch_tencent_kline(code)
    if df is None or len(df) == 0:
        return ('fail', code)
    
    df.to_csv(csv_path)
    return ('ok', code)


def main():
    parser = argparse.ArgumentParser(description='中证800成分股日线数据初始化')
    parser.add_argument('--check', action='store_true', help='只检查缺失数量')
    parser.add_argument('--workers', type=int, default=4, help='并发线程数')
    parser.add_argument('--force', action='store_true', help='强制重新下载')
    args = parser.parse_args()
    
    # 读取中证800成分股列表
    csv_path = '/root/data/zz800_constituents.csv'
    if not os.path.exists(csv_path):
        print(f"❌ 成分股列表不存在: {csv_path}")
        print("   请先运行生成脚本")
        return
    
    df = pd.read_csv(csv_path, dtype={'code': str})
    codes = df['code'].astype(str).str.zfill(6).tolist()
    print(f"中证800成分股: {len(codes)} 只")
    
    # 检查已下载
    existing = set(f.replace('.csv', '') for f in os.listdir(DAILY_DIR) if f.endswith('.csv'))
    need_download = [c for c in codes if c not in existing]
    
    print(f"已下载: {len(existing)} 只")
    print(f"需下载: {len(need_download)} 只")
    
    if args.check:
        return
    
    if not need_download:
        print("全部已下载，无需更新")
        return
    
    # 下载
    print(f"\n开始下载 ({args.workers} 线程)...")
    t0 = time.time()
    ok = 0
    fail = 0
    exists = 0
    
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(download_stock, code, args.force): code for code in need_download}
        
        for i, future in enumerate(as_completed(futures)):
            status, code = future.result()
            if status == 'ok':
                ok += 1
            elif status == 'fail':
                fail += 1
            elif status == 'exists':
                exists += 1
            
            if (i + 1) % 50 == 0:
                elapsed = time.time() - t0
                speed = (i + 1) / elapsed if elapsed > 0 else 0
                remaining = (len(need_download) - i - 1) / speed if speed > 0 else 0
                print(f"  进度 {i+1}/{len(need_download)} ({ok}成功/{fail}失败), "
                      f"{speed:.1f}只/s, 剩余{remaining:.0f}s")
    
    elapsed = time.time() - t0
    print(f"\n完成: {ok} 成功, {fail} 失败, {exists} 已存在")
    print(f"耗时: {elapsed:.1f}s")
    
    # 最终统计
    total = len([f for f in os.listdir(DAILY_DIR) if f.endswith('.csv')])
    print(f"本地数据: {total} 只")


if __name__ == "__main__":
    main()
