#!/usr/bin/env python3
"""
backfill_star_kline.py — 回补科创板历史K线数据

腾讯API支持日期范围查询，可精确获取2021年以来的历史数据。
对stock_pool_zz1800中数据不完整的688股票，回补缺失的历史K线。

用法:
  python scripts/tools/backfill_star_kline.py [--start 2021-01-01] [--dry-run]
"""

import os, sys, time, sqlite3, requests, argparse
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
sys.path.insert(0, PROJECT_ROOT)

DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'quant_stocks.db')
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
}

# 科创板代码前缀
STAR_PREFIXES = ('688',)


def get_star_stocks_needing_backfill(conn, target_start='2021-01-01'):
    """找出需要回补的科创板股票"""
    rows = conn.execute("""
        SELECT code, MIN(date) as earliest, MAX(date) as latest, COUNT(*) as cnt
        FROM daily_kline
        WHERE code LIKE '688%'
        GROUP BY code
        HAVING earliest > ?
        ORDER BY code
    """, (target_start,)).fetchall()
    return rows


def fetch_tencent_kline_range(code, start_date, end_date):
    """
    从腾讯API获取指定日期范围的前复权日K线。
    支持大范围查询，API内部会自动分页。
    """
    # 判断市场前缀
    if code.startswith('6') or code.startswith('9'):
        tx_code = f"sh{code}"
    else:
        tx_code = f"sz{code}"

    url = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    all_records = []
    current_start = start_date

    while current_start <= end_date:
        param = f"{tx_code},day,{current_start},{end_date},500,qfq"
        try:
            r = requests.get(url, params={'param': param}, headers=HEADERS, timeout=15)
            data = r.json()
            if data.get('code') != 0:
                break

            stock_data = data.get('data', {}).get(tx_code, {})
            qfq_key = 'qfqday' if 'qfqday' in stock_data else 'day'
            klines = stock_data.get(qfq_key, [])

            if not klines:
                break

            for k in klines:
                if len(k) < 6:
                    continue
                all_records.append({
                    'date': k[0],
                    'open': float(k[1]),
                    'close': float(k[2]),
                    'high': float(k[3]),
                    'low': float(k[4]),
                    'volume': float(k[5]),
                    'amount': 0,  # 腾讯不提供成交额，后续估算
                })

            # 下一批从最后一天的下一天开始
            last_date = klines[-1][0]
            next_date = (datetime.strptime(last_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
            if next_date == current_start:
                break  # 防止死循环
            current_start = next_date

            time.sleep(0.3)  # 避免请求过快

        except Exception as e:
            print(f"    ⚠️ 请求失败: {e}")
            break

    if not all_records:
        return None

    import pandas as pd
    df = pd.DataFrame(all_records)
    df['date'] = pd.to_datetime(df['date'])
    df = df.set_index('date').sort_index()

    # 估算成交额: 均价 * 成交量
    vwap = (df['open'] + df['close'] + df['high'] + df['low']) / 4
    df['amount'] = vwap * df['volume']

    return df


def upsert_kline_batch(conn, code, df):
    """批量写入K线数据"""
    records = []
    for date, row in df.iterrows():
        date_str = date.strftime('%Y-%m-%d')
        records.append((
            code, date_str,
            float(row['open']), float(row['high']),
            float(row['low']), float(row['close']),
            float(row['volume']), float(row['amount'])
        ))

    conn.executemany("""
        INSERT INTO daily_kline (code, date, open, high, low, close, volume, amount)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code, date) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, volume=excluded.volume, amount=excluded.amount
    """, records)
    conn.commit()
    return len(records)


def main():
    parser = argparse.ArgumentParser(description='回补科创板历史K线数据')
    parser.add_argument('--start', default='2021-01-01', help='回补起始日期')
    parser.add_argument('--dry-run', action='store_true', help='只显示计划，不实际执行')
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)

    # 找出需要回补的股票
    stocks = get_star_stocks_needing_backfill(conn, args.start)
    print(f"📊 需要回补的科创板股票: {len(stocks)}只")
    print(f"   回补区间: {args.start} ~ 各股票最早K线日期之前")
    print()

    if args.dry_run:
        for code, earliest, latest, cnt in stocks:
            print(f"  {code}: 现有{earliest}~{latest}({cnt}条), 需回补{args.start}~{earliest}")
        conn.close()
        return

    total_inserted = 0
    total_skipped = 0
    t0 = time.time()

    for i, (code, earliest, latest, cnt) in enumerate(stocks):
        # 回补区间: start ~ earliest-1天
        backfill_end = (datetime.strptime(earliest, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')

        if backfill_end < args.start:
            total_skipped += 1
            continue

        print(f"[{i+1}/{len(stocks)}] {code}: 回补 {args.start} ~ {backfill_end} ...", end=' ', flush=True)

        df = fetch_tencent_kline_range(code, args.start, backfill_end)
        if df is None or len(df) == 0:
            print("无数据")
            continue

        n = upsert_kline_batch(conn, code, df)
        total_inserted += n
        print(f"✅ {n}条")

        time.sleep(0.2)  # 避免请求过快

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"回补完成: {total_inserted}条新数据, {total_skipped}只跳过")
    print(f"耗时: {elapsed:.1f}s")

    # 验证
    star_count = conn.execute("SELECT COUNT(DISTINCT code) FROM daily_kline WHERE code LIKE '688%'").fetchone()[0]
    star_with_2021 = conn.execute("""
        SELECT COUNT(DISTINCT code) FROM daily_kline 
        WHERE code LIKE '688%' AND date <= '2021-12-31'
    """).fetchone()[0]
    print(f"\n验证:")
    print(f"  科创板股票总数: {star_count}")
    print(f"  有2021年数据的: {star_with_2021}")

    conn.close()


if __name__ == '__main__':
    main()
