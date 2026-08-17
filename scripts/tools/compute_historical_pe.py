#!/usr/bin/env python3
"""计算历史PE/PB（带断点续传）

用已有日线close + 季度EPS/BVPS计算
PE = Close / EPS_TTM
PB = Close / BVPS
断点续传：每200只股票保存进度到checkpoint文件+增量写DB
"""
import sys
sys.path.insert(0, "/root/a-share-quant-sim")

import sqlite3
import pandas as pd
import numpy as np
import os

CHECKPOINT_FILE = '/tmp/pe_done_codes.txt'


def get_quarter_dates():
    quarters = []
    for year in range(2020, 2027):
        for q in ['0331', '0630', '0930', '1231']:
            quarters.append(f'{year}{q}')
    return quarters


def pull_financial_data():
    import akshare as ak
    quarters = get_quarter_dates()
    all_data = []
    for q in quarters:
        print(f"  拉取 {q}...", end=" ")
        try:
            df = ak.stock_yjbb_em(date=q)
            if df is not None and len(df) > 0:
                subset = df[['股票代码', '每股收益', '每股净资产']].copy()
                subset.columns = ['code', 'eps', 'bvps']
                subset['code'] = subset['code'].astype(str).str.zfill(6)
                subset['quarter'] = q
                all_data.append(subset)
                print(f"{len(subset)}条")
            else:
                print("无数据")
        except Exception as e:
            print(f"失败: {e}")
    if all_data:
        result = pd.concat(all_data, ignore_index=True)
        result.to_csv('/tmp/quarterly_financial.csv', index=False)
        print(f"\n总计: {len(result)}条")
        return result
    return None


def load_done_codes():
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE) as f:
            return set(f.read().strip().split('\n'))
    return set()


def save_done_codes(codes):
    with open(CHECKPOINT_FILE, 'w') as f:
        f.write('\n'.join(sorted(codes)))


def compute_historical_pe_pb():
    print("=" * 60)
    print("计算历史PE/PB（断点续传版）")
    print("=" * 60)

    conn = sqlite3.connect('data/quant_stocks.db')

    # 创建表
    conn.execute('''CREATE TABLE IF NOT EXISTS valuation_history (
        code TEXT, date TEXT, pe_ttm REAL, pb REAL, eps REAL, bvps REAL,
        PRIMARY KEY (code, date)
    )''')

    # 1. 加载日线数据
    print("\n[1] 加载日线数据...")
    kline = pd.read_sql(
        "SELECT code, date, close FROM daily_kline WHERE date >= '2020-01-01' ORDER BY code, date",
        conn
    )
    print(f"  {len(kline)}条")

    # 2. 加载季度财务数据
    print("\n[2] 加载季度财务数据...")
    fin = pd.read_csv('/tmp/quarterly_financial.csv', dtype={'code': str})
    fin['code'] = fin['code'].str.zfill(6)
    fin['quarter_end'] = pd.to_datetime(fin['quarter'], format='%Y%m%d')
    print(f"  {len(fin)}条, {fin['quarter'].nunique()}个季度")

    # 3. 加载断点
    done_codes = load_done_codes()
    print(f"\n[3] 断点续传: 已完成{len(done_codes)}只")

    # 4. 计算每日PE/PB
    print("\n[4] 计算每日PE/PB...")
    codes = kline['code'].unique()
    batch = []
    total_new = 0

    for i, code in enumerate(codes):
        if code in done_codes:
            continue

        stock_kline = kline[kline['code'] == code].copy()
        stock_kline['date'] = pd.to_datetime(stock_kline['date'])
        stock_fin = fin[fin['code'] == code].sort_values('quarter_end')

        if len(stock_fin) == 0 or len(stock_kline) == 0:
            done_codes.add(code)
            continue

        for _, row in stock_kline.iterrows():
            date = row['date']
            close = row['close']
            valid_fin = stock_fin[stock_fin['quarter_end'] <= date]
            if len(valid_fin) == 0:
                continue
            latest = valid_fin.iloc[-1]
            eps = latest['eps']
            bvps = latest['bvps']
            if pd.isna(eps) or pd.isna(bvps) or eps <= 0 or bvps <= 0:
                continue
            batch.append((code, date.strftime('%Y-%m-%d'), round(close/eps, 4), round(close/bvps, 4), eps, bvps))

        done_codes.add(code)

        # 每200只写一次DB + 保存checkpoint
        if (i + 1) % 200 == 0:
            if batch:
                conn.executemany("INSERT OR REPLACE INTO valuation_history VALUES (?,?,?,?,?,?)", batch)
                conn.commit()
                total_new += len(batch)
                batch = []
            save_done_codes(done_codes)
            print(f"  进度: {i+1}/{len(codes)} (新写入: {total_new})")

    # 最终写入
    if batch:
        conn.executemany("INSERT OR REPLACE INTO valuation_history VALUES (?,?,?,?,?,?)", batch)
        conn.commit()
        total_new += len(batch)
    save_done_codes(done_codes)

    # 统计
    count = conn.execute("SELECT COUNT(*) FROM valuation_history").fetchone()[0]
    print(f"\n[5] 完成! DB中共{count}条 (本次新写入{total_new})")
    conn.close()


if __name__ == "__main__":
    import os
    if not os.path.exists('/tmp/quarterly_financial.csv'):
        print("拉取季度财务数据...")
        pull_financial_data()
    compute_historical_pe_pb()
