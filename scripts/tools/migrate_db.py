"""
migrate_db.py — 将旧的单库 quant.db 拆分为双库

用法:
  python scripts/tools/migrate_db.py              # 迁移（安全，不删旧库）
  python scripts/tools/migrate_db.py --cleanup    # 迁移后删除旧库
  python scripts/tools/migrate_db.py --dry-run    # 只打印统计，不迁移

原理:
  旧 quant.db → 新 quant_stocks.db (stock_pool, daily_kline, indicators, industry_map)
               → 新 quant_accounts.db (account, holdings, trade_log)
"""
import os
import sys
import sqlite3
import shutil
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
OLD_DB = os.path.join(DATA_DIR, "quant.db")
STOCKS_DB = os.path.join(DATA_DIR, "quant_stocks.db")
ACCOUNTS_DB = os.path.join(DATA_DIR, "quant_accounts.db")

STOCK_TABLES = ["stock_pool", "daily_kline", "indicators", "industry_map"]
ACCOUNT_TABLES = ["account", "holdings", "trade_log"]


def table_exists(conn, table):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def get_row_count(conn, table):
    try:
        row = conn.execute(f"SELECT COUNT(*) as n FROM {table}").fetchone()
        return row["n"] if row else 0
    except Exception:
        return 0


def migrate_table(src_conn, dst_conn, table):
    """迁移单个表的数据"""
    if not table_exists(src_conn, table):
        print(f"    跳过 {table}（旧库中不存在）")
        return 0

    # 获取表结构
    cursor = src_conn.execute(f"SELECT sql FROM sqlite_master WHERE type='name' AND name='{table}'")
    create_sql = cursor.fetchone()
    if not create_sql:
        # 尝试直接复制
        rows = src_conn.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"    {table}: 0 行（空表）")
            return 0
        # 获取列名
        cols = [desc[0] for desc in src_conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
        placeholders = ",".join(["?"] * len(cols))
        dst_conn.execute(f"DELETE FROM {table}")  # 清空目标
        dst_conn.executemany(f"INSERT INTO {table} VALUES({placeholders})", rows)
        print(f"    {table}: {len(rows)} 行（直接复制）")
        return len(rows)

    # 建表
    dst_conn.execute(create_sql[0])

    # 复制数据
    rows = src_conn.execute(f"SELECT * FROM {table}").fetchall()
    if not rows:
        print(f"    {table}: 0 行")
        return 0

    cols = [desc[0] for desc in src_conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
    placeholders = ",".join(["?"] * len(cols))
    dst_conn.executemany(f"INSERT INTO {table} VALUES({placeholders})", rows)
    print(f"    {table}: {len(rows)} 行")
    return len(rows)


def migrate(dry_run=False, cleanup=False):
    if not os.path.exists(OLD_DB):
        print(f"❌ 旧数据库不存在: {OLD_DB}")
        print("   如果是首次部署，直接运行 init_project.py 即可")
        return

    # 统计旧库
    print("=" * 60)
    print("旧库统计:")
    old_conn = sqlite3.connect(OLD_DB)
    old_conn.row_factory = sqlite3.Row
    total_old = 0
    for t in STOCK_TABLES + ACCOUNT_TABLES:
        n = get_row_count(old_conn, t)
        print(f"  {t}: {n} 行")
        total_old += n
    old_size = os.path.getsize(OLD_DB) / 1024 / 1024
    print(f"  总大小: {old_size:.1f} MB")
    old_conn.close()

    if dry_run:
        print("\n[DRY RUN] 跳过实际迁移")
        return

    # 检查目标库是否已存在
    if os.path.exists(STOCKS_DB) or os.path.exists(ACCOUNTS_DB):
        print(f"\n⚠️  目标库已存在，跳过迁移")
        print(f"   删除后重试: rm {STOCKS_DB} {ACCOUNTS_DB}")
        return

    print("\n开始迁移...")

    # 迁移股票库
    print("\n📦 股票库 →", os.path.basename(STOCKS_DB))
    old_conn = sqlite3.connect(OLD_DB)
    new_conn = sqlite3.connect(STOCKS_DB)
    new_conn.execute("PRAGMA journal_mode=WAL")
    total_stocks = 0
    for t in STOCK_TABLES:
        total_stocks += migrate_table(old_conn, new_conn, t)
    new_conn.commit()
    new_conn.close()

    # 迁移账户库
    print("\n💰 账户库 →", os.path.basename(ACCOUNTS_DB))
    new_conn = sqlite3.connect(ACCOUNTS_DB)
    new_conn.execute("PRAGMA journal_mode=WAL")
    total_accounts = 0
    for t in ACCOUNT_TABLES:
        total_accounts += migrate_table(old_conn, new_conn, t)
    new_conn.commit()
    new_conn.close()
    old_conn.close()

    # 验证
    print("\n验证:")
    stocks_conn = sqlite3.connect(STOCKS_DB)
    stocks_conn.row_factory = sqlite3.Row
    accounts_conn = sqlite3.connect(ACCOUNTS_DB)
    accounts_conn.row_factory = sqlite3.Row

    verify_stocks = sum(get_row_count(stocks_conn, t) for t in STOCK_TABLES)
    verify_accounts = sum(get_row_count(accounts_conn, t) for t in ACCOUNT_TABLES)
    stocks_conn.close()
    accounts_conn.close()

    stocks_size = os.path.getsize(STOCKS_DB) / 1024 / 1024
    accounts_size = os.path.getsize(ACCOUNTS_DB) / 1024 / 1024

    print(f"  股票库: {verify_stocks} 行, {stocks_size:.1f} MB")
    print(f"  账户库: {verify_accounts} 行, {accounts_size:.1f} MB")

    if verify_stocks + verify_accounts == total_old:
        print(f"\n✅ 迁移成功！（{total_old} 行全部迁移）")
    else:
        print(f"\n⚠️  行数不匹配：旧 {total_old} 行，新 {verify_stocks + verify_accounts} 行")

    if cleanup:
        backup_path = OLD_DB + f".bak.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        shutil.move(OLD_DB, backup_path)
        print(f"🗑️  旧库已删除（备份: {backup_path}）")
    else:
        print(f"\n💡 确认无误后，手动删除旧库: rm {OLD_DB}")
        print(f"   或重新运行加 --cleanup 参数")


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    cleanup = "--cleanup" in sys.argv
    migrate(dry_run=dry_run, cleanup=cleanup)
