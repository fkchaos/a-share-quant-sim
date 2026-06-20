#!/usr/bin/env python3
"""
init_project.py — 项目初始化（从零开始）

用法:
  python scripts/tools/init_project.py              # 完整初始化（建表 + 股票池 + K线数据 + 账户）
  python scripts/tools/init_project.py --db-only    # 只建表
  python scripts/tools/init_project.py --pool-only  # 只更新股票池
  python scripts/tools/init_project.py --kline-only  # 只下载K线（需先有股票池）
  python scripts/tools/init_project.py --accounts         # 只初始化账户
  python scripts/tools/init_project.py --accounts --force # 强制重建账户（清空已有数据）

依赖: pip install pandas numpy requests
"""
import sys, os, time, asyncio, argparse
from datetime import datetime

# 确保项目根目录在 path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(os.path.dirname(SCRIPT_DIR))

os.environ.setdefault("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
os.makedirs(DATA_DIR, exist_ok=True)

def step_init_db():
    """建表"""
    from core.db import init_db
    init_db()
    print()

def step_init_pool():
    """从内置 CSV 获取中证800成分股并写入 stock_pool"""
    import pandas as pd
    from core.db import upsert_stock

    csv_path = os.path.join(PROJECT_ROOT, "data", "zz800_constituents.csv")
    if not os.path.exists(csv_path):
        print(f"  ❌ 找不到成分股文件: {csv_path}")
        return False

    df = pd.read_csv(csv_path)
    print(f"📋 从内置CSV加载中证800成分股: {len(df)} 只")

    # 统一code格式为6位数字字符串
    df['code'] = df['code'].astype(str).str.zfill(6)

    # 判断板块
    def _board(code):
        if code.startswith("688"):
            return "kc"
        elif code.startswith("30"):
            return "cy"
        elif code.startswith("60") or code.startswith("9"):
            return "sh"
        else:
            return "sz"

    df['board'] = df['code'].apply(_board)

    # 写入 DB
    for _, row in df.iterrows():
        upsert_stock(str(row['code']), name=str(row['name']), board=str(row['board']), pool="zz800")

    print(f"  ✅ stock_pool 已写入 {len(df)} 只股票")
    return True

def step_init_kline(start_year=2020):
    """下载日K线数据（并发，腾讯接口单次最多2000天，分段下载确保从start_year开始）"""
    from core.db import get_all_codes, upsert_kline_batch, get_stock_name_map
    from scripts.tools.update_daily_data import fetch_tencent_kline
    import asyncio
    from datetime import date, timedelta

    # 优先从 stock_pool 获取代码
    from core.db import get_stock_pool
    pool = get_stock_pool()
    codes = [s["code"] for s in pool]
    if not codes:
        codes = get_all_codes()
    if not codes:
        print("  ❌ stock_pool 无股票，请先运行 --pool-only")
        return False

    # 腾讯接口单次最多2000天，分段下载覆盖从start_year至今
    MAX_CHUNK = 2000  # 单次请求上限
    today = date.today()
    start_date = date(start_year, 1, 1)

    # 计算分段：从后往前每MAX_CHUNK天一段，确保每段days_param不超过MAX_CHUNK
    # fetch_tencent_kline(code, days) 中 days = 从今天往前推的天数
    chunks = []
    remaining_end = today  # 当前段终点（今天开始）
    while remaining_end > start_date:
        # 当前段最多往回走MAX_CHUNK-1天
        chunk_start = max(start_date, remaining_end - timedelta(days=MAX_CHUNK - 1))
        days_param = (today - chunk_start).days
        if days_param <= 0:
            break
        days_param = min(days_param, MAX_CHUNK)  # 不超过上限
        if not chunks or chunks[-1][0] != days_param:
            chunks.append((days_param, chunk_start))
        remaining_end = chunk_start - timedelta(days=1)

    chunks.reverse()  # 从早到晚（days从小到大）
    print(f"🔄 下载 {len(codes)} 只股票 {start_year}年1月1日至今的K线")
    print(f"   分段: {len(chunks)} 段 (腾讯接口单次最多{MAX_CHUNK}天, 并发=30)")

    t0 = time.time()
    all_records = []
    ok_count = 0
    fail_count = 0
    name_map = get_stock_name_map()
    CONCURRENCY = 30
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def fetch_one(code, days_param):
        nonlocal ok_count, fail_count
        async with semaphore:
            loop = asyncio.get_event_loop()
            try:
                df = await loop.run_in_executor(None, fetch_tencent_kline, code, days_param)
                if df is not None and len(df) > 0:
                    records = []
                    for date_idx, row in df.iterrows():
                        date_str = str(date_idx)[:10]
                        records.append((
                            code, date_str,
                            float(row.get("open", 0) or 0),
                            float(row.get("high", 0) or 0),
                            float(row.get("low", 0) or 0),
                            float(row.get("close", 0) or 0),
                            float(row.get("volume", 0) or 0),
                            float(row.get("amount", 0) or 0),
                        ))
                    return records
                else:
                    return []
            except Exception:
                return []

    async def run_all():
        tasks = []
        # 每段分别请求，最后去重
        for days_param, chunk_start in chunks:
            for code in codes:
                tasks.append((code, fetch_one(code, days_param)))

        results = await asyncio.gather(*[t[1] for t in tasks])

        # 按(code, date)去重合并
        seen = set()
        for records in results:
            for rec in records:
                key = (rec[0], rec[1])  # (code, date)
                if key not in seen:
                    seen.add(key)
                    all_records.append(rec)

    asyncio.run(run_all())

    # 统计成功股票数
    ok_codes = set(r[0] for r in all_records)
    ok_count = len(ok_codes)
    fail_count = len(codes) - ok_count

    if all_records:
        # amount 单位检查：DB 统一为元（非分）
        # amount / volume 应 ≈ close（允许 ±30% 误差）
        bad_amount = 0
        for rec in all_records:
            code, date_str, o, h, l, c, v, a = rec
            if v > 0 and c > 0:
                ratio = a / (v * c)
                if ratio < 0.5 or ratio > 2.0:
                    bad_amount += 1
                    if bad_amount <= 3:
                        print(f"  ⚠️ amount 异常: {code} {date_str} amount={a:.0f} volume={v:.0f} close={c:.2f} ratio={ratio:.2f}")
        if bad_amount > 0:
            print(f"  ⚠️ 共 {bad_amount} 条 amount 异常记录（已跳过）")
            all_records = [r for r in all_records
                          if not (r[6] > 0 and r[5] > 0 and not (0.5 <= r[7] / (r[6] * r[5]) <= 2.0))]

        upsert_kline_batch(all_records)

    t_total = time.time() - t0
    dates = sorted(set(r[1] for r in all_records))
    earliest = dates[0] if dates else "无"
    latest = dates[-1] if dates else "无"
    print(f"  ✅ K线数据: {ok_count} 只股票, {len(all_records)} 条记录")
    print(f"     日期范围: {earliest} ~ {latest}")
    print(f"     耗时: {t_total:.1f}s (失败{fail_count})")
    return True

DEFAULT_INDICES = [
    ("sh000001", "上证指数"),
    ("sz399001", "深证成指"),
    ("sz399006", "创业板指"),
]

def step_init_indices(start_year=2020):
    """下载指数K线数据"""
    from core.db import upsert_index_batch
    from scripts.tools.update_daily_data import fetch_tencent_kline
    import asyncio
    from datetime import date, timedelta

    MAX_CHUNK = 2000
    today = date.today()
    start_date = date(start_year, 1, 1)

    # 计算分段
    chunks = []
    remaining_end = today
    while remaining_end > start_date:
        chunk_start = max(start_date, remaining_end - timedelta(days=MAX_CHUNK - 1))
        days_param = (today - chunk_start).days
        if days_param <= 0:
            break
        days_param = min(days_param, MAX_CHUNK)
        if not chunks or chunks[-1][0] != days_param:
            chunks.append((days_param, chunk_start))
        remaining_end = chunk_start - timedelta(days=1)
    chunks.reverse()

    print(f"🔄 下载 {len(DEFAULT_INDICES)} 个指数 {start_year}年1月1日至今的K线")
    print(f"   分段: {len(chunks)} 段 (并发=30)")

    t0 = time.time()
    all_records = []
    ok_count = 0
    fail_count = 0
    CONCURRENCY = 30
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def fetch_one(idx_code, days_param):
        nonlocal ok_count, fail_count
        async with semaphore:
            loop = asyncio.get_event_loop()
            try:
                df = await loop.run_in_executor(None, fetch_tencent_kline, idx_code, days_param)
                if df is not None and len(df) > 0:
                    records = []
                    for date_idx, row in df.iterrows():
                        date_str = str(date_idx)[:10]
                        records.append((
                            idx_code, date_str,
                            float(row.get("open", 0) or 0),
                            float(row.get("high", 0) or 0),
                            float(row.get("low", 0) or 0),
                            float(row.get("close", 0) or 0),
                            float(row.get("volume", 0) or 0),
                            float(row.get("amount", 0) or 0),
                        ))
                    return records
                else:
                    return []
            except Exception:
                return []

    async def run_all():
        tasks = []
        for days_param, chunk_start in chunks:
            for idx_code, _ in DEFAULT_INDICES:
                tasks.append((idx_code, fetch_one(idx_code, days_param)))
        results = await asyncio.gather(*[t[1] for t in tasks])
        seen = set()
        for records in results:
            for rec in records:
                key = (rec[0], rec[1])
                if key not in seen:
                    seen.add(key)
                    all_records.append(rec)

    asyncio.run(run_all())

    ok_codes = set(r[0] for r in all_records)
    ok_count = len(ok_codes)
    fail_count = len(DEFAULT_INDICES) - ok_count

    if all_records:
        upsert_index_batch(all_records)

    t_total = time.time() - t0
    dates = sorted(set(r[1] for r in all_records))
    earliest = dates[0] if dates else "无"
    latest = dates[-1] if dates else "无"
    print(f"  ✅ 指数K线: {ok_count}/{len(DEFAULT_INDICES)} 成功, {len(all_records)} 条记录")
    print(f"     日期范围: {earliest} ~ {latest}")
    print(f"     耗时: {t_total:.1f}s (失败{fail_count})")
    return True


def step_init_accounts(force=False):
    """初始化模拟账户（空账户，策略由用户自行绑定）"""
    from core.db import create_account, list_accounts, get_conn

    existing = list_accounts()
    existing_ids = {a["id"] for a in existing}

    # 如果要强制重建，先清空
    if force:
        with get_conn("account") as conn:
            conn.execute("DELETE FROM holdings")
            conn.execute("DELETE FROM trade_log")
            conn.execute("DELETE FROM account")
        print("  ⚠️ 已清空所有账户、持仓、交易记录")
        existing_ids = set()

    accounts = [
        (1, "账户1", 200000),
        (2, "账户2", 100000),
        (3, "账户3", 100000),
    ]
    for aid, name, capital in accounts:
        if aid not in existing_ids:
            create_account(aid, name=name, cash=capital, initial_capital=capital, strategy="")
            print(f"  ✅ 账户{aid}: {name} 初始资金 ¥{capital:,}（未绑定策略）")
        else:
            print(f"  ⏭️ 账户{aid} 已存在，跳过")

    print()
    print("  提示: 使用以下命令绑定策略:")
    print("    python scripts/sim/account_runner.py switch --account-id 1 --strategy v11b")
    print("    python scripts/sim/account_runner.py switch --account-id 2 --strategy v27")

def main():
    parser = argparse.ArgumentParser(description="项目初始化")
    parser.add_argument("--db-only", action="store_true", help="只建表")
    parser.add_argument("--pool-only", action="store_true", help="只更新股票池")
    parser.add_argument("--kline-only", action="store_true", help="只下载K线")
    parser.add_argument("--indices", action="store_true", help="只下载指数K线（可与 --kline-only 同时使用）")
    parser.add_argument("--accounts", action="store_true", help="只初始化账户")
    parser.add_argument("--force", action="store_true", help="强制重建（清空已有数据，仅与 --accounts 配合使用）")
    parser.add_argument("--start-year", type=int, default=2020, help="K线数据起始年份 (默认: 2020)")
    args = parser.parse_args()

    print("=" * 60)
    print(f"🚀 项目初始化 — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)
    print()

    # 如果没有指定特定步骤，执行完整初始化
    full_init = not (args.db_only or args.pool_only or args.kline_only or args.indices or args.accounts)

    if full_init or args.db_only:
        print("📦 Step 1: 建表...")
        step_init_db()

    if full_init or args.pool_only:
        print("📦 Step 2: 更新股票池...")
        # 先清空旧数据，避免重复
        from core.db import get_conn
        with get_conn() as conn:
            conn.execute("DELETE FROM stock_pool WHERE pool='zz800'")
            print(f"  已清空旧股票池")
        step_init_pool()

    if full_init or args.kline_only:
        print(f"📦 Step 3: 下载日K线 (起始年份: {args.start_year})...")
        step_init_kline(start_year=args.start_year)

    if full_init or args.kline_only:
        print(f"📦 Step 3b: 下载指数K线 (起始年份: {args.start_year})...")
        step_init_indices(start_year=args.start_year)

    if args.indices:
        print(f"📦 Step 指数: 下载指数K线 (起始年份: {args.start_year})...")
        # 先确保表存在
        from core.db import init_db
        init_db()
        step_init_indices(start_year=args.start_year)

    if full_init or args.accounts:
        print("📦 Step 4: 初始化账户...")
        step_init_accounts(force=args.force)

    if full_init:
        print("=" * 60)
        print("✅ 初始化完成！")
        print()
        print("下一步:")
        print("  1. 绑定策略:")
        print("     python scripts/sim/account_runner.py switch --account-id 1 --strategy v27")
        print("  2. 跑回测: python scripts/backtest/run_backtest.py --strategy v27")
        print("  3. 跑模拟盘: python scripts/sim/account_runner.py --account-id 1 intraday_signal")
        print("  4. 查看账户: python scripts/sim/account_runner.py list")
        print("=" * 60)

if __name__ == "__main__":
    main()
