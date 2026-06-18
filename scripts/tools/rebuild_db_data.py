#!/usr/bin/env python3
"""
rebuild_db_data.py — 全量重新拉取所有股票数据，修复 amount 计算 bug
用修复后的 fetch_tencent_kline（不再 *100）覆盖 DB 中最近一年的数据
之前的估算公式: amount = vwap * volume * 100（错，volume 单位是股不是手）
修复后的公式: amount = vwap * volume
"""
import sys, os, time
from datetime import datetime, timedelta

sys.path.insert(0, os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.environ.get('PROJECT_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'scripts'))

from scripts.update_daily_data import fetch_tencent_kline, get_stock_list, HEADERS
from core.db import get_conn

print("=" * 60)
print("全量重拉 DB 数据（修复 amount 计算）")
print("=" * 60)

stocks = get_stock_list()
print(f"股票池: {len(stocks)} 只")

# 拉取最近 400 天（覆盖闰年 + buffer）
days = 400
cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
print(f"拉取范围: {cutoff} ~ 今天")

# 验证 fetch_tencent_kline 的 amount 是否正常
test_code = stocks[0]
test_df = fetch_tencent_kline(test_code, days=10)
if test_df is not None and len(test_df) > 0:
    latest = test_df.iloc[-1]
    vwap = (latest['open'] + latest['close'] + latest['high'] + latest['low']) / 4
    ratio = latest['amount'] / (vwap * latest['volume']) if vwap * latest['volume'] > 0 else 0
    print(f"\n验证 {test_code}: amount/(vwap*vol) = {ratio:.4f} (应≈1.0)")
    if abs(ratio - 1.0) > 0.1:
        print("❌ amount 公式仍有问题！")
        sys.exit(1)
    print("✅ amount 公式正确")

# 开始全量更新
print(f"\n开始全量更新 {len(stocks)} 只股票...")
t0 = time.time()
success = 0
fail = 0
skip = 0
total_records = 0
fail_list = []

for i, code in enumerate(stocks):
    if (i + 1) % 100 == 0:
        elapsed = time.time() - t0
        rate = (i + 1) / elapsed
        eta = (len(stocks) - i - 1) / rate
        print(f"  [{i+1}/{len(stocks)}] 成功 {success} 失败 {fail} 写入 {total_records} 条 "
              f"({rate:.1f}/s, ETA {eta:.0f}s)")
    
    try:
        df = fetch_tencent_kline(code, days=days)
        if df is None or len(df) == 0:
            skip += 1
            continue
        
        # 过滤掉 cutoff 之前的数据
        df = df[df.index >= cutoff]
        if len(df) == 0:
            skip += 1
            continue
        
        # 构建 upsert 记录
        records = []
        for date_idx, row in df.iterrows():
            date_str = str(date_idx)[:10]
            records.append((
                code, date_str,
                float(row.get('open', 0) or 0),
                float(row.get('high', 0) or 0),
                float(row.get('low', 0) or 0),
                float(row.get('close', 0) or 0),
                float(row.get('volume', 0) or 0),
                float(row.get('amount', 0) or 0),
            ))
        
        if records:
            with get_conn() as conn:
                conn.executemany("""
                    INSERT OR REPLACE INTO daily_kline 
                    (code, date, open, high, low, close, volume, amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, records)
                conn.commit()
            total_records += len(records)
        
        success += 1
    except Exception as e:
        fail += 1
        if len(fail_list) < 10:
            fail_list.append(f"{code}: {e}")

elapsed = time.time() - t0
print(f"\n{'='*60}")
print(f"完成: {elapsed:.1f}s")
print(f"  成功: {success}/{len(stocks)}")
print(f"  失败: {fail}")
print(f"  跳过: {skip}")
print(f"  写入记录: {total_records}")
if fail_list:
    print(f"\n失败示例:")
    for f in fail_list[:5]:
        print(f"  {f}")

# 最终验证
print(f"\n{'='*60}")
print("最终验证:")
with get_conn() as conn:
    stats = conn.execute('''
        SELECT 
            COUNT(*) as total,
            COUNT(CASE WHEN amount/close/volume BETWEEN 0.8 AND 1.2 THEN 1 END) as ideal,
            COUNT(CASE WHEN amount/close/volume > 3 THEN 1 END) as high,
            COUNT(CASE WHEN amount/close/volume < 0.5 THEN 1 END) as low
        FROM daily_kline
        WHERE volume > 0 AND close > 0 AND amount > 0
    ''').fetchone()
    
    print(f"  总记录: {stats['total']}")
    print(f"  理想 (0.8-1.2): {stats['ideal']} ({stats['ideal']/stats['total']*100:.1f}%)")
    print(f"  异常高 (>3): {stats['high']}")
    print(f"  异常低 (<0.5): {stats['low']}")
    
    # 样本
    samples = conn.execute('''
        SELECT code, date, close, volume, amount
        FROM daily_kline
        WHERE volume > 0 AND close > 0 AND amount > 0
        ORDER BY date DESC
        LIMIT 5
    ''').fetchall()
    print(f"\n最新数据样本:")
    for r in samples:
        ratio = r['amount'] / r['close'] / r['volume']
        print(f"  {r['code']} {r['date']}: C={r['close']:.2f} V={r['volume']:.0f} A={r['amount']:.0f} ratio={ratio:.2f}")
