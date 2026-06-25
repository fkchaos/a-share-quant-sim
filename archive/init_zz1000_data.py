#!/usr/bin/env python3
"""
scripts/tools/init_zz1000_data.py — 中证1000成分股数据初始化
====================================================
从腾讯行情接口下载中证1000成分股的日K线数据。

中证1000覆盖了大量小市值股票，更适合v43小市值轮动策略。

用法:
    python scripts/tools/init_zz1000_data.py
    python scripts/tools/init_zz1000_data.py --days 2520  # 下载10年数据
"""
import os
import sys
import time
import sqlite3
import argparse
import urllib.request
import urllib.parse
import json
import csv

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "quant_stocks.db")

# ── 中证1000成分股获取 ──────────────────────────────────

def fetch_zz1000_constituents():
    """
    获取中证1000成分股列表。
    尝试多个数据源：
    1. 从akshare获取中证1000成分股
    2. 从现有daily_kline提取所有活跃股票（兜底）
    """
    # 方法1: 从akshare获取中证1000
    try:
        import akshare as ak
        df = ak.index_stock_cons_csindex(symbol="000852")  # 中证1000
        if df is not None and len(df) > 0:
            codes = df["品种代码"].tolist()
            print(f"  akshare: 中证1000成分股 {len(codes)} 只")
            return codes
    except Exception as e:
        print(f"  akshare 失败: {e}")

    # 方法2: 从现有daily_kline提取所有活跃股票（兜底）
    print("  ⚠️ 使用现有daily_kline中的全A股票（排除科创/北交所）")
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT DISTINCT code FROM daily_kline
        WHERE code NOT LIKE '688%' AND code NOT LIKE '689%'
          AND code NOT LIKE '83%' AND code NOT LIKE '43%'
          AND code NOT LIKE '82%'
        ORDER BY code
    ''')
    codes = [row[0] for row in cursor.fetchall()]
    conn.close()
    print(f"  现有全A股票: {len(codes)} 只")
    return codes


def fetch_stock_name_from_tencent(code):
    """从腾讯行情获取股票名称"""
    prefix = "sh" if code.startswith("6") else "sz"
    url = f"http://qt.gtimg.cn/q={prefix}{code}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = resp.read().decode("gbk", errors="ignore")
        # v_sh600519="1~贵州茅台~600519~1721.00~..."
        if "~" in data:
            parts = data.split("~")
            if len(parts) > 1:
                return parts[1]
    except Exception:
        pass
    return ""


def fetch_kline_from_tencent(code, days=2520):
    """从腾讯行情获取日K线数据"""
    prefix = "sh" if code.startswith("6") else "sz"
    url = f"http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{days},qfq"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
        data = json.loads(raw)
        kline = data.get("data", {}).get(f"{prefix}{code}", {})
        day_data = kline.get("day") or kline.get("qfqday", [])
        if not day_data:
            return []
        records = []
        for row in day_data:
            # [date, open, close, high, low, volume]
            records.append({
                "date": row[0],
                "open": float(row[1]),
                "close": float(row[2]),
                "high": float(row[3]),
                "low": float(row[4]),
                "volume": float(row[5]) if len(row) > 5 else 0,
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
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = resp.read().decode("gbk", errors="ignore")
        parts = data.split("~")
        if len(parts) > 45:
            float_mv_yi = float(parts[45])  # 流通市值（亿元）
            close_price = float(parts[3])   # 当前收盘价
            if float_mv_yi > 0 and close_price > 0:
                return int(float_mv_yi * 1e8 / close_price)
    except Exception:
        pass
    return 0


def upsert_kline(conn, code, date, open_, high, low, close, volume):
    """写入K线数据"""
    conn.execute(
        """INSERT OR REPLACE INTO daily_kline(code,date,open,high,low,close,volume,amount)
           VALUES(?,?,?,?,?,?,?,?)""",
        (code, date, open_, high, low, close, volume, 0),
    )


def upsert_stock_pool(conn, code, name, pool):
    """写入股票池"""
    conn.execute(
        """INSERT OR IGNORE INTO stock_pool_full(code, name, pool, is_active, float_shares)
           VALUES(?,?,?,?,?)""",
        (code, name, pool, 1, 0),
    )


def update_float_shares(conn, code, float_shares):
    """更新流通股本"""
    conn.execute(
        "UPDATE stock_pool_full SET float_shares=? WHERE code=?",
        (float_shares, code),
    )


def main():
    parser = argparse.ArgumentParser(description="中证1000成分股数据初始化")
    parser.add_argument("--days", type=int, default=2520, help="下载天数（默认2520=10年）")
    parser.add_argument("--skip-kline", action="store_true", help="跳过K线下载")
    parser.add_argument("--skip-float", action="store_true", help="跳过流通股本下载")
    args = parser.parse_args()

    print("=" * 60)
    print("中证1000成分股数据初始化")
    print("=" * 60)

    # 1. 获取成分股列表
    print("\n[1/4] 获取中证1000成分股列表...")
    codes = fetch_zz1000_constituents()
    if not codes:
        print("  ❌ 无法获取成分股列表")
        return

    # 2. 获取股票名称
    print(f"\n[2/4] 获取 {len(codes)} 只股票的名称...")
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    name_map = {}
    for i, code in enumerate(codes):
        name = fetch_stock_name_from_tencent(code)
        name_map[code] = name
        upsert_stock_pool(conn, code, name, "full_a")
        if (i + 1) % 100 == 0:
            print(f"  进度: {i+1}/{len(codes)}")
    conn.commit()
    print(f"  ✅ 股票池写入完成: {len(codes)} 只")

    # 3. 下载K线数据
    if not args.skip_kline:
        print(f"\n[3/4] 下载K线数据（{args.days} 天）...")
        total = len(codes)
        success = 0
        for i, code in enumerate(codes):
            records = fetch_kline_from_tencent(code, args.days)
            if records:
                for r in records:
                    upsert_kline(conn, code, r["date"], r["open"], r["high"],
                                 r["low"], r["close"], r["volume"])
                success += 1
            if (i + 1) % 50 == 0:
                conn.commit()
                print(f"  进度: {i+1}/{total} (成功: {success})")
            # 限速：每秒最多10个请求
            if (i + 1) % 10 == 0:
                time.sleep(1)
        conn.commit()
        print(f"  ✅ K线下载完成: {success}/{total} 只")

    # 4. 下载流通股本
    if not args.skip_float:
        print(f"\n[4/4] 下载流通股本数据...")
        for i, code in enumerate(codes):
            float_shares = get_float_shares(code)
            if float_shares > 0:
                update_float_shares(conn, code, float_shares)
            if (i + 1) % 10 == 0:
                time.sleep(1)
        conn.commit()

    # 5. 验证
    print(f"\n{'=' * 60}")
    print("验证结果:")
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
