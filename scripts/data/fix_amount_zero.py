#!/usr/bin/env python3
"""
v53 数据修复：DB amount=0 用 close × volume 回填

问题：DB 中 96.6% 的 amount 为 0（历史数据未获取 amount）
方案：amount = close × volume（与腾讯接口一致，amount 单位为元）
"""
import sqlite3
import time

DB_PATH = 'data/quant_stocks.db'

def fix_amount_zero():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 统计
    c.execute('SELECT COUNT(*) FROM daily_kline WHERE amount = 0 OR amount IS NULL')
    total_zero = c.fetchone()[0]
    print(f'amount=0 或 NULL 的记录数: {total_zero:,}')
    
    c.execute('SELECT COUNT(*) FROM daily_kline')
    total = c.fetchone()[0]
    print(f'总记录数: {total:,}')
    print(f'需要修复: {total_zero/total:.1%}')
    
    # 先验证 close 和 volume 是否完整
    c.execute('SELECT COUNT(*) FROM daily_kline WHERE (amount = 0 OR amount IS NULL) AND close > 0 AND volume > 0')
    fixable = c.fetchone()[0]
    print(f'可修复 (close>0 AND volume>0): {fixable:,}')
    
    c.execute('SELECT COUNT(*) FROM daily_kline WHERE (amount = 0 OR amount IS NULL) AND (close = 0 OR volume = 0 OR close IS NULL OR volume IS NULL)')
    not_fixable = c.fetchone()[0]
    print(f'不可修复 (close或volume为0/NULL): {not_fixable:,}')
    
    # 抽样验证
    print('\n抽样验证 (修复前):')
    c.execute('SELECT code, date, close, volume, amount FROM daily_kline WHERE amount = 0 AND close > 0 AND volume > 0 LIMIT 5')
    for row in c.fetchall():
        code, date, close, volume, amount = row
        expected = close * volume
        print(f'  {code} {date}: close={close}, volume={volume}, amount={amount}, expected={expected:.0f}')
    
    # 执行修复
    print(f'\n开始修复...')
    t0 = time.time()
    
    # UPDATE: amount = close * volume (WHERE amount = 0 OR amount IS NULL)
    c.execute('''
        UPDATE daily_kline 
        SET amount = close * volume 
        WHERE (amount = 0 OR amount IS NULL) 
          AND close > 0 AND volume > 0
    ''')
    updated = c.rowcount
    conn.commit()
    
    elapsed = time.time() - t0
    print(f'修复完成: {updated:,} 行, 耗时 {elapsed:.1f}s')
    
    # 验证
    print('\n抽样验证 (修复后):')
    c.execute('SELECT code, date, close, volume, amount FROM daily_kline WHERE close > 0 AND volume > 0 ORDER BY date ASC LIMIT 5')
    for row in c.fetchall():
        code, date, close, volume, amount = row
        expected = close * volume
        diff_pct = abs(amount - expected) / expected * 100 if expected > 0 else 0
        ok = '✅' if diff_pct < 1 else '⚠️'
        print(f'  {ok} {code} {date}: close={close}, volume={volume}, amount={amount:.0f}, expected={expected:.0f}, diff={diff_pct:.2f}%')
    
    # 统计修复后
    c.execute('SELECT COUNT(*) FROM daily_kline WHERE amount = 0 OR amount IS NULL')
    remaining = c.fetchone()[0]
    print(f'\n修复后 amount=0/NULL: {remaining:,}')
    
    # 验证 amount ≈ close × volume
    c.execute('''
        SELECT COUNT(*) FROM daily_kline 
        WHERE close > 0 AND volume > 0 AND amount > 0
          AND ABS(amount - close * volume) / (close * volume) > 0.01
    ''')
    mismatch = c.fetchone()[0]
    print(f'amount ≠ close×volume (>1%误差): {mismatch:,}')
    
    conn.close()
    
    return updated

if __name__ == '__main__':
    print('=' * 60)
    print('v53 数据修复：amount = close × volume')
    print('=' * 60)
    fix_amount_zero()
