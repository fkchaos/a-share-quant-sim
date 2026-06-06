"""
行业分类数据获取 - 使用国证行业分类（CNINFO）。

数据源：AKShare stock_industry_change_cninfo
缓存：data/industry_map.csv（股票代码 -> 行业名称）

用法：
    python industry_data_v2.py          # 获取并缓存行业分类
    python industry_data_v2.py --check  # 检查缓存状态
"""

import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
CACHE_FILE = os.path.join(DATA_DIR, "industry_map.csv")

import ssl
ssl._create_default_https_context = ssl._create_unverified_context


def fetch_single_industry(code):
    """获取单只股票的行业分类。"""
    import akshare as ak
    try:
        df = ak.stock_industry_change_cninfo(symbol=code)
        if df is not None and len(df) > 0:
            # 取最近一次变更的行业大类
            df = df.sort_values("变更日期", ascending=False)
            latest = df.iloc[0]
            industry = latest.get("行业大类", "")
            if pd.notna(industry) and industry:
                return code, industry
    except Exception:
        pass
    return code, ""


def fetch_all_industries(codes, max_workers=4):
    """多线程获取所有股票的行业分类。"""
    results = {}
    failed = []
    total = len(codes)

    print(f"📡 获取 {total} 只股票的行业分类（{max_workers} 线程）...")
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_single_industry, code): code for code in codes}
        done = 0
        for future in as_completed(futures):
            code, industry = future.result()
            done += 1
            if industry:
                results[code] = industry
            else:
                failed.append(code)

            if done % 50 == 0 or done == total:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                print(f"  [{done}/{total}] {rate:.1f} 只/秒, 已映射 {len(results)} 只")

    elapsed = time.time() - t0
    print(f"✅ 完成: {len(results)}/{total} 只股票有行业分类 ({elapsed:.1f}s)")
    if failed:
        print(f"   ⚠️ {len(failed)} 只股票获取失败")

    return results


def save_cache(stock_industry_map):
    """保存行业映射到缓存文件。"""
    df = pd.DataFrame(
        list(stock_industry_map.items()),
        columns=["code", "industry"]
    )
    df.to_csv(CACHE_FILE, index=False)
    print(f"💾 已保存到 {CACHE_FILE}（{len(df)} 条记录）")
    return df


def load_cache():
    """从缓存加载行业映射。"""
    if not os.path.exists(CACHE_FILE):
        return None
    df = pd.read_csv(CACHE_FILE, dtype={"code": str})
    return dict(zip(df["code"], df["industry"]))


def check_cache():
    """检查缓存状态。"""
    if not os.path.exists(CACHE_FILE):
        print("❌ 缓存文件不存在")
        return

    df = pd.read_csv(CACHE_FILE, dtype={"code": str})
    print(f"✅ 缓存文件: {CACHE_FILE}")
    print(f"   股票数: {len(df)}")
    print(f"   行业数: {df['industry'].nunique()}")
    print(f"   行业列表: {sorted(df['industry'].unique())}")


def main():
    parser = argparse.ArgumentParser(description="行业分类数据获取（国证）")
    parser.add_argument("--check", action="store_true", help="检查缓存状态")
    parser.add_argument("--workers", type=int, default=4, help="线程数（默认 4）")
    args = parser.parse_args()

    if args.check:
        check_cache()
        return

    # 获取所有股票代码
    daily_dir = os.path.join(DATA_DIR, "daily")
    if not os.path.exists(daily_dir):
        print(f"❌ 数据目录不存在: {daily_dir}")
        return

    codes = sorted([f.replace(".csv", "") for f in os.listdir(daily_dir) if f.endswith(".csv")])
    print(f"📂 数据目录: {daily_dir}（{len(codes)} 只股票）")

    # 获取行业分类
    stock_industry_map = fetch_all_industries(codes, max_workers=args.workers)

    if stock_industry_map:
        save_cache(stock_industry_map)
        check_cache()
    else:
        print("❌ 获取失败")


if __name__ == "__main__":
    main()
