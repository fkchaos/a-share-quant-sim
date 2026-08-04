#!/usr/bin/env python3
"""构建 Golden Dataset — 从生产库提取标准输入数据

从 quant_stocks.db 提取固定股票池 + 固定时间段的数据，
写入 tests/golden/golden_stocks.db，作为回归测试的标准输入。

用法：
    python tests/golden/build_golden.py
"""
import os
import sys
import sqlite3
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

GOLDEN_DIR = os.path.dirname(os.path.abspath(__file__))
PROD_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data', 'quant_stocks.db')
GOLDEN_DB = os.path.join(GOLDEN_DIR, 'golden_stocks.db')

# ── 标准测试股票（10只，覆盖不同行业/市值/特征） ──
GOLDEN_CODES = [
    '000001',  # 平安银行（金融）
    '000002',  # 万科A（地产）
    '000009',  # 中国宝安（材料）
    '000012',  # 南玻A（工业）
    '000021',  # 深科技（科技）
    '600000',  # 浦发银行（金融）
    '600036',  # 招商银行（金融）
    '600519',  # 贵州茅台（消费）
    '601318',  # 中国平安（金融）
    '603288',  # 海天味业（消费）
]

# ── 标准时间段：2021全年 ──
GOLDEN_START = '2021-01-01'
GOLDEN_END = '2021-12-31'


def build_golden_db():
    """从生产库提取标准数据集"""
    print(f"源数据库: {PROD_DB}")
    print(f"目标数据库: {GOLDEN_DB}")
    print(f"测试股票: {GOLDEN_CODES}")
    print(f"时间范围: {GOLDEN_START} ~ {GOLDEN_END}")
    print()

    # 删除旧的
    if os.path.exists(GOLDEN_DB):
        os.remove(GOLDEN_DB)
        print("已删除旧的 golden_stocks.db")

    conn_prod = sqlite3.connect(PROD_DB, timeout=30)
    conn_gold = sqlite3.connect(GOLDEN_DB)

    # ── 1. 复制 stock_pool ──
    conn_gold.execute("""CREATE TABLE stock_pool (
        code TEXT, name TEXT, board TEXT, pool TEXT, is_active INTEGER, float_shares INTEGER,
        PRIMARY KEY (code)
    )""")
    for code in GOLDEN_CODES:
        cursor = conn_prod.execute(
            "SELECT code, name, board, pool, is_active, float_shares FROM stock_pool WHERE code=?",
            (code,)
        )
        for row in cursor.fetchall():
            conn_gold.execute(
                "INSERT INTO stock_pool VALUES (?,?,?,?,?,?)", row
            )
    n_pool = conn_gold.execute("SELECT COUNT(*) FROM stock_pool").fetchone()[0]
    print(f"✅ stock_pool: {n_pool} 条")

    # ── 2. 复制 daily_kline ──
    conn_gold.execute("""CREATE TABLE daily_kline (
        code TEXT, date TEXT, open REAL, high REAL, low REAL, close REAL,
        volume REAL, amount REAL,
        PRIMARY KEY (code, date)
    )""")
    placeholders = ','.join(['?'] * len(GOLDEN_CODES))
    cursor = conn_prod.execute(
        f"""SELECT code, date, open, high, low, close, volume, amount
            FROM daily_kline
            WHERE code IN ({placeholders})
            AND date >= ? AND date <= ?
            ORDER BY code, date""",
        GOLDEN_CODES + [GOLDEN_START, GOLDEN_END]
    )
    rows = cursor.fetchall()
    conn_gold.executemany(
        "INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?)", rows
    )
    n_kline = conn_gold.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
    print(f"✅ daily_kline: {n_kline} 条")

    # ── 3. 复制 stock_pool_zz1800（仅测试股票） ──
    conn_gold.execute("""CREATE TABLE stock_pool_zz1800 (
        code TEXT PRIMARY KEY
    )""")
    for code in GOLDEN_CODES:
        conn_gold.execute(
            "INSERT OR IGNORE INTO stock_pool_zz1800 (code) VALUES (?)", (code,)
        )
    n_zz = conn_gold.execute("SELECT COUNT(*) FROM stock_pool_zz1800").fetchone()[0]
    print(f"✅ stock_pool_zz1800: {n_zz} 条")

    # ── 4. 复制 industry_map ──
    conn_gold.execute("""CREATE TABLE industry_map (
        code TEXT PRIMARY KEY, industry TEXT, industry_m TEXT, industry_s TEXT
    )""")
    for code in GOLDEN_CODES:
        cursor = conn_prod.execute(
            "SELECT code, industry, industry_m, industry_s FROM industry_map WHERE code=?", (code,)
        )
        row = cursor.fetchone()
        if row:
            conn_gold.execute(
                "INSERT OR REPLACE INTO industry_map VALUES (?,?,?,?)", row
            )
    n_ind = conn_gold.execute("SELECT COUNT(*) FROM industry_map").fetchone()[0]
    print(f"✅ industry_map: {n_ind} 条")

    # ── 5. 复制 indicators（如果存在） ──
    try:
        conn_gold.execute("""CREATE TABLE indicators (
            code TEXT, date TEXT, turnover_rate REAL, float_shares REAL,
            PRIMARY KEY (code, date)
        )""")
        placeholders = ','.join(['?'] * len(GOLDEN_CODES))
        cursor = conn_prod.execute(
            f"""SELECT code, date, turnover_rate, float_shares
                FROM indicators
                WHERE code IN ({placeholders})
                AND date >= ? AND date <= ?
                ORDER BY code, date""",
            GOLDEN_CODES + [GOLDEN_START, GOLDEN_END]
        )
        rows = cursor.fetchall()
        if rows:
            conn_gold.executemany(
                "INSERT INTO indicators VALUES (?,?,?,?)", rows
            )
        n_ind2 = conn_gold.execute("SELECT COUNT(*) FROM indicators").fetchone()[0]
        print(f"✅ indicators: {n_ind2} 条")
    except Exception:
        print("⚠️ indicators 表不存在，跳过")

    conn_gold.commit()
    conn_prod.close()
    conn_gold.close()

    # 统计
    size_kb = os.path.getsize(GOLDEN_DB) / 1024
    print(f"\n📦 golden_stocks.db: {size_kb:.1f} KB")
    print(f"   {len(GOLDEN_CODES)} 只股票 × {GOLDEN_START}~{GOLDEN_END}")
    print(f"   可作为回归测试的标准输入数据")


if __name__ == '__main__':
    build_golden_db()
