"""
下载限售解禁数据到 quant_stocks.db。
数据源：东方财富 stock_restricted_release_queue_em
"""
import time
import sqlite3
import pandas as pd
import akshare as ak
from concurrent.futures import ThreadPoolExecutor, as_completed


def download_unlock_events(codes: list, max_workers: int = 5, db_path: str = 'data/quant_stocks.db'):
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS unlock_events (
            code TEXT,
            unlock_date TEXT,
            shareholder_count REAL,
            unlock_shares REAL,
            actual_unlock_shares REAL,
            unlock_market_value REAL,
            pct_of_total REAL,
            pct_of_circulating REAL,
            price_before REAL,
            lock_type TEXT,
            PRIMARY KEY (code, unlock_date)
        )
    """)
    conn.commit()
    
    results = {'success': 0, 'fail': 0, 'total_records': 0}
    t0 = time.time()
    
    def fetch_one(code):
        try:
            df = ak.stock_restricted_release_queue_em(symbol=code)
            if df is not None and len(df) > 0:
                return code, df
            return code, None
        except Exception:
            return code, None
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_one, code): code for code in codes}
        
        for i, future in enumerate(as_completed(futures)):
            code, df = future.result()
            
            if df is not None and len(df) > 0:
                df = df.copy()
                df['code'] = code
                
                df = df.rename(columns={
                    '解禁时间': 'unlock_date',
                    '解禁股东数': 'shareholder_count',
                    '解禁数量': 'unlock_shares',
                    '实际解禁数量': 'actual_unlock_shares',
                    '实际解禁数量市值': 'unlock_market_value',
                    '占总市值比例': 'pct_of_total',
                    '占流通市值比例': 'pct_of_circulating',
                    '解禁前一交易日收盘价': 'price_before',
                    '限售股类型': 'lock_type'
                })
                
                cols = ['code', 'unlock_date', 'shareholder_count', 'unlock_shares',
                        'actual_unlock_shares', 'unlock_market_value', 'pct_of_total',
                        'pct_of_circulating', 'price_before', 'lock_type']
                df = df[[c for c in cols if c in df.columns]]
                
                # 数值化
                for c in ['shareholder_count', 'unlock_shares', 'actual_unlock_shares',
                          'unlock_market_value', 'pct_of_total', 'pct_of_circulating', 'price_before']:
                    if c in df.columns:
                        df[c] = pd.to_numeric(df[c], errors='coerce')
                
                try:
                    df.to_sql('unlock_events', conn, if_exists='append', index=False)
                    results['success'] += 1
                    results['total_records'] += len(df)
                except Exception:
                    pass
            else:
                results['fail'] += 1
            
            if (i + 1) % 200 == 0:
                elapsed = time.time() - t0
                rate = (i + 1) / elapsed
                eta = (len(codes) - i - 1) / rate
                recs = results["total_records"]
                print(f'  进度: {i+1}/{len(codes)} ({rate:.1f}只/s, ETA {eta:.0f}s, 记录 {recs})')
    
    conn.close()
    elapsed = time.time() - t0
    ok = results["success"]
    fail = results["fail"]
    recs = results["total_records"]
    print(f'\n完成: 成功 {ok}, 失败 {fail}, 总记录 {recs}, 耗时 {elapsed:.1f}s')
    return results


if __name__ == '__main__':
    conn = sqlite3.connect('data/quant_stocks.db')
    codes = [r[0] for r in conn.execute("SELECT code FROM stock_pool_zz1800 WHERE is_active=1").fetchall()]
    conn.close()
    
    print(f'下载 {len(codes)} 只股票的解禁数据...')
    result = download_unlock_events(codes, max_workers=5)
    print(f'\n结果: {result}')
