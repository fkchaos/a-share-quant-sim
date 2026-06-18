#!/usr/bin/env python3
"""
fix_amount_data.py — 修复 DB 中 amount 数据异常
分析：5月20日之前 ratio=1.0（正常），5月25日之后 ratio=100（异常，被乘了100）
方案：只将 2026-05-21 之后的 amount 除以 100
"""
import sys, os, time
from datetime import datetime, timedelta

from core.db import get_conn

print("=" * 60)
print("修复 DB amount 数据")
print("=" * 60)

with get_conn() as conn:
    # 先看看异常数据的日期分布
    rows = conn.execute("""
        SELECT date, 
               COUNT(*) as cnt,
               AVG(amount / close / volume) as avg_ratio
        FROM daily_kline 
        WHERE volume > 0 AND close > 0 AND amount > 0
        AND date >= '2026-05-15'
        GROUP BY date
        ORDER BY date
    """).fetchall()
    
    print("\n按日期统计 amount/close/volume 比值:")
    for r in rows:
        status = "⚠️ 异常" if r['avg_ratio'] > 5 else "✅ 正常"
        print(f"  {r['date']}: {r['cnt']} 条, avg_ratio={r['avg_ratio']:.2f} {status}")
    
    # 找出异常开始日期
    abnormal_start = None
    for r in rows:
        if r['avg_ratio'] > 5:
            abnormal_start = r['date']
            break
    
    if abnormal_start is None:
        print("\n✅ 没有发现异常数据")
        sys.exit(0)
    
    print(f"\n异常开始日期: {abnormal_start}")
    
    # 统计需要修复的记录数
    need_fix = conn.execute("""
        SELECT COUNT(*) FROM daily_kline 
        WHERE date >= ? AND amount > 0
    """, (abnormal_start,)).fetchone()[0]
    print(f"需要修复的记录数: {need_fix}")
    
    # 执行修复：将异常日期之后的 amount 除以 100
    print(f"\n执行修复: amount = amount / 100 (date >= {abnormal_start})")
    t0 = time.time()
    
    conn.execute("""
        UPDATE daily_kline 
        SET amount = amount / 100.0 
        WHERE date >= ? AND amount > 0
    """, (abnormal_start,))
    conn.commit()
    
    elapsed = time.time() - t0
    print(f"修复完成: {elapsed:.2f}s")
    
    # 验证
    print("\n修复后验证:")
    rows = conn.execute("""
        SELECT date, 
               COUNT(*) as cnt,
               AVG(amount / close / volume) as avg_ratio
        FROM daily_kline 
        WHERE volume > 0 AND close > 0 AND amount > 0
        AND date >= '2026-05-15'
        GROUP BY date
        ORDER BY date
    """).fetchall()
    
    for r in rows:
        status = "⚠️ 异常" if r['avg_ratio'] > 5 else "✅ 正常"
        print(f"  {r['date']}: {r['cnt']} 条, avg_ratio={r['avg_ratio']:.2f} {status}")
    
    # 检查是否还有异常
    abnormal = conn.execute("""
        SELECT COUNT(*) FROM daily_kline 
        WHERE volume > 0 AND close > 0 AND amount > 0
        AND amount / close / volume > 5
    """).fetchone()[0]
    print(f"\n仍异常记录数 (ratio > 5): {abnormal}")
