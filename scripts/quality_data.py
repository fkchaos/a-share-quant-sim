"""
基本面质量因子：从 AKShare THS 接口获取财务数据，构建质量因子面板。

质量因子：
- roe: 净资产收益率（ROE）
- revenue_yoy: 营收同比增速
- profit_yoy: 净利同比增速
- gross_margin: 销售净利率（毛利率代理）
- debt_asset: 资产负债率

数据源：AKShare stock_financial_abstract_ths（年度数据，前向填充到日频）
并发：4 线程，~15 分钟获取全市场数据
缓存：data/quality_factors_cache.csv
"""

import os
import sys
import time
import concurrent.futures
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
CACHE_FILE = os.path.join(DATA_DIR, "quality_factors_cache.csv")

# THS 财务数据列名映射
COL_MAP = {
    "净资产收益率": "roe",
    "营业总收入同比增长率": "revenue_yoy",
    "净利润同比增长率": "profit_yoy",
    "销售净利率": "gross_margin",  # 用销售净利率代理毛利率
    "资产负债率": "debt_asset",
}


def _clean_pct(val):
    """清洗百分比字符串，如 '6.89%%' -> 6.89, '-54.55%' -> -54.55"""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return np.nan
    if isinstance(val, (int, float)):
        return float(val)
    val = str(val).strip().replace('%', '').replace('%%', '')
    try:
        return float(val)
    except (ValueError, TypeError):
        return np.nan


def _fetch_one(code):
    """获取单只股票年度财务数据。"""
    import akshare as ak
    try:
        df = ak.stock_financial_abstract_ths(symbol=code, indicator="按年度")
        if df is None or len(df) == 0:
            return code, None
        df = df[["报告期"] + [c for c in COL_MAP.keys() if c in df.columns]].copy()
        df.rename(columns={k: v for k, v in COL_MAP.items() if k in df.columns}, inplace=True)
        df["code"] = code
        df["报告期"] = pd.to_datetime(df["报告期"], format="%Y", errors="coerce")
        # 清洗百分比列
        for col in COL_MAP.values():
            if col in df.columns:
                df[col] = df[col].apply(_clean_pct)
        return code, df
    except Exception:
        return code, None


def fetch_all_financial(stock_list, max_workers=4):
    """并发获取全市场年度财务数据。"""
    if os.path.exists(CACHE_FILE):
        print(f"  📂 读取缓存: {CACHE_FILE}")
        return pd.read_csv(CACHE_FILE, parse_dates=["报告期"], dtype={"code": str})

    print(f"  📊 获取 {len(stock_list)} 只股票年度财务数据（{max_workers} 线程）...")
    all_dfs = []
    t0 = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(_fetch_one, c) for c in stock_list]
        for i, f in enumerate(concurrent.futures.as_completed(futures)):
            code, df = f.result()
            if df is not None:
                all_dfs.append(df)
            if (i + 1) % 200 == 0:
                print(f"    {i+1}/{len(stock_list)} 只, 已用时 {time.time()-t0:.0f}s, 成功 {len(all_dfs)}")

    if not all_dfs:
        return pd.DataFrame()

    result = pd.concat(all_dfs, ignore_index=True)
    result.to_csv(CACHE_FILE, index=False)
    print(f"  ✅ 保存 {len(result)} 条记录到 {CACHE_FILE}（耗时 {time.time()-t0:.0f}s, 成功 {len(all_dfs)}/{len(stock_list)}）")
    return result


def build_quality_factors(stock_list, dates):
    """
    构建质量因子面板。

    Args:
        stock_list: 股票代码列表
        dates: 日期索引（日频）

    Returns:
        dict: {factor_name: DataFrame (dates x stocks)}
    """
    raw = fetch_all_financial(stock_list)
    if raw.empty:
        print("  ⚠️ 财务数据为空，质量因子全为 NaN")
        return {k: pd.DataFrame(np.nan, index=dates, columns=stock_list) for k in COL_MAP.values()}

    raw = raw.dropna(subset=["报告期", "code"])
    raw["code"] = raw["code"].astype(str).str.zfill(6)

    # 只保留每只股票的最新年度数据（简化：用最新年报）
    # 更精细的做法是保留所有年度数据并按时间对齐
    raw = raw.sort_values(["code", "报告期"])

    factors = {}
    for fname in COL_MAP.values():
        if fname not in raw.columns:
            factors[fname] = pd.DataFrame(np.nan, index=dates, columns=stock_list)
            continue

        # 透视：报告期 × 股票
        pivot = raw.pivot_table(index="报告期", columns="code", values=fname, aggfunc="last")
        pivot = pivot.reindex(columns=stock_list)

        # 前向填充到日频
        full_idx = pd.DatetimeIndex(dates)
        pivot = pivot.reindex(full_idx, method="ffill")

        # 限制最大填充天数（365 天 ≈ 一年）
        pivot = pivot.ffill(limit=365)

        factors[fname] = pivot.reindex(dates).reindex(columns=stock_list)

    # 打印覆盖率
    for fname, fdf in factors.items():
        nan_pct = fdf.isna().mean().mean()
        print(f"  📈 {fname}: NaN%={nan_pct:.1%}")

    return factors


if __name__ == "__main__":
    # 测试
    test_codes = ["000001", "600000", "000858", "600519", "000333", "002415", "300750"]
    test_dates = pd.date_range("2024-01-01", periods=100, freq="B")
    result = build_quality_factors(test_codes, test_dates)
    for k, v in result.items():
        print(f"{k}: shape={v.shape}, last_valid={v.iloc[-1].notna().sum()}/{len(test_codes)}")
