#!/usr/bin/env python3
"""
init_project.py — 项目初始化（从零开始）

用法:
  python scripts/tools/init_project.py              # 完整初始化（建表 + 股票池 + K线数据 + 账户）
  python scripts/tools/init_project.py --db-only    # 只建表
  python scripts/tools/init_project.py --pool-only  # 只更新股票池
  python scripts/tools/init_project.py --kline-only  # 只下载K线（需先有股票池）
  python scripts/tools/init_project.py --accounts   # 只初始化账户

依赖: pip install pandas numpy requests
"""
import sys, os, time, asyncio, argparse
from datetime import datetime

# 确保项目根目录在 path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))
sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("BACKTEST_DATA_DIR", "/root/data")

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
os.makedirs(DATA_DIR, exist_ok=True)


def step_init_db():
    """建表"""
    from core.db import init_db
    init_db()
    print()


def step_init_pool():
    """获取中证800成分股并写入 stock_pool"""
    import requests, pandas as pd
    from core.db import upsert_stock

    print("📋 获取中证800成分股列表...")

    # 东方财富中证800成分股接口
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    all_stocks = []
    page = 1
    while True:
        params = {
            "pn": page, "pz": 50, "po": 1, "np": 1, "fltt": 2, "invt": 2,
            "fid": "f3", "fs": "b:M0080",
            "fields": "f12,f14,f2,f15,f16,f17,f18"
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            data = resp.json()
            if not data.get("data") or not data["data"].get("diff"):
                break
            items = data["data"]["diff"]
            if not items:
                break
            for item in items:
                code = item.get("f12", "")
                name = item.get("f14", "")
                if code and name:
                    # 判断板块
                    if code.startswith("688"):
                        board = "kc"
                    elif code.startswith("30"):
                        board = "cy"
                    elif code.startswith("60") or code.startswith("9"):
                        board = "sh"
                    else:
                        board = "sz"
                    all_stocks.append({"code": code, "name": name, "board": board})
            if len(items) < 50:
                break
            page += 1
            time.sleep(0.3)
        except Exception as e:
            print(f"  ⚠️ 第{page}页出错: {e}")
            break

    if not all_stocks:
        print("  ❌ 未获取到成分股，使用内置备用列表")
        # 备用：从本地文件读取
        pool_file = os.path.join(DATA_DIR, "zz800_constituents.csv")
        if os.path.exists(pool_file):
            df = pd.read_csv(pool_file)
            all_stocks = df.to_dict("records")
        else:
            print("  ❌ 无可用股票列表，请检查网络后重试")
            return False

    # 去重
    seen = set()
    unique = []
    for s in all_stocks:
        if s["code"] not in seen:
            seen.add(s["code"])
            unique.append(s)
    all_stocks = unique

    print(f"  共 {len(all_stocks)} 只成分股，写入 stock_pool...")

    # 写入 DB
    for s in all_stocks:
        upsert_stock(s["code"], name=s["name"], board=s["board"], pool="zz800")

    print(f"  ✅ stock_pool 已写入 {len(all_stocks)} 只股票")
    return True


def step_init_kline(days=30):
    """下载日K线数据"""
    from core.db import get_all_codes, upsert_kline_batch, get_stock_name_map
    from scripts.tools.update_daily_data import fetch_tencent_kline

    codes = get_all_codes()
    if not codes:
        print("  ❌ stock_pool 无股票，请先运行 --pool-only")
        return False

    print(f"🔄 下载 {len(codes)} 只股票的近 {days} 日K线...")

    t0 = time.time()
    all_records = []
    ok_count = 0
    fail_count = 0
    name_map = get_stock_name_map()

    for i, code in enumerate(codes):
        try:
            df = fetch_tencent_kline(code, days=days)
            if df is not None and len(df) > 0:
                for date_idx, row in df.iterrows():
                    date_str = str(date_idx)[:10]
                    all_records.append((
                        code, date_str,
                        float(row.get("open", 0) or 0),
                        float(row.get("high", 0) or 0),
                        float(row.get("low", 0) or 0),
                        float(row.get("close", 0) or 0),
                        float(row.get("volume", 0) or 0),
                        float(row.get("amount", 0) or 0),
                    ))
                ok_count += 1
            else:
                fail_count += 1
        except Exception:
            fail_count += 1

        if (i + 1) % 50 == 0:
            print(f"  进度: {i+1}/{len(codes)} (成功{ok_count} 失败{fail_count})")

        time.sleep(0.1)  # 避免请求过快

    if all_records:
        upsert_kline_batch(all_records)

    t_total = time.time() - t0
    print(f"  ✅ K线数据: {ok_count} 只股票, {len(all_records)} 条记录, {t_total:.1f}s (失败{fail_count})")
    return True


def step_init_accounts():
    """初始化3个模拟账户"""
    from core.db import upsert_account

    accounts = [
        (1, "v11b", 200000, "v11b"),
        (2, "v27", 100000, "v27"),
        (3, "v20c", 100000, "v20c"),
    ]
    for aid, name, capital, strategy in accounts:
        upsert_account(account_id=aid, name=name, cash=capital, initial_capital=capital, strategy=strategy)
        print(f"  ✅ 账户{aid}: {name} 初始资金 ¥{capital:,}")

    print()


def main():
    parser = argparse.ArgumentParser(description="项目初始化")
    parser.add_argument("--db-only", action="store_true", help="只建表")
    parser.add_argument("--pool-only", action="store_true", help="只更新股票池")
    parser.add_argument("--kline-only", action="store_true", help="只下载K线")
    parser.add_argument("--accounts", action="store_true", help="只初始化账户")
    parser.add_argument("--days", type=int, default=30, help="K线下载天数(默认30)")
    args = parser.parse_args()

    print("=" * 60)
    print(f"🚀 项目初始化 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    print()

    # 如果没有指定特定步骤，执行完整初始化
    full_init = not (args.db_only or args.pool_only or args.kline_only or args.accounts)

    if full_init or args.db_only:
        print("📦 Step 1: 建表...")
        step_init_db()

    if full_init or args.pool_only:
        print("📦 Step 2: 获取中证800成分股...")
        if not step_init_pool():
            print("  ⚠️ 股票池初始化失败，跳过K线下载")
            if args.pool_only:
                return

    if full_init or args.kline_only:
        print(f"📦 Step 3: 下载日K线 ({args.days}天)...")
        step_init_kline(days=args.days)

    if full_init or args.accounts:
        print("📦 Step 4: 初始化账户...")
        step_init_accounts()

    if full_init:
        print("=" * 60)
        print("✅ 初始化完成！")
        print()
        print("下一步:")
        print("  1. 跑回测: python scripts/backtest/run_backtest.py --strategy v27")
        print("  2. 跑模拟盘: python scripts/sim/account_runner.py --strategy v27 intraday_signal")
        print("  3. 查看账户: python scripts/tools/cli.py account 2")
        print("=" * 60)


if __name__ == "__main__":
    main()
