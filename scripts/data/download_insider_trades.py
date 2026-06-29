"""
下载高管增减持数据到 quant_stocks.db。
数据源：同花顺 stock_management_change_ths
"""
import time
import sqlite3
import pandas as pd
import akshare as ak
from concurrent.futures import ThreadPoolExecutor, as_completed


DB_PATH = 'data/quant_stocks.db'


def _init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS insider_trades (
            code TEXT,
            trade_date TEXT,
            name TEXT,
            relation TEXT,
            change_shares REAL,
            price REAL,
            remaining_shares REAL,
            method TEXT,
            PRIMARY KEY (code, trade_date, name, method)
        )
    """)
    conn.commit()
    conn.close()


def _fetch_and_save(code: str) -> tuple:
    """单只股票的下载+写入（线程安全，带重试）。"""
    df = None
    for attempt in range(3):
        try:
            df = ak.stock_management_change_ths(symbol=code)
            if df is not None and len(df) > 0:
                break
            return code, 0
        except Exception:
            if attempt < 2:
                time.sleep(1 * (attempt + 1))
            else:
                return code, 0
    
    if df is None or len(df) == 0:
        return code, 0
    
    try:
        
        df = df.copy()
        df['code'] = code
        
        df = df.rename(columns={
            '变动日期': 'trade_date',
            '变动人': 'name',
            '与公司高管关系': 'relation',
            '变动数量': 'change_shares',
            '交易均价': 'price',
            '剩余股数': 'remaining_shares',
            '股份变动途径': 'method'
        })
        
        df['price'] = pd.to_numeric(df['price'], errors='coerce')
        df['remaining_shares'] = pd.to_numeric(df['remaining_shares'], errors='coerce')
        
        # 解析变动数量：格式为 "增持1600.00" 或 "减持4.62万"
        def parse_change(val):
            if pd.isna(val):
                return 0.0
            s = str(val).strip()
            # 提取方向和数值
            if s.startswith('增持'):
                sign = 1
                num_str = s[2:]
            elif s.startswith('减持'):
                sign = -1
                num_str = s[2:]
            else:
                return 0.0
            # 处理"万"
            if '万' in num_str:
                num_str = num_str.replace('万', '')
                try:
                    return sign * float(num_str) * 10000
                except:
                    return 0.0
            else:
                try:
                    return sign * float(num_str)
                except:
                    return 0.0
        
        df['change_shares'] = df['change_shares'].apply(parse_change)
        
        cols = ['code', 'trade_date', 'name', 'relation', 'change_shares', 'price', 'remaining_shares', 'method']
        df = df[[c for c in cols if c in df.columns]]
        
        # 每个线程独立连接
        conn = sqlite3.connect(DB_PATH)
        try:
            df.to_sql('insider_trades', conn, if_exists='append', index=False)
            conn.commit()
        except Exception:
            try:
                df.to_sql('insider_trades', conn, if_exists='replace', index=False)
                conn.commit()
            except Exception:
                pass
        conn.close()
        
        return code, len(df)
    except Exception as e:
        return code, 0


def download_insider_trades(codes: list, max_workers: int = 8):
    """
    下载高管增减持数据（线程安全版）。
    """
    _init_db()
    
    results = {'success': 0, 'fail': 0, 'total_records': 0}
    t0 = time.time()
    
    # 检查已下载的股票（断点续传）
    conn = sqlite3.connect(DB_PATH)
    existing = set(r[0] for r in conn.execute("SELECT DISTINCT code FROM insider_trades").fetchall())
    conn.close()
    
    codes_todo = [c for c in codes if c not in existing]
    print(f'已有 {len(existing)} 只，待下载 {len(codes_todo)} 只')
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_and_save, code): code for code in codes_todo}
        
        for i, future in enumerate(as_completed(futures)):
            code, n = future.result()
            
            if n > 0:
                results['success'] += 1
                results['total_records'] += n
            else:
                results['fail'] += 1
            
            if (i + 1) % 100 == 0:
                elapsed = time.time() - t0
                rate = (i + 1) / elapsed
                eta = (len(codes_todo) - i - 1) / rate
                recs = results["total_records"]
                print(f'  进度: {i+1}/{len(codes_todo)} ({rate:.1f}只/s, ETA {eta:.0f}s, 记录 {recs})')
    
    elapsed = time.time() - t0
    ok = results["success"]
    fail = results["fail"]
    recs = results["total_records"]
    print(f'\n完成: 成功 {ok}, 失败 {fail}, 总记录 {recs}, 耗时 {elapsed:.1f}s')
    return results


if __name__ == '__main__':
    conn = sqlite3.connect(DB_PATH)
    codes = [r[0] for r in conn.execute("SELECT code FROM stock_pool_zz1800 WHERE is_active=1").fetchall()]
    conn.close()
    
    print(f'下载 {len(codes)} 只股票的高管增减持数据...')
    result = download_insider_trades(codes, max_workers=8)
    print(f'\n结果: {result}')
