#!/usr/bin/env python3
"""批量拉取估值数据（PE/PB/市值）

逐股拉取，支持断点续跑。
结果存 data/external/valuation_daily.csv
"""
import sys
sys.path.insert(0, "/root/a-share-quant-sim")

import os
import time
import pandas as pd
import sqlite3
from datetime import datetime

OUTPUT_FILE = "data/external/valuation_daily.csv"
PROGRESS_FILE = "/tmp/valuation_progress.txt"


def get_zz1800_stocks():
    """获取zz1800成分股"""
    conn = sqlite3.connect('data/quant_stocks.db')
    codes = [r[0] for r in conn.execute(
        "SELECT code FROM stock_pool_zz1800 WHERE is_active=1"
    ).fetchall()]
    conn.close()
    return codes


def load_done_stocks():
    """已拉取的股票"""
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f:
            return set(f.read().strip().split('\n'))
    return set()


def mark_done(code):
    """标记完成"""
    with open(PROGRESS_FILE, 'a') as f:
        f.write(code + '\n')


def pull_one(code):
    """拉取单只股票估值数据"""
    import akshare as ak
    try:
        df = ak.stock_value_em(symbol=code)
        if df is None or len(df) == 0:
            return None
        df = df.rename(columns={
            '数据日期': 'date',
            'PE(TTM)': 'pe_ttm',
            '市净率': 'pb',
            '总市值': 'total_mv',
            '流通市值': 'circ_mv'
        })
        df['code'] = code
        df['date'] = pd.to_datetime(df['date'])
        return df[['code', 'date', 'pe_ttm', 'pb', 'total_mv', 'circ_mv']]
    except Exception as e:
        print(f"  {code} 失败: {e}")
        return None


def main():
    print("=" * 60)
    print("批量拉取估值数据")
    print("=" * 60)

    codes = get_zz1800_stocks()
    print(f"zz1800成分股: {len(codes)}只")

    done = load_done_stocks()
    print(f"已完成: {len(done)}只")

    todo = [c for c in codes if c not in done]
    print(f"待拉取: {len(todo)}只")

    if len(todo) == 0:
        print("全部完成！")
        return

    # 加载已有数据
    all_data = []
    if os.path.exists(OUTPUT_FILE):
        existing = pd.read_csv(OUTPUT_FILE)
        all_data.append(existing)
        print(f"已有数据: {len(existing)}行")

    # 逐股拉取
    success = 0
    fail = 0
    start_time = time.time()

    for i, code in enumerate(todo):
        if i > 0 and i % 50 == 0:
            elapsed = time.time() - start_time
            eta = elapsed / i * (len(todo) - i)
            print(f"  进度: {i}/{len(todo)} ({i/len(todo)*100:.1f}%) "
                  f"成功{success} 失败{fail} "
                  f"耗时{elapsed/60:.1f}分钟 预计剩余{eta/60:.1f}分钟")

        df = pull_one(code)
        if df is not None and len(df) > 0:
            all_data.append(df)
            success += 1
        else:
            fail += 1

        mark_done(code)

        # 每100股保存一次
        if (i + 1) % 100 == 0:
            combined = pd.concat(all_data, ignore_index=True)
            combined.to_csv(OUTPUT_FILE, index=False)
            print(f"  已保存: {OUTPUT_FILE} ({len(combined)}行)")

        # 限速，避免被封
        time.sleep(0.5)

    # 最终保存
    if all_data:
        combined = pd.concat(all_data, ignore_index=True)
        combined.to_csv(OUTPUT_FILE, index=False)
        elapsed = time.time() - start_time
        print(f"\n完成！总耗时: {elapsed/60:.1f}分钟")
        print(f"成功: {success}, 失败: {fail}")
        print(f"数据保存: {OUTPUT_FILE} ({len(combined)}行)")


if __name__ == "__main__":
    main()
