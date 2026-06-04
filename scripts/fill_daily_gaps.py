"""
补全日 K 线数据 — 下载缺失成分股的日 K 线
==========================================
从腾讯 API 下载 data/daily/ 中缺失的股票日 K 线数据。

用法:
  python scripts/fill_daily_gaps.py          # 下载所有缺失的
  python scripts/fill_daily_gaps.py --check  # 只检查缺口
"""

import os, sys, time, argparse
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(_BASE_DIR, "data"))
DAILY_DIR = os.path.join(DATA_DIR, "daily")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
}

START_DATE = "2021-01-01"


def _tx_code(code):
    code = str(code).zfill(6)
    if code.startswith('6') or code.startswith('9'):
        return f"sh{code}"
    else:
        return f"s{code}"


def fetch_tencent_kline_full(code, start=START_DATE):
    """获取完整日 K 线（2021年至今）"""
    code = str(code).zfill(6)
    tx = _tx_code(code)
    
    # 分批获取（每次最多600天）
    all_klines = []
    end_date = datetime.now()
    current_end = end_date
    batch_days = 500
    
    while current_end >= pd.Timestamp(start):
        batch_start = max(pd.Timestamp(start), current_end - pd.Timedelta(days=batch_days))
        
        url = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        params = {
            'param': f"{tx},day,{batch_start.strftime('%Y-%m-%d')},{current_end.strftime('%Y-%m-%d')},600,qfq"
        }
        
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=15)
            data = r.json()
            if data.get('code') == 0:
                stock_data = data.get('data', {}).get(code) or data.get('data', {}).get(tx)
                if stock_data:
                    qfq_key = 'qfqday' if 'qfqday' in stock_data else 'day'
                    klines = stock_data.get(qfq_key, [])
                    all_klines.extend(klines)
        except:
            pass
        
        current_end = batch_start - pd.Timedelta(days=1)
        time.sleep(0.1)
    
    if not all_klines:
        return None
    
    # 解析
    records = []
    seen_dates = set()
    for kl in all_klines:
        parts = kl.split(',')
        if len(parts) < 6:
            continue
        try:
            date = pd.Timestamp(parts[0])
            if date in seen_dates:
                continue
            seen_dates.add(date)
            records.append({
                'date': date,
                'open': float(parts[1]),
                'close': float(parts[2]),
                'high': float(parts[3]),
                'low': float(parts[4]),
                'volume': float(parts[5]),
                'amount': float(parts[6]) if len(parts) > 6 else float(parts[5]) * float(parts[2]),
                'outstanding_share': np.nan,
                'turnover': np.nan,
            })
        except:
            continue
    
    if not records:
        return None
    
    df = pd.DataFrame(records).set_index('date').sort_index()
    df = df[df.index >= pd.Timestamp(start)]
    return df


def get_missing_codes(target_codes=None):
    """获取 data/daily/ 中缺失的股票代码"""
    existing = set(f.replace('.csv', '') for f in os.listdir(DAILY_DIR) if f.endswith('.csv'))
    
    if target_codes is None:
        # 备选池
        pool_file = os.path.join(DATA_DIR, "cache", "fallback_pool.csv")
        if not os.path.exists(pool_file):
            print("❌ 备选池缓存不存在，请运行 fallback_pool.py")
            return []
        pool = pd.read_csv(pool_file, dtype={'code': str})
        target_codes = set(pool['code'].tolist())
    
    missing = target_codes - existing
    return sorted(missing)


def batch_download(missing_codes, start_from=None):
    """批量下载日 K 线"""
    os.makedirs(DAILY_DIR, exist_ok=True)
    
    total = len(missing_codes)
    success = 0
    fail = 0
    skip = 0
    
    t0 = time.time()
    
    for i, code in enumerate(missing_codes):
        code = str(code).zfill(6)
        
        if start_from and code < start_from:
            skip += 1
            continue
        
        # 已存在就跳过
        csv_path = os.path.join(DAILY_DIR, f"{code}.csv")
        if os.path.exists(csv_path):
            skip += 1
            continue
        
        df = fetch_tencent_kline_full(code)
        if df is not None and len(df) > 0:
            df.to_csv(csv_path)
            success += 1
        else:
            fail += 1
        
        # 进度
        if (i + 1) % 50 == 0 or i == total - 1:
            elapsed = time.time() - t0
            speed = (i + 1) / elapsed if elapsed > 0 else 0
            remaining = (total - i - 1) / speed if speed > 0 else 0
            print(f"  进度 {i+1}/{total} ({speed:.1f}只/s, 剩余~{remaining:.0f}s) | 成功 {success}, 失败 {fail}, 跳过 {skip}")
        
        time.sleep(0.15)  # 避免限流
    
    elapsed = time.time() - t0
    print(f"\n完成: 成功 {success}, 失败 {fail}, 跳过 {skip}")
    print(f"耗时: {elapsed:.1f}s")
    return success, fail


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="补全日 K 线数据缺口")
    parser.add_argument('--check', action='store_true', help="只检查缺口")
    parser.add_argument('--start-from', type=str, help="从指定代码开始")
    args = parser.parse_args()
    
    missing = get_missing_codes()
    print(f"缺失股票: {len(missing)} 只")
    
    if args.check:
        if missing:
            print(f"前20只: {missing[:20]}")
        sys.exit(0)
    
    if not missing:
        print("无缺失，全部已下载")
        sys.exit(0)
    
    print(f"\n开始下载 {len(missing)} 只股票...")
    batch_download(missing, start_from=args.start_from)
