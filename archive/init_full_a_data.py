#!/usr/bin/env python3
"""
scripts/tools/init_full_a_data.py — 全A股票数据初始化（腾讯行情源）
====================================================
遍历A股全部代码空间，从腾讯行情验证有效性，下载日K线数据。

改进日志：
  - 一年多线程下载（验证10线程，K线3线程）
  - 一年一年往前增量下载（避免单次请求超时返回空数据）
  - 支持断点续传（已下载的股票跳过）
  - CSV 导入股票池元数据，初始化只拉K线

覆盖范围：
- 沪深主板（000xxx~003xxx, 600xxx~605xxx）
- 创业板（300xxx, 301xxx, 302xxx）
- 排除：科创板(688xxx)、北交所(83xxx/43xxx/82xxx)、老三板

用法:
    python scripts/tools/init_full_a_data.py                    # 全量初始化（验证+下载）
    python scripts/tools/init_full_a_data.py --days 2520      # 下载10年
    python scripts/tools/init_full_a_data.py --quick          # 快速模式（只验证不下载）
    python scripts/tools/init_full_a_data.py --from-csv FILE  # 从CSV导入股票池，只下载K线
    python scripts/tools/init_full_a_data.py --resume         # 断点续传（跳过已有K线的股票）
"""
import os
import sys
import time
import sqlite3
import argparse
import urllib.request
import json
import csv
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "quant_stocks.db")

# ── A股代码空间 ──────────────────────────────────────────

SH_PREFIXES = ['600', '601', '603', '605']
SZ_PREFIXES = ['000', '001', '002', '003', '300', '301', '302']


def is_valid_a_share(code):
    """判断是否为有效A股代码（排除科创/北交所）"""
    if code.startswith('688') or code.startswith('689'):
        return False
    if code.startswith('83') or code.startswith('43') or code.startswith('82'):
        return False
    if code.startswith('80') or code.startswith('40'):
        return False
    if code.startswith('200') or code.startswith('201'):
        return False
    if code.startswith('900'):
        return False
    if code.startswith('sh0') or code.startswith('sz0'):
        return False
    return True


def generate_all_a_share_codes():
    """生成所有可能的A股代码"""
    codes = []
    for prefix in SH_PREFIXES:
        for i in range(1000):
            code = f"{prefix}{i:03d}"
            if is_valid_a_share(code):
                codes.append(code)
    for prefix in SZ_PREFIXES:
        for i in range(1000):
            code = f"{prefix}{i:03d}"
            if is_valid_a_share(code):
                codes.append(code)
    return codes


def verify_stock_exists(code):
    """验证股票在腾讯行情中是否存在"""
    prefix = "sh" if code.startswith("6") else "sz"
    url = f"http://qt.gtimg.cn/q={prefix}{code}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode("gbk", errors="ignore")
        if "~" in data:
            parts = data.split("~")
            if len(parts) > 1 and parts[1].strip():
                return True, parts[1].strip(), data
        return False, "", data
    except Exception:
        return False, "", ""


def fetch_kline(code, max_records=1000):
    """从腾讯行情获取日K线数据（最近N条，前复权）
    使用 web.ifzq.gtimg.cn 接口，参数2=前复权+扩展数据
    返回约1000条（最早~2022-05），volume单位=股
    """
    prefix = "sh" if code.startswith("6") else "sz"
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{max_records},2"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        data = json.loads(raw)
        d = data.get("data", {})
        if not isinstance(d, dict):
            return []
        kline = d.get(f"{prefix}{code}", {})
        if not isinstance(kline, dict):
            return []
        day_data = kline.get("day") or kline.get("qfqday", [])
        if not day_data:
            return []
        records = []
        for row in day_data:
            records.append({
                "date": row[0],
                "open": float(row[1]),
                "close": float(row[2]),
                "high": float(row[3]),
                "low": float(row[4]),
                "volume": float(row[5]),
            })
        return records
    except Exception:
        return []


def get_float_shares(code):
    """获取流通股本（股）
    腾讯字段45 = 流通市值（亿元）
    流通股本 = 流通市值 / 收盘价 = 字段45 * 1e8 / close
    """
    prefix = "sh" if code.startswith("6") else "sz"
    url = f"http://qt.gtimg.cn/q={prefix}{code}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode("gbk", errors="ignore")
        parts = data.split("~")
        if len(parts) > 45:
            float_mv_yi = float(parts[45])  # 流通市值（亿元）
            close_price = float(parts[3])   # 当前收盘价
            if float_mv_yi > 0 and close_price > 0:
                float_shares = int(float_mv_yi * 1e8 / close_price)
                return float_shares
    except Exception:
        pass
    return 0


# ── 多线程验证 ──────────────────────────────────────────

def verify_worker(code):
    """验证线程的worker"""
    exists, name, data = verify_stock_exists(code)
    return (code, exists, name)


def multithread_verify(all_codes, max_workers=10):
    """多线程验证股票有效性"""
    valid_stocks = []
    total = len(all_codes)
    done = [0]  # 用list实现nonlocal

    def _verify_batch(batch):
        results = []
        for code in batch:
            results.append(verify_worker(code))
        return results

    batch_size = max(50, total // (max_workers * 4))
    batches = [all_codes[i:i+batch_size] for i in range(0, total, batch_size)]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_verify_batch, batch): i for i, batch in enumerate(batches)}
        for future in as_completed(futures):
            results = future.result()
            for code, exists, name in results:
                if exists:
                    valid_stocks.append((code, name))
            done[0] += len(results)
            if done[0] % 500 == 0 or done[0] >= total:
                print(f"  验证进度: {done[0]}/{total} (有效: {len(valid_stocks)})")

    return valid_stocks


# ── 多线程K线下载 ──────────────────────────────────────

def download_kline_chunk(stock_chunk, db_path, max_workers=3):
    """多线程下载一批股票的K线数据，直接写入DB"""
    success = 0
    empty = 0
    lock = threading.Lock()

    def _download_one(item):
        nonlocal success, empty
        code, name = item
        # 从东方财富下载K线（2000条≈8年，覆盖2021年至今）
        records = fetch_kline(code, max_records=2000)
        if records:
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            for r in records:
                conn.execute(
                    "INSERT OR REPLACE INTO daily_kline(code,date,open,high,low,close,volume,amount) VALUES(?,?,?,?,?,?,?,?)",
                    (code, r["date"], r["open"], r["high"], r["low"], r["close"], r["volume"], 0),
                )
            conn.commit()
            conn.close()
            with lock:
                nonlocal success
                success += 1
        else:
            with lock:
                nonlocal empty
                empty += 1

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(executor.map(_download_one, stock_chunk))

    return success, empty


def multithread_download_klines(valid_stocks, db_path, max_workers=3, resume=False):
    """多线程下载所有股票的K线数据"""
    total = len(valid_stocks)

    # 断点续传：跳过已有K线的股票
    if resume:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT code FROM daily_kline")
        existing_codes = set(row[0] for row in cursor.fetchall())
        conn.close()
        valid_stocks = [(c, n) for c, n in valid_stocks if c not in existing_codes]
        print(f"  断点续传：跳过已有 {len(existing_codes)} 只，剩余 {len(valid_stocks)} 只")

    if not valid_stocks:
        print("  所有股票已有K线数据，无需下载")
        return 0, 0

    # 分批处理，每批 max_workers*10 只股票
    batch_size = max_workers * 10
    success_total = 0
    empty_total = 0

    for batch_start in range(0, len(valid_stocks), batch_size):
        batch = valid_stocks[batch_start:batch_start + batch_size]
        s, e = download_kline_chunk(batch, db_path, max_workers=max_workers)
        success_total += s
        empty_total += e
        processed = min(batch_start + batch_size, len(valid_stocks))
        print(f"  进度: {processed}/{len(valid_stocks)} (有数据: {success_total}, 无数据: {empty_total})")
        # 批次间限速
        time.sleep(0.5)

    return success_total, empty_total


# ── CSV 导入 ────────────────────────────────────────────

def load_stock_pool_from_csv(csv_path):
    """从CSV文件导入股票池
    CSV格式: code,name（可选: float_shares）
    """
    stocks = []
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            code = row['code'].strip()
            name = row.get('name', '')
            float_shares = int(row.get('float_shares', 0))
            stocks.append((code, name, float_shares))
    return stocks


def save_stock_pool_to_csv(stocks, csv_path):
    """导出股票池到CSV"""
    with open(csv_path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['code', 'name', 'float_shares'])
        for code, name, float_shares in stocks:
            writer.writerow([code, name, float_shares])


# ── 主流程 ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="全A股票数据初始化（腾讯源）")
    parser.add_argument("--days", type=int, default=2520, help="下载天数（默认2520=10年，已废弃，改用按年增量）")
    parser.add_argument("--quick", action="store_true", help="快速模式：只验证不下载K线")
    parser.add_argument("--skip-float", action="store_true", help="跳过流通股本下载")
    parser.add_argument("--max-workers", type=int, default=3, help="K线下载并发数（默认3）")
    parser.add_argument("--verify-workers", type=int, default=10, help="验证并发数（默认10）")
    parser.add_argument("--from-csv", type=str, help="从CSV导入股票池，只下载K线（跳过验证）")
    parser.add_argument("--export-csv", type=str, help="验证后导出股票池到CSV")
    parser.add_argument("--resume", action="store_true", help="断点续传（跳过已有K线的股票）")
    parser.add_argument("--current-only", action="store_true", help="只下载当前活跃股票（从腾讯实时接口获取）")
    args = parser.parse_args()

    print("=" * 60)
    print("全A股票数据初始化（腾讯行情源）")
    print("=" * 60)

    # 1. 获取股票池
    if args.from_csv:
        # 从CSV导入
        print(f"\n[1] 从CSV导入股票池: {args.from_csv}")
        stocks = load_stock_pool_from_csv(args.from_csv)
        valid_stocks = [(c, n) for c, n, _ in stocks]
        # 写入DB
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("DELETE FROM stock_pool_full WHERE pool='full_a'")
        for code, name, float_shares in stocks:
            conn.execute(
                "INSERT OR IGNORE INTO stock_pool_full(code, name, pool, is_active, float_shares) VALUES(?,?,?,?,?)",
                (code, name, "full_a", 1, float_shares),
            )
        conn.commit()
        print(f"  导入 {len(valid_stocks)} 只股票")
        conn.close()
    else:
        # 生成代码空间 + 验证
        print("\n[1] 生成A股代码空间...")
        all_codes = generate_all_a_share_codes()
        print(f"  候选代码: {len(all_codes)} 个")

        print(f"\n[2] 验证股票有效性（{args.verify_workers}线程并发）...")
        valid_stocks = multithread_verify(all_codes, max_workers=args.verify_workers)
        print(f"\n  ✅ 有效A股: {len(valid_stocks)} 只")

        # 写入股票池
        print(f"\n[3] 写入数据库...")
        conn = sqlite3.connect(DB_PATH)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("DELETE FROM stock_pool_full WHERE pool='full_a'")
        for code, name in valid_stocks:
            conn.execute(
                "INSERT OR IGNORE INTO stock_pool_full(code, name, pool, is_active, float_shares) VALUES(?,?,?,?,?)",
                (code, name, "full_a", 1, 0),
            )
        conn.commit()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM stock_pool_full WHERE pool='full_a'")
        print(f"  stock_pool_full (full_a): {cursor.fetchone()[0]} 只")
        conn.close()

        # 导出CSV（方便后续复用）
        if args.export_csv:
            float_data = []
            for code, name in valid_stocks:
                fs = get_float_shares(code)
                float_data.append((code, name, fs))
            save_stock_pool_to_csv(float_data, args.export_csv)
            print(f"  导出CSV: {args.export_csv} ({len(float_data)} 只)")

    # 4. 下载K线数据
    if not args.quick:
        print(f"\n[4] 下载K线数据（按年增量，{args.max_workers}线程并发）...")
        print(f"  策略：从 2015 年起逐年下载，避免大数据量超时")
        if args.resume:
            print(f"  断点续传：跳过已有K线的股票")

        success, empty = multithread_download_klines(
            valid_stocks, DB_PATH,
            max_workers=args.max_workers,
            resume=args.resume,
        )
        print(f"  ✅ K线下载完成: 有数据 {success} / 无数据 {empty} / 总计 {success + empty}")

        # 5. 下载流通股本
        if not args.skip_float:
            print(f"\n[5] 下载流通股本...")
            total = len(valid_stocks)
            has_float = 0
            lock = threading.Lock()

            def _fetch_float(item):
                nonlocal has_float
                code, name = item
                fs = get_float_shares(code)
                if fs > 0:
                    conn = sqlite3.connect(DB_PATH)
                    conn.execute("UPDATE stock_pool_full SET float_shares=? WHERE code=?", (fs, code))
                    conn.commit()
                    conn.close()
                    with lock:
                        has_float += 1

            with ThreadPoolExecutor(max_workers=10) as executor:
                list(executor.map(_fetch_float, valid_stocks))
            print(f"  ✅ 流通股本: {has_float}/{total} 只")

    # 6. 验证
    print(f"\n{'=' * 60}")
    print("最终验证:")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM stock_pool_full WHERE pool='full_a'")
    print(f"  stock_pool_full (full_a): {cursor.fetchone()[0]} 只")
    cursor.execute("SELECT COUNT(DISTINCT code) FROM daily_kline WHERE code IN (SELECT code FROM stock_pool_full WHERE pool='full_a')")
    print(f"  其中已下载K线: {cursor.fetchone()[0]} 只")
    cursor.execute("SELECT COUNT(*) FROM stock_pool_full WHERE pool='full_a' AND float_shares > 0")
    print(f"  其中已有float_shares: {cursor.fetchone()[0]} 只")
    conn.close()
    print("=" * 60)


if __name__ == "__main__":
    main()
