#!/usr/bin/env python3
"""
restore_amount_before_may21.py — 恢复 2026-05-20 及之前的 amount 数据
这些数据的 amount 被误除了 100，需要恢复（乘回 100）
"""
import sys, time
from core.db import get_conn

print("恢复 2026-05-20 及之前的 amount 数据（乘回 100）")

with get_conn() as conn:
    # 先看看当前状态
    rows = conn.execute("""
        SELECT date, 
               COUNT(*) as cnt,
               AVG(amount / close / volume) as avg_ratio
        FROM daily_kline 
        WHERE volume > 0 AND close > 0 AND amount > 0
        AND date BETWEEN '2024-01-01' AND '2026-05-20'
        GROUP BY date
        ORDER BY date DESC
        LIMIT 5
    """).fetchall()
    
    print("修复前 (被误除100后):")
    for r in rows:
        print(f"  {r['date']}: {r['cnt']} 条, avg_ratio={r['avg_ratio']:.4f}")
    
    # 需要修复的记录数
    need_fix = conn.execute("""
        SELECT COUNT(*) FROM daily_kline 
        WHERE date <= '2026-05-20' AND amount > 0
    """).fetchone()[0]
    print(f"\n需要恢复的记录数: {need_fix}")
    
    # 执行恢复
    t0 = time.time()
    conn.execute("""
        UPDATE daily_kline 
        SET amount = amount * 100.0 
        WHERE date <= '2026-05-20' AND amount > 0
    """)
    conn.commit()
    elapsed = time.time() - t0
    print(f"恢复完成: {elapsed:.2f}s")
    
    # 验证
    rows = conn.execute("""
        SELECT date, 
               COUNT(*) as cnt,
               AVG(amount / close / volume) as avg_ratio
        FROM daily_kline 
        WHERE volume > 0 AND close > 0 AND amount > 0
        AND date BETWEEN '2024-01-01' AND '2026-05-20'
        GROUP BY date
        ORDER BY date DESC
        LIMIT 5
    """).fetchall()
    
    print("\n恢复后:")
    for r in rows:
        print(f"  {r['date']}: {r['cnt']} 条, avg_ratio={r['avg_ratio']:.4f}")
    
    # 再检查 5/21 之后是否正常
    rows2 = conn.execute("""
        SELECT date, 
               COUNT(*) as cnt,
               AVG(amount / close / volume) as avg_ratio
        FROM daily_kline 
        WHERE volume > 0 AND close > 0 AND amount > 0
        AND date >= '2026-05-21'
        GROUP BY date
        ORDER BY date DESC
        LIMIT 5
    """).fetchall()
    
    print("\n5/21及之后 (不应该受影响):")
    for r in rows2:
        print(f"  {r['date']}: {r['cnt']} 条, avg_ratio={r['avg_ratio']:.4f}")
    
    # 最终检查
    abnormal = conn.execute("""
        SELECT COUNT(*) FROM daily_kline 
        WHERE volume > 0 AND close > 0 AND amount > 0
        AND (amount / close / volume > 5 OR amount / close / volume < 0.02)
    """).fetchone()[0]
    print(f"\n最终异常记录数: {abnormal}")
