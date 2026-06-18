"""
多数据源 fallback 模块
======================

数据源优先级（自动切换）：
  1. 腾讯 API (主) — 实时行情 + 历史K线 + 批量获取
  2. AKShare (备) — 全量股票列表 + 历史K线
  3. 东方财富 (备) — 历史K线（间歇性可用）
  4. Tushare (备) — 需要 token

用法:
  from data_fetcher import fetch_kline, fetch_spot, fetch_stock_list
  
  df = fetch_kline('600519', days=30)  # 自动选择可用数据源
  spot = fetch_spot('600519')
  stocks = fetch_stock_list()  # 全 A 股列表
"""

import os, sys, time, json, hashlib
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from functools import lru_cache

# ============================================================
# 配置
# ============================================================

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'http://stockapp.finance.qq.com/',
}

# 缓存目录
CACHE_DIR = os.path.join(os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


# ============================================================
# 数据源 1: 腾讯 API (主)
# ============================================================

def _tx_code(code):
    """生成腾讯格式代码"""
    if code.startswith('6') or code.startswith('9'):
        return f"sh{code}"
    return f"sz{code}"


def fetch_tencent_spot(code):
    """腾讯实时行情"""
    url = f"http://qt.gtimg.cn/q={_tx_code(code)}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        text = r.text.strip()
        if '~' not in text:
            return None
        parts = text.split('~')
        if len(parts) < 50:
            return None
        return {
            'code': parts[2],
            'name': parts[1],
            'close': float(parts[3]) if parts[3] else 0,
            'open': float(parts[5]) if parts[5] else 0,
            'high': float(parts[33]) if len(parts) > 33 and parts[33] else 0,
            'low': float(parts[34]) if len(parts) > 34 and parts[34] else 0,
            'volume': float(parts[6]) if parts[6] else 0,
            'amount': float(parts[37]) * 10000 if len(parts) > 37 and parts[37] else 0,
            'change_pct': float(parts[32]) if len(parts) > 32 and parts[32] else 0,
            'source': 'tencent',
        }
    except:
        return None


def fetch_tencent_kline(code, days=30):
    """腾讯历史 K 线"""
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {'param': f"{_tx_code(code)},day,,,{days},qfq"}
    
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=15)
        data = r.json()
        
        if data.get('code') != 0:
            return None
        
        tx = _tx_code(code)
        stock_data = data.get('data', {}).get(code, None)
        if stock_data is None:
            stock_data = data.get('data', {}).get(tx, None)
        if stock_data is None:
            return None
        
        qfq_key = 'qfqday' if 'qfqday' in stock_data else 'day'
        klines = stock_data.get(qfq_key, [])
        if not klines:
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
        
        df = pd.DataFrame(records).set_index('date').sort_index()
        vwap = (df['open'] + df['close'] + df['high'] + df['low']) / 4
        df['amount'] = vwap * df['volume'] * 100
        df['outstanding_share'] = np.nan
        df['turnover'] = np.nan
        df['source'] = 'tencent'
        
        return df
    
    except:
        return None


def fetch_tencent_stock_list(prefixes=None):
    """
    腾讯批量获取股票列表（按前缀遍历）
    
    Parameters
    ----------
    prefixes : list[str] - 代码前缀列表，如 ['600', '601', '000', '002', '300']
    
    Returns
    -------
    DataFrame with columns [code, name]
    """
    if prefixes is None:
        prefixes = ['600', '601', '603', '605', '000', '001', '002', '003', '300', '301', '302']
    
    all_stocks = []
    
    for prefix in prefixes:
        # 每批 100 只
        for start in range(1, 1000, 100):
            batch = []
            for i in range(start, min(start + 100, 1000)):
                code = f"{prefix}{str(i).zfill(3)}"
                batch.append(_tx_code(code))
            
            codes_str = ",".join(batch)
            url = f"http://qt.gtimg.cn/q={codes_str}"
            
            try:
                r = requests.get(url, headers=HEADERS, timeout=10)
                for line in r.text.strip().split('\n'):
                    if '~' in line and len(line) > 20:
                        parts = line.split('~')
                        if len(parts) > 2 and parts[1]:
                            all_stocks.append({'code': parts[2], 'name': parts[1]})
            except:
                pass
            
            time.sleep(0.15)  # 限速
    
    df = pd.DataFrame(all_stocks).drop_duplicates(subset=['code'])
    df['source'] = 'tencent'
    return df


# ============================================================
# 数据源 2: AKShare (备)
# ============================================================

def fetch_akshare_kline(code, days=30):
    """AKShare 历史 K 线"""
    try:
        import akshare as ak
        
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=days * 2)).strftime('%Y%m%d')
        
        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                 start_date=start_date, end_date=end_date,
                                 adjust="qfq")
        
        if df is None or len(df) == 0:
            return None
        
        df = df.rename(columns={
            '日期': 'date', '开盘': 'open', '收盘': 'close',
            '最高': 'high', '最低': 'low', '成交量': 'volume', '成交额': 'amount',
        })
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        df['outstanding_share'] = np.nan
        df['turnover'] = np.nan
        df['source'] = 'akshare'
        
        return df
    
    except ImportError:
        return None
    except:
        return None


def fetch_akshare_stock_list():
    """AKShare 全 A 股列表"""
    try:
        import akshare as ak
        
        for attempt in range(3):
            try:
                df = ak.stock_zh_a_spot_em()
                if len(df) > 0:
                    result = pd.DataFrame({
                        'code': df['代码'].astype(str).str.zfill(6),
                        'name': df['名称'],
                        'source': 'akshare',
                    })
                    return result
            except:
                if attempt < 2:
                    time.sleep(2)
        return None
    
    except ImportError:
        return None


# ============================================================
# 数据源 3: 东方财富 (备)
# ============================================================

def fetch_eastmoney_kline(code, days=30):
    """东方财富历史 K 线"""
    secid = f"1.{code}" if code.startswith('6') else f"0.{code}"
    
    for protocol in ['http', 'https']:
        url = f"{protocol}://push2his.eastmoney.com/api/qt/stock/kline/get"
        params = {
            'secid': secid,
            'fields1': 'f1,f2,f3,f4,f5,f6',
            'fields2': 'f51,f52,f53,f54,f55,f56,f57',
            'klt': 101, 'fqt': 1,
            'end': datetime.now().strftime('%Y%m%d'),
            'lmt': days,
            'ut': 'bd1d9ddb04089700cf9c27f6f7426281',
        }
        
        try:
            r = requests.get(url, params=params, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
            if not r.text.strip():
                continue
            
            data = r.json()
            if not data.get('data') or not data['data'].get('klines'):
                continue
            
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
            
            if records:
                df = pd.DataFrame(records).set_index('date').sort_index()
                df['outstanding_share'] = np.nan
                df['turnover'] = np.nan
                df['source'] = 'eastmoney'
                return df
        
        except:
            continue
    
    return None


# ============================================================
# 数据源 4: Tushare (备，需要 token)
# ============================================================

def _get_tushare_pro():
    """获取 Tushare pro 实例"""
    try:
        import tushare as ts
        
        # 尝试从文件读取 token
        token_file = os.path.expanduser("~/.tushare_token")
        if os.path.exists(token_file):
            with open(token_file) as f:
                token = f.read().strip()
            return ts.pro_api(token)
        
        # 尝试环境变量
        token = os.environ.get('TUSHARE_TOKEN', '')
        if token:
            return ts.pro_api(token)
        
        return None
    except ImportError:
        return None


def fetch_tushare_kline(code, days=30):
    """Tushare 历史 K 线"""
    pro = _get_tushare_pro()
    if pro is None:
        return None
    
    try:
        ts_code = f"{code}.SH" if code.startswith('6') else f"{code}.SZ"
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=days * 2)).strftime('%Y%m%d')
        
        df = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
        
        if df is None or len(df) == 0:
            return None
        
        df = df.rename(columns={
            'trade_date': 'date', 'open': 'open', 'close': 'close',
            'high': 'high', 'low': 'low', 'vol': 'volume', 'amount': 'amount',
        })
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date').sort_index()
        df['volume'] = df['volume'] * 100  # 手 → 股
        df['amount'] = df['amount'] * 1000  # 千元 → 元
        df['outstanding_share'] = np.nan
        df['turnover'] = np.nan
        df['source'] = 'tushare'
        
        return df
    
    except:
        return None


# ============================================================
# 统一接口（多源 fallback）
# ============================================================

def fetch_kline(code, days=30, source='auto'):
    """
    获取单只股票历史 K 线（多源 fallback）
    
    Parameters
    ----------
    code : str - 股票代码
    days : int - 获取天数
    source : str - 'auto' | 'tencent' | 'akshare' | 'eastmoney' | 'tushare'
    
    Returns
    -------
    DataFrame or None
    """
    # 检查缓存
    cache_key = f"{code}_{days}_{source}"
    cache_file = os.path.join(CACHE_DIR, f"kline_{hashlib.md5(cache_key.encode()).hexdigest()}.csv")
    
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file, index_col='date', parse_dates=True)
            if len(df) > 0 and (datetime.now() - df.index[-1]).days < 1:
                return df
        except:
            pass
    
    sources = {
        'tencent': fetch_tencent_kline,
        'akshare': fetch_akshare_kline,
        'eastmoney': fetch_eastmoney_kline,
        'tushare': fetch_tushare_kline,
    }
    
    if source == 'auto':
        # 按优先级尝试
        for src_name in ['tencent', 'akshare', 'eastmoney', 'tushare']:
            try:
                df = sources[src_name](code, days=days)
                if df is not None and len(df) > 0:
                    # 写入缓存
                    df.to_csv(cache_file)
                    return df
            except:
                continue
        return None
    else:
        func = sources.get(source)
        if func:
            return func(code, days=days)
        return None


def fetch_spot(code, source='auto'):
    """
    获取单只股票实时行情
    
    Parameters
    ----------
    code : str - 股票代码
    source : str - 'auto' | 'tencent'
    
    Returns
    -------
    dict or None
    """
    if source in ('auto', 'tencent'):
        return fetch_tencent_spot(code)
    return None


def fetch_stock_list(source='auto', prefixes=None, use_cache=True):
    """
    获取全 A 股股票列表
    
    Parameters
    ----------
    source : str - 'auto' | 'tencent' | 'akshare'
    prefixes : list[str] - 腾讯模式下的代码前缀列表
    use_cache : bool - 是否使用缓存
    
    Returns
    -------
    DataFrame with columns [code, name, source]
    """
    cache_file = os.path.join(CACHE_DIR, "stock_list.csv")
    
    # 检查缓存（24 小时内有效）
    if use_cache and os.path.exists(cache_file):
        try:
            cache_time = os.path.getmtime(cache_file)
            if (time.time() - cache_time) < 86400:  # 24 小时
                df = pd.read_csv(cache_file)
                if len(df) > 0:
                    return df
        except:
            pass
    
    if source == 'auto':
        # 优先 AKShare（全量），fallback 腾讯（按前缀）
        for src in ['akshare', 'tencent']:
            try:
                if src == 'akshare':
                    df = fetch_akshare_stock_list()
                else:
                    df = fetch_tencent_stock_list(prefixes)
                
                if df is not None and len(df) > 0:
                    df.to_csv(cache_file, index=False)
                    return df
            except:
                continue
        return None
    elif source == 'akshare':
        return fetch_akshare_stock_list()
    elif source == 'tencent':
        return fetch_tencent_stock_list(prefixes)
    
    return None


# ============================================================
# 便捷函数
# ============================================================

def get_data_source_status():
    """检查各数据源可用性"""
    status = {}
    
    # 腾讯
    try:
        spot = fetch_tencent_spot('600519')
        status['tencent'] = '✅ 可用' if spot else '❌ 不可用'
    except:
        status['tencent'] = '❌ 不可用'
    
    # AKShare
    try:
        import akshare
        status['akshare'] = '✅ 已安装'
    except ImportError:
        status['akshare'] = '❌ 未安装'
    
    # 东方财富
    try:
        df = fetch_eastmoney_kline('600519', days=3)
        status['eastmoney'] = '✅ 可用' if df is not None else '❌ 不可用'
    except:
        status['eastmoney'] = '❌ 不可用'
    
    # Tushare
    try:
        import tushare
        pro = _get_tushare_pro()
        status['tushare'] = '✅ 可用' if pro else '⚠️ 需要 token'
    except ImportError:
        status['tushare'] = '❌ 未安装'
    
    return status


if __name__ == "__main__":
    print("=" * 50)
    print("多数据源 fallback 模块测试")
    print("=" * 50)
    
    # 测试数据源状态
    print("\n📊 数据源状态:")
    for src, status in get_data_source_status().items():
        print(f"  {src}: {status}")
    
    # 测试获取 K 线
    print("\n📈 测试获取 K 线 (600519 贵州茅台):")
    df = fetch_kline('600519', days=5, source='auto')
    if df is not None:
        print(f"  ✅ 获取成功: {len(df)} 条 (来源: {df.get('source', 'unknown').iloc[0]})")
        print(f"  最新日期: {df.index[-1].date()}")
        print(f"  最新收盘: {df['close'].iloc[-1]:.2f}")
    else:
        print("  ❌ 所有数据源均失败")
    
    # 测试获取股票列表
    print("\n📋 测试获取股票列表 (腾讯 600 前缀):")
    stocks = fetch_tencent_stock_list(prefixes=['600'])
    if stocks is not None and len(stocks) > 0:
        print(f"  ✅ 获取成功: {len(stocks)} 只")
        print(f"  前5只: {stocks['code'].head().tolist()}")
    else:
        print("  ❌ 获取失败")
