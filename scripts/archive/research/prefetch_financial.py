"""
预缓存全选股池财务数据
======================
在后台运行，把选股池所有股票的财务数据缓存到本地。
后续 filter_quality_batch 就能秒级完成。

用法:
  python scripts/prefetch_financial.py          # 缓存全部
  python scripts/prefetch_financial.py --check  # 检查缓存状态
"""

import os, sys, time, json
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE_DIR)

from scripts.financial_filter import fetch_financial

CACHE_DIR = os.path.join(_BASE_DIR, "data", "cache", "financial")
os.makedirs(CACHE_DIR, exist_ok=True)


def prefetch_all(codes: list, n_workers: int = 4, use_cache: bool = True):
    """预缓存所有股票财务数据"""
    total = len(codes)
    cached = sum(1 for c in codes if os.path.exists(os.path.join(CACHE_DIR, f"{c}.json")))
    need_fetch = total - cached

    print(f"总计: {total} 只, 已缓存: {cached}, 需获取: {need_fetch}")
    if need_fetch == 0:
        print("全部已缓存，无需获取")
        return

    t0 = time.time()
    fetched = 0
    failed = 0
    skipped = 0

    def _fetch(code):
        cache_file = os.path.join(CACHE_DIR, f"{code}.json")
        if use_cache and os.path.exists(cache_file):
            return ('cached', code)
        try:
            time.sleep(0.3)  # 避免限流
            fin = fetch_financial(code, use_cache=False)
            if fin:
                return ('ok', code)
            else:
                return ('fail', code)
        except:
            return ('fail', code)

    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = {executor.submit(_fetch, str(c).zfill(6)): c for c in codes}
        for i, future in enumerate(as_completed(futures)):
            status, code = future.result()
            if status == 'ok':
                fetched += 1
            elif status == 'fail':
                failed += 1
            else:
                skipped += 1

            done = fetched + failed + skipped
            if done % 200 == 0:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                remaining = (total - done) / rate if rate > 0 else 0
                print(f"  进度 {done}/{total} ({done/total:.0%}), "
                      f"成功 {fetched}, 失败 {failed}, "
                      f"速度 {rate:.0f}只/s, 预计剩余 {remaining:.0f}s")

    elapsed = time.time() - t0
    print(f"\n完成: 成功 {fetched}, 失败 {failed}, 耗时 {elapsed:.1f}s")


def check_cache_status(codes: list):
    """检查缓存状态"""
    cached = sum(1 for c in codes if os.path.exists(os.path.join(CACHE_DIR, f"{c}.json")))
    print(f"缓存状态: {cached}/{len(codes)} ({cached/len(codes):.0%})")

    # 检查缓存时效
    fresh = 0
    stale = 0
    for c in codes:
        f = os.path.join(CACHE_DIR, f"{c}.json")
        if os.path.exists(f):
            age_days = (time.time() - os.path.getmtime(f)) / 86400
            if age_days < 30:
                fresh += 1
            else:
                stale += 1
    print(f"  30天内: {fresh}, 过期: {stale}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='只检查状态')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--limit', type=int, default=None, help='限制数量（测试用）')
    args = parser.parse_args()

    # 加载选股池
    pool_file = os.path.join(_BASE_DIR, "data", "cache", "stock_pool_fast.csv")
    if not os.path.exists(pool_file):
        print("❌ 选股池不存在，请先运行 stock_pool.py")
        sys.exit(1)

    pool = pd.read_csv(pool_file, dtype={'code': str})
    codes = pool['code'].astype(str).str.zfill(6).tolist()
    if args.limit:
        codes = codes[:args.limit]

    print(f"选股池: {len(codes)} 只")
    check_cache_status(codes)

    if not args.check:
        print("\n开始预缓存...")
        prefetch_all(codes, n_workers=args.workers)
        print("\n最终状态:")
        check_cache_status(codes)
