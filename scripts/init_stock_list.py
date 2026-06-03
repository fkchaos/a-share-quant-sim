"""
全 A 股股票列表获取与数据初始化
================================
从东方财富/腾讯 API 获取全 A 股股票列表，初始化日 K 数据

用法:
  python scripts/init_stock_list.py          # 获取全 A 股列表并初始化数据
  python scripts/init_stock_list.py --check  # 只检查当前数据状态
  python scripts/init_stock_list.py --update # 仅更新已有股票数据

数据源优先级:
  1. 东方财富 API (股票列表 + 日 K)
  2. 腾讯 API (备用)
"""

import os, sys, time, argparse
import pandas as pd
import numpy as np
import requests
from datetime import datetime, timedelta

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(_BASE_DIR, "data"))
DAILY_DIR = os.path.join(DATA_DIR, "daily")

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'http://quote.eastmoney.com/',
}

# ============================================================
# 数据源 1: 东方财富 API
# ============================================================

def fetch_eastmoney_stock_list():
    """
    从东方财富获取全 A 股股票列表
    返回: DataFrame with columns [code, name, market]
    """
    stocks = []
    
    # 沪市主板 (600/601/603/605)
    stocks.extend(_fetch_eastmoney_market(prefix='sh', market='沪市主板'))
    time.sleep(1)
    
    # 深市主板 (000/001/002/003)
    stocks.extend(_fetch_eastmoney_market(prefix='sz', market='深市主板'))
    time.sleep(1)
    
    # 创业板 (300/301/302)
    stocks.extend(_fetch_eastmoney_market(prefix='cy', market='创业板'))
    time.sleep(1)
    
    df = pd.DataFrame(stocks, columns=['code', 'name', 'market'])
    df = df.drop_duplicates(subset=['code'])
    return df


def _fetch_eastmoney_market(prefix='sh', market='沪市'):
    """从东方财富获取单个市场的股票列表"""
    url = "http://push2.eastmoney.com/api/qt/clist/get"
    
    # 市场映射
    market_map = {
        'sh': {'fs': 'm:1+t:2,m:1+t:23', 'market_id': '1'},   # 沪市 A 股
        'sz': {'fs': 'm:0+t:6,m:0+t:80', 'market_id': '0'},   # 深市 A 股
        'cy': {'fs': 'm:0+t:80', 'market_id': '0'},            # 创业板
        'kc': {'fs': 'm:1+t:23', 'market_id': '1'},            # 科创板
    }
    
    params = {
        'pn': 1,           # 页码
        'pz': 5000,        # 每页数量
        'po': 1,           # 排序方向
        'np': 1,
        'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
        'fltt': 2,
        'invt': 2,
        'fid': 'f3',
        'fs': market_map.get(prefix, {}).get('fs', 'm:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23'),
        'fields': 'f2,f3,f12,f14',  # 最新涨跌幅, 代码, 名称
        '_': int(time.time() * 1000),
    }
    
    stocks = []
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        data = r.json()
        
        if data.get('data') and data['data'].get('diff'):
            for item in data['data']['diff']:
                code = item.get('f12', '')
                name = item.get('f14', '')
                if code and name:
                    stocks.append({'code': code, 'name': name, 'market': market})
        
        print(f"  东方财富 {market}: 获取 {len(stocks)} 只")
    except Exception as e:
        print(f"  东方财富 {market} 请求失败: {e}")
    
    return stocks


def fetch_eastmoney_kline(code, days=30):
    """
    从东方财富获取单只股票日 K 线
    返回: DataFrame with columns [date, open, high, low, close, volume, amount]
    """
    # 判断市场
    if code.startswith('6'):
        secid = f"1.{code}"  # 沪市
    else:
        secid = f"0.{code}"  # 深市/创业板
    
    url = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        'secid': secid,
        'fields1': 'f1,f2,f3,f4,f5,f6',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57',
        'klt': 101,        # 日 K
        'fqt': 1,          # 前复权
        'end': datetime.now().strftime('%Y%m%d'),
        'lmt': days,
        'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
    }
    
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        data = r.json()
        
        if not data.get('data') or not data['data'].get('klines'):
            return None
        
        records = []
        for kline in data['data']['klines']:
            parts = kline.split(',')
            if len(parts) >= 7:
                records.append({
                    'date': pd.to_datetime(parts[0]),
                    'open': float(parts[1]),
                    'close': float(parts[2]),
                    'high': float(parts[3]),
                    'low': float(parts[4]),
                    'volume': float(parts[5]),
                    'amount': float(parts[6]),
                })
        
        if not records:
            return None
        
        df = pd.DataFrame(records)
        df = df.set_index('date')
        df = df.sort_index()
        
        # 添加其他必要列
        df['outstanding_share'] = np.nan
        df['turnover'] = np.nan
        
        return df
    
    except Exception as e:
        raise e


# ============================================================
# 数据源 2: 腾讯 API (备用)
# ============================================================

def fetch_tencent_kline(code, days=30):
    """
    从腾讯行情接口获取前复权日 K 线数据
    返回: DataFrame with columns [date, open, high, low, close, volume, amount]
    """
    # 判断市场前缀
    if code.startswith('6') or code.startswith('9'):
        tx_code = f"sh{code}"
    elif code.startswith('0') or code.startswith('3') or code.startswith('2'):
        tx_code = f"sz{code}"
    else:
        tx_code = f"sz{code}"
    
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {
        'param': f"{tx_code},day,,,{days},qfq"
    }
    
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        data = r.json()
        
        if data.get('code') != 0:
            return None
        
        stock_data = data.get('data', {}).get(tx_code.replace('sh', '').replace('sz', ''), None)
        if stock_data is None:
            stock_data = data.get('data', {}).get(tx_code, None)
        
        if stock_data is None:
            return None
        
        qfq_key = 'qfqday'
        if qfq_key not in stock_data:
            if 'day' in stock_data:
                qfq_key = 'day'
            else:
                return None
        
        klines = stock_data[qfq_key]
        if not klines or len(klines) == 0:
            return None
        
        records = []
        for k in klines:
            if len(k) < 6:
                continue
            records.append({
                'date': pd.to_datetime(k[0]),
                'open': float(k[1]),
                'close': float(k[2]),
                'high': float(k[3]),
                'low': float(k[4]),
                'volume': float(k[5]),
                'amount': 0,
            })
        
        if not records:
            return None
        
        df = pd.DataFrame(records)
        df = df.set_index('date')
        df = df.sort_index()
        
        # 估算成交额
        if df['amount'].eq(0).all():
            vwap = (df['open'] + df['close'] + df['high'] + df['low']) / 4
            df['amount'] = vwap * df['volume'] * 100
        
        df['outstanding_share'] = np.nan
        df['turnover'] = np.nan
        
        return df
    
    except Exception as e:
        raise e


# ============================================================
# 数据源 3: AKShare (备用，可能受限)
# ============================================================

def fetch_akshare_kline(code, days=30):
    """
    从 AKShare 获取单只股票日 K 线
    返回: DataFrame
    """
    try:
        import akshare as ak
        
        # 格式化代码
        if code.startswith('6'):
            symbol = f"{code}"
        else:
            symbol = f"{code}"
        
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=days*2)).strftime('%Y%m%d')
        
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                 start_date=start_date, end_date=end_date,
                                 adjust="qfq")
        
        if df is None or len(df) == 0:
            return None
        
        # 标准化列名
        df = df.rename(columns={
            '日期': 'date',
            '开盘': 'open',
            '收盘': 'close',
            '最高': 'high',
            '最低': 'low',
            '成交量': 'volume',
            '成交额': 'amount',
        })
        
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')
        df = df.sort_index()
        
        df['outstanding_share'] = np.nan
        df['turnover'] = np.nan
        
        return df
    
    except ImportError:
        return None
    except Exception as e:
        raise e


# ============================================================
# 统一数据获取接口（多源 fallback）
# ============================================================

def fetch_kline(code, days=30, source='auto'):
    """
    获取单只股票日 K 线，支持多数据源自动切换
    
    Parameters
    ----------
    code : str - 股票代码
    days : int - 获取天数
    source : str - 数据源选择 ('auto' | 'eastmoney' | 'tencent' | 'akshare')
    
    Returns
    -------
    DataFrame or None
    """
    sources = {
        'eastmoney': fetch_eastmoney_kline,
        'tencent': fetch_tencent_kline,
        'akshare': fetch_akshare_kline,
    }
    
    if source == 'auto':
        # 按优先级尝试
        for src_name in ['eastmoney', 'tencent', 'akshare']:
            try:
                df = sources[src_name](code, days=days)
                if df is not None and len(df) > 0:
                    return df
            except Exception:
                continue
        return None
    else:
        func = sources.get(source)
        if func:
            return func(code, days=days)
        return None


def get_stock_list_online():
    """
    在线获取全 A 股股票列表（非本地 CSV）
    优先从东方财富获取，失败则用本地列表
    """
    try:
        df = fetch_eastmoney_stock_list()
        if len(df) > 0:
            return df
    except Exception as e:
        print(f"  在线获取股票列表失败: {e}")
    
    # fallback: 从本地获取
    return None


# ============================================================
# 数据初始化与更新
# ============================================================

def initialize_stock_data(codes, days=365, source='auto'):
    """
    批量初始化股票数据
    
    Parameters
    ----------
    codes : list[str] - 股票代码列表
    days : int - 初始化天数（默认 1 年）
    source : str - 数据源
    
    Returns
    -------
    dict - {success: int, fail: int, skip: int}
    """
    os.makedirs(DAILY_DIR, exist_ok=True)
    
    success = 0
    fail = 0
    skip = 0
    fail_list = []
    
    for i, code in enumerate(codes):
        csv_file = os.path.join(DAILY_DIR, f"{code}.csv")
        
        # 检查是否已有数据
        if os.path.exists(csv_file):
            try:
                existing = pd.read_csv(csv_file, index_col='date', parse_dates=True)
                if len(existing) > 0:
                    latest = existing.index[-1]
                    if latest >= pd.Timestamp(datetime.now().date() - timedelta(days=3)):
                        skip += 1
                        continue
            except:
                pass
        
        try:
            df = fetch_kline(code, days=days, source=source)
            if df is not None and len(df) > 0:
                df.to_csv(csv_file)
                success += 1
            else:
                fail += 1
                fail_list.append(code)
        except Exception as e:
            fail += 1
            fail_list.append(code)
            if fail <= 5:
                print(f"  ❌ {code}: {e}")
        
        if (i + 1) % 50 == 0:
            print(f"  进度: {i+1}/{len(codes)} ✅{success} ❌{fail} ⏭️{skip}")
        
        time.sleep(0.1)  # 限速
    
    # 重试失败
    if fail_list:
        print(f"\n🔁 重试 {len(fail_list)} 只失败的股票...")
        time.sleep(3)
        retry_fail = []
        for code in fail_list:
            try:
                df = fetch_kline(code, days=days, source='tencent' if source == 'eastmoney' else 'eastmoney')
                if df is not None and len(df) > 0:
                    df.to_csv(os.path.join(DAILY_DIR, f"{code}.csv"))
                    success += 1
                    fail -= 1
                else:
                    retry_fail.append(code)
            except:
                retry_fail.append(code)
            time.sleep(0.2)
        fail_list = retry_fail
    
    return {'success': success, 'fail': fail, 'skip': skip, 'fail_list': fail_list}


def filter_stock_list(df, min_listed_days=120):
    """
    过滤股票列表，排除:
    - 退市股票
    - 长期停牌股票
    - 上市不足 min_listed_days 天的次新股
    - 价格异常（< 1 元或 > 200 元）
    - ST/*ST 股票
    
    Parameters
    ----------
    df : DataFrame - 股票列表，包含 code 和 name 列
    min_listed_days : int - 最少上市天数
    
    Returns
    -------
    DataFrame - 过滤后的股票列表
    """
    filtered = []
    excluded = {'退市': 0, 'ST': 0, '停牌': 0, '次新': 0, '价格异常': 0}
    
    for _, row in df.iterrows():
        code = row['code']
        name = row.get('name', '')
        
        # 排除 ST/*ST
        if 'ST' in name or '*' in name:
            excluded['ST'] += 1
            continue
        
        # 排除退市（名称含"退"）
        if '退' in name:
            excluded['退市'] += 1
            continue
        
        # 检查数据可用性
        csv_file = os.path.join(DAILY_DIR, f"{code}.csv")
        if os.path.exists(csv_file):
            try:
                data = pd.read_csv(csv_file, index_col='date', parse_dates=True, nrows=5)
                if len(data) > 0:
                    # 检查最新日期
                    latest = data.index[-1]
                    if (datetime.now() - latest).days > 30:
                        excluded['停牌'] += 1
                        continue
                    # 检查价格
                    price = data['close'].iloc[-1]
                    if price < 1 or price > 200:
                        excluded['价格异常'] += 1
                        continue
                    # 检查上市天数（有数据的天数）
                    if len(data) < min_listed_days:
                        excluded['次新'] += 1
                        continue
            except:
                pass
        
        filtered.append(row)
    
    result = pd.DataFrame(filtered, columns=df.columns)
    print(f"\n  过滤结果: 原{len(df)} → 保留{len(result)} 只")
    for reason, count in excluded.items():
        if count > 0:
            print(f"    - {reason}: {count} 只")
    
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='全 A 股数据初始化')
    parser.add_argument('--check', action='store_true', help='只检查当前数据状态')
    parser.add_argument('--update', action='store_true', help='仅更新已有股票数据')
    parser.add_argument('--init-days', type=int, default=365, help='初始化天数（默认365天）')
    parser.add_argument('--source', choices=['auto', 'eastmoney', 'tencent', 'akshare'], 
                        default='auto', help='数据源选择')
    parser.add_argument('--max-stocks', type=int, default=3000, help='最大股票数量（默认3000）')
    args = parser.parse_args()
    
    os.makedirs(DAILY_DIR, exist_ok=True)
    
    if args.check:
        # 仅检查状态
        files = [f for f in os.listdir(DAILY_DIR) if f.endswith('.csv')]
        print(f"📊 当前数据状态: {len(files)} 只股票")
        
        # 检查最新日期
        latest_dates = {}
        for f in files[:100]:  # 抽样检查
            code = f.replace('.csv', '')
            csv_file = os.path.join(DAILY_DIR, f)
            try:
                df = pd.read_csv(csv_file, index_col='date', parse_dates=True, nrows=1)
                if len(df) > 0:
                    latest_dates[code] = df.index[-1]
            except:
                pass
        
        if latest_dates:
            newest = max(latest_dates.values())
            oldest = min(latest_dates.values())
            today = datetime.now().date()
            days_behind = (today - newest.date()).days
            print(f"  最新日期: {newest.date()}")
            print(f"  最旧日期: {oldest.date()}")
            print(f"  数据滞后: {days_behind} 天")
        sys.exit(0)
    
    if args.update:
        # 仅更新已有数据
        files = [f for f in os.listdir(DAILY_DIR) if f.endswith('.csv')]
        codes = [f.replace('.csv', '') for f in files]
        print(f"🔄 更新 {len(codes)} 只股票数据...")
        result = initialize_stock_data(codes, days=10, source=args.source)
        print(f"\n📊 更新结果: ✅{result['success']} ❌{result['fail']} ⏭️{result['skip']}")
        sys.exit(0)
    
    # 完整初始化流程
    print("=" * 60)
    print(f"全 A 股数据初始化 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    
    # Step 1: 获取股票列表
    print("\n📋 Step 1: 获取全 A 股股票列表...")
    stock_df = get_stock_list_online()
    
    if stock_df is None or len(stock_df) == 0:
        print("  ❌ 在线获取失败，使用本地列表")
        files = [f for f in os.listdir(DAILY_DIR) if f.endswith('.csv')]
        codes = [f.replace('.csv', '') for f in files]
        stock_df = pd.DataFrame({'code': codes, 'name': codes, 'market': '未知'})
    
    print(f"  获取到 {len(stock_df)} 只股票")
    
    # Step 2: 过滤
    print(f"\n🔍 Step 2: 过滤股票（排除 ST/退市/停牌/次新/价格异常）...")
    filtered_df = filter_stock_list(stock_df, min_listed_days=120)
    
    # 限制最大数量
    if len(filtered_df) > args.max_stocks:
        print(f"  限制到 {args.max_stocks} 只（从 {len(filtered_df)} 只中随机选择）")
        filtered_df = filtered_df.sample(n=args.max_stocks, random_state=42)
    
    codes = filtered_df['code'].tolist()
    print(f"  最终选股范围: {len(codes)} 只")
    
    # Step 3: 初始化数据
    print(f"\n💾 Step 3: 初始化日 K 数据（{args.init_days} 天）...")
    result = initialize_stock_data(codes, days=args.init_days, source=args.source)
    
    print(f"\n📊 初始化结果:")
    print(f"  成功: {result['success']} 只")
    print(f"  失败: {result['fail']} 只")
    print(f"  跳过: {result['skip']} 只")
    
    if result['fail_list']:
        print(f"  失败列表: {result['fail_list'][:20]}...")
    
    # Step 4: 保存股票列表
    list_file = os.path.join(DATA_DIR, "stock_list.csv")
    filtered_df.to_csv(list_file, index=False)
    print(f"\n  股票列表已保存: {list_file}")
    
    print("\n" + "=" * 60)
    print("✅ 初始化完成")
    print("=" * 60)
