#!/usr/bin/env python3
"""通过 ProviderManager 更新日K线数据

严格遵守 Provider 架构：
- 优先使用主源（config/data_sources.yaml 配置）
- 主源 health_check 失败时自动 fallback 到备用源
- 不混用、不硬编码数据源
"""
import sys, os, time, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.provider_manager import ProviderManager
from core.providers.baostock import BaoStockProvider
from core.providers.tencent import TencentProvider
from core.db import upsert_kline_batch, get_stock_name_map, _db_path

BATCH_SIZE = 100


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=3, help='拉取最近N天')
    parser.add_argument('--start', type=str, default=None, help='开始日期 YYYY-MM-DD')
    parser.add_argument('--end', type=str, default=None, help='结束日期 YYYY-MM-DD')
    args = parser.parse_args()

    # 初始化 ProviderManager（自动读取 config/data_sources.yaml）
    pm = ProviderManager()
    pm.register('tencent', TencentProvider())
    pm.register('baostock', BaoStockProvider())

    # 健康检查
    health = pm.health_check_all()
    print(f"📊 Provider 健康状态: {health}")

    # 获取当前使用的数据源（按 fallback 链）
    active_provider = pm.get_provider()
    print(f"🎯 当前数据源: {active_provider.name}")

    # 日期范围
    from datetime import datetime, timedelta
    if args.start and args.end:
        start_date = args.start
        end_date = args.end
    else:
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=args.days)).strftime('%Y-%m-%d')
    print(f"📅 日期范围: {start_date} → {end_date}")

    # 股票池
    conn = sqlite3.connect(_db_path('quant_stocks.db'))
    codes = [r[0] for r in conn.execute('SELECT code FROM stock_pool').fetchall()]
    conn.close()
    print(f"📋 股票池: {len(codes)} 只")

    # 分批拉取（整个过程用同一个数据源，不混用）
    t0 = time.time()
    all_records = []
    total_batches = (len(codes) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(codes), BATCH_SIZE):
        batch = codes[i:i+BATCH_SIZE]
        batch_num = i // BATCH_SIZE + 1
        try:
            df = pm.get_daily_kline(batch, start_date, end_date)
            for _, row in df.iterrows():
                o = float(row.get('open', 0) or 0)
                h = float(row.get('high', 0) or 0)
                l = float(row.get('low', 0) or 0)
                c = float(row.get('close', 0) or 0)
                v = float(row.get('volume', 0) or 0)
                a = float(row.get('amount', 0) or 0)
                if c <= 0 or o <= 0 or v <= 0:
                    continue
                all_records.append((row['code'], str(row['date'])[:10], o, h, l, c, v, a))
            if batch_num % 10 == 0 or batch_num == total_batches:
                print(f"  ✅ 批次 {batch_num}/{total_batches}: 累计 {len(all_records)} 条记录")
        except Exception as e:
            print(f"  ❌ 批次 {batch_num} 失败: {e}")

    t_fetch = time.time() - t0
    print(f"\n📥 拉取完成: {len(all_records)} 条记录, 耗时 {t_fetch:.1f}s")

    # 写入 DB
    if all_records:
        t1 = time.time()
        upsert_kline_batch(all_records)
        t_db = time.time() - t1
        print(f"💾 DB写入: {t_db:.1f}s")

        # 验证
        conn = sqlite3.connect(_db_path('quant_stocks.db'))
        rows = conn.execute('''
            SELECT date, COUNT(*) as cnt 
            FROM daily_kline 
            WHERE date >= ? AND date <= ?
            GROUP BY date ORDER BY date
        ''', (start_date, end_date)).fetchall()
        conn.close()
        print(f"\n📊 更新后数据覆盖:")
        for r in rows:
            print(f"  {r[0]}: {r[1]} stocks")
    else:
        print("⚠️ 没有有效数据可写入")

    print(f"\n⏱️ 总耗时: {time.time()-t0:.1f}s")


if __name__ == '__main__':
    main()
