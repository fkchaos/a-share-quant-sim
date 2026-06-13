#!/usr/bin/env python3
"""
industry_map_db — 行业分类数据 DB 导入/查询工具

功能：
1. 从 AKShare 拉取全量行业分类（国证 CNINFO）并写入 industry_map 表
2. 提供 DB 查询接口替代 CSV 缓存
3. 兼容旧 CSV 导入

用法：
    python industry_map_db.py --import-csv    # 从已有 CSV 导入
    python industry_map_db.py --fetch         # 从 AKShare 拉取
    python industry_map_db.py --fetch --update # 增量更新（跳过已有）
    python industry_map_db.py --stats         # 查看统计
    python industry_map_db.py --export-csv    # 导出 CSV（兼容旧代码）
"""

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.db import get_conn, DB_PATH


def init_industry_table():
    """建表（幂等，core/db.py init_db 已包含）"""
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS industry_map (
                code        TEXT PRIMARY KEY,
                industry    TEXT NOT NULL DEFAULT '',
                industry_m  TEXT NOT NULL DEFAULT '',
                industry_s  TEXT NOT NULL DEFAULT '',
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            ) WITHOUT ROWID
        """)


def import_from_csv(csv_path):
    """从 CSV 文件导入到 DB"""
    if not os.path.exists(csv_path):
        print(f"❌ CSV 文件不存在: {csv_path}")
        return 0

    df = pd.read_csv(csv_path, dtype={"code": str})
    count = 0
    with get_conn() as conn:
        for _, row in df.iterrows():
            code = str(row["code"]).zfill(6)
            industry = str(row.get("industry", ""))
            conn.execute("""
                INSERT OR REPLACE INTO industry_map (code, industry, industry_m, industry_s)
                VALUES (?, ?, ?, ?)
            """, (code, industry, "", ""))
            count += 1

    print(f"✅ 从 CSV 导入 {count} 条行业分类")
    return count


def fetch_one(code):
    """获取单只股票的行业分类（取最新一条）"""
    import akshare as ak
    try:
        df = ak.stock_industry_change_cninfo(symbol=code)
        if df is not None and len(df) > 0:
            df = df.sort_values("变更日期", ascending=False)
            latest = df.iloc[0]
            return (
                code,
                str(latest.get("行业大类", "")),
                str(latest.get("行业中类", "")),
                str(latest.get("行业次类", "")),
            )
    except Exception:
        pass
    return None


def fetch_all(codes, max_workers=4, skip_existing=True):
    """多线程获取所有股票的行业分类"""
    if skip_existing:
        with get_conn() as conn:
            existing = {r["code"] for r in conn.execute("SELECT code FROM industry_map WHERE industry!=''").fetchall()}
        codes = [c for c in codes if c not in existing]
        print(f"跳过已有 {len(existing)} 只，需获取 {len(codes)} 只")
    else:
        # 清空重新导入
        with get_conn() as conn:
            conn.execute("DELETE FROM industry_map")

    results = []
    failed = []
    total = len(codes)
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_one, code): code for code in codes}
        done = 0
        for future in as_completed(futures):
            done += 1
            result = future.result()
            if result:
                results.append(result)
            else:
                failed.append(futures[future])

            if done % 50 == 0 or done == total:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                print(f"  [{done}/{total}] {rate:.1f} 只/秒, 成功 {len(results)}")

    # 写入 DB
    with get_conn() as conn:
        for code, ind, ind_m, ind_s in results:
            conn.execute("""
                INSERT OR REPLACE INTO industry_map (code, industry, industry_m, industry_s)
                VALUES (?, ?, ?, ?)
            """, (code, ind, ind_m, ind_s))

    elapsed = time.time() - t0
    print(f"✅ 完成: {len(results)}/{total} 只成功 ({elapsed:.1f}s)")
    if failed:
        print(f"   ⚠️ {len(failed)} 只失败")

    return len(results)


def get_stats():
    """查看统计"""
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM industry_map WHERE industry!=''").fetchone()[0]
        total_all = conn.execute("SELECT COUNT(*) FROM industry_map").fetchone()[0]
        ind_count = conn.execute("SELECT COUNT(DISTINCT industry) FROM industry_map WHERE industry!=''").fetchone()[0]

        print(f"DB路径: {DB_PATH}")
        print(f"有行业分类: {total} 只")
        print(f"总记录数: {total_all} 只")
        print(f"行业数: {ind_count}")

        # Top 15 行业
        rows = conn.execute("""
            SELECT industry, COUNT(*) as cnt
            FROM industry_map WHERE industry!=''
            GROUP BY industry ORDER BY cnt DESC LIMIT 15
        """).fetchall()
        print(f"\n行业分布 (top 15):")
        for r in rows:
            print(f"  {r[0]}: {r[1]} 只")


def export_csv(csv_path):
    """导出 CSV（兼容旧代码）"""
    with get_conn() as conn:
        rows = conn.execute("SELECT code, industry FROM industry_map WHERE industry!=''").fetchall()
    df = pd.DataFrame(rows, columns=["code", "industry"])
    df.to_csv(csv_path, index=False)
    print(f"✅ 导出 {len(df)} 条到 {csv_path}")


def main():
    parser = argparse.ArgumentParser(description="行业分类 DB 工具")
    parser.add_argument("--import-csv", action="store_true", help="从 CSV 导入")
    parser.add_argument("--fetch", action="store_true", help="从 AKShare 拉取")
    parser.add_argument("--update", action="store_true", help="增量更新（跳过已有）")
    parser.add_argument("--stats", action="store_true", help="查看统计")
    parser.add_argument("--export-csv", action="store_true", help="导出 CSV")
    parser.add_argument("--workers", type=int, default=4, help="线程数")
    args = parser.parse_args()

    init_industry_table()

    if args.import_csv:
        csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "industry_map.csv")
        import_from_csv(csv_path)

    if args.fetch:
        # 从 DB 的 daily_kline 获取所有股票代码
        with get_conn() as conn:
            codes = [r["code"] for r in conn.execute("SELECT DISTINCT code FROM daily_kline").fetchall()]
        print(f"📡 从 AKShare 获取 {len(codes)} 只股票的行业分类...")
        fetch_all(codes, max_workers=args.workers, skip_existing=args.update)

    if args.stats:
        get_stats()

    if args.export_csv:
        csv_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "industry_map.csv")
        export_csv(csv_path)

    if not any([args.import_csv, args.fetch, args.stats, args.export_csv]):
        parser.print_help()


if __name__ == "__main__":
    main()
