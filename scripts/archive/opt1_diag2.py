"""
opt-1 诊断2：对比 v11b vs v11b_style 的选股差异
直接调用 run_backtest 的数据加载，在相同数据上对比两组选股结果
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from core.config import STRATEGY_PROFILES
from core.factors import calc_factors_panel
from core.scoring import ensemble_union_score
import time

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

def load_daily(code):
    """加载单只股票日K线"""
    fpath = os.path.join(DAILY_DIR, f"{code}.csv")
    if not os.path.exists(fpath):
        return None
    df = pd.read_csv(fpath, index_col=0, parse_dates=True)
    return df

def load_panel_from_daily(start="2025-06-01", end="2026-06-11"):
    """从日K线 CSV 构建面板"""
    import glob
    files = sorted(glob.glob(os.path.join(DAILY_DIR, "*.csv")))
    
    close_dict = {}
    volume_dict = {}
    amount_dict = {}
    
    for fpath in files:
        code = os.path.basename(fpath).replace(".csv", "")
        try:
            df = pd.read_csv(fpath, index_col=0, parse_dates=True)
            if start:
                df = df[df.index >= start]
            if end:
                df = df[df.index <= end]
            if len(df) < 60:
                continue
            close_dict[code] = df["close"]
            volume_dict[code] = df.get("volume", df["close"] * 0)
            amount_dict[code] = df.get("amount", df["close"] * df.get("volume", 0))
        except Exception:
            continue
    
    if not close_dict:
        print("没有加载到任何股票数据，检查 DAILY_DIR")
        return None, None, None, None, None, None
    
    close = pd.DataFrame(close_dict).sort_index()
    volume = pd.DataFrame(volume_dict).sort_index()
    amount = pd.DataFrame(amount_dict).sort_index()
    
    # 对齐索引
    common_idx = close.index.intersection(volume.index).intersection(amount.index)
    close = close.loc[common_idx]
    volume = volume.loc[common_idx]
    amount = amount.loc[common_idx]
    
    # 用 close 近似 high/low
    high = close.rolling(2).max()
    low = close.rolling(2).min()
    
    # 过滤停牌过多的股票
    valid_stocks = close.columns[close.isna().sum() < len(close) * 0.3]
    close = close[valid_stocks]
    volume = volume[valid_stocks]
    amount = amount[valid_stocks]
    high = high[valid_stocks]
    low = low[valid_stocks]
    
    return close, volume, amount, None, high, low


def calc_ic_quick(factors, close_panel, forward=5):
    """快速计算截面 IC"""
    fwd_ret = close_panel.pct_change(forward).shift(-forward)
    results = {}
    for fname, fdf in factors.items():
        if not isinstance(fdf, pd.DataFrame):
            continue
        ics = []
        for date in fdf.index:
            if date not in fwd_ret.index:
                continue
            fv = fdf.loc[date].dropna()
            rv = fwd_ret.loc[date].dropna()
            common = fv.index.intersection(rv.index)
            if len(common) < 20:
                continue
            c = np.corrcoef(fv[common].values, rv[common].values)[0, 1]
            if not np.isnan(c):
                ics.append(c)
        if ics:
            results[fname] = {"IC": np.mean(ics), "IR": np.mean(ics) / np.std(ics), "N": len(ics)}
    return results


def main():
    start = "2025-06-01"
    end = "2026-06-11"
    
    print(f"=== opt-1 因子诊断 ({start} ~ {end}) ===\n")
    
    print("加载日K线数据...")
    loaded = load_panel_from_daily(start, end)
    if loaded[0] is None:
        return
    close, volume, amount, _, high, low = loaded
    print(f"  {close.shape[0]} 天 × {close.shape[1]} 只股票\n")
    
    print("计算因子...")
    t0 = time.time()
    factors = calc_factors_panel(close, volume, amount, None, high, low)
    print(f"  {len(factors)} 个因子, {time.time()-t0:.1f}s\n")
    
    # 候选风格因子 IC
    candidates = [
        'illiquidity', 'vol_ratio_20', 'amount_ratio',
        'turnover_skew', 'turnover_change', 'price_impact',
        'skew_20', 'kurt_20', 'vwap_mom', 'amplitude',
    ]
    
    print("计算截面 IC...")
    ic = calc_ic_quick({k: factors[k] for k in candidates if k in factors}, close, 5)
    
    print(f"\n{'因子':<20s} {'IC5':>8s} {'IR5':>8s} {'N':>6s}  | 建议方向")
    print("-" * 60)
    valid = []
    for fname in candidates:
        if fname not in ic:
            print(f"  {fname:<18s} (缺数据)")
            continue
        d = ic[fname]
        ic_val = d["IC"]
        direction = "买入高值" if ic_val > 0.01 else ("买入低值" if ic_val < -0.01 else "噪声")
        tag = "✅" if abs(ic_val) > 0.01 else "❌"
        print(f"  {fname:<18s} {ic_val:>+8.4f} {d['IR']:>+8.4f} {d['N']:>6d}  | {direction} {tag}")
        if abs(ic_val) > 0.01:
            valid.append((fname, ic_val, d["IR"]))
    
    print(f"\n=== 有效因子排序 ===")
    valid.sort(key=lambda x: abs(x[2]), reverse=True)
    for fname, ic_val, ir in valid:
        sign = "+" if ic_val > 0 else "-"
        print(f"  {fname}: IC={ic_val:+.4f}, IR={ir:+.4f} → ensemble权重取 {sign}")
    
    # 相关性
    print(f"\n=== 有效因子截面相关性（最新日）===")
    valid_names = [v[0] for v in valid]
    if len(valid_names) >= 2:
        latest = pd.DataFrame({k: factors[k].iloc[-1] for k in valid_names if k in factors}).dropna()
        corr = latest.corr()
        print(corr.round(2).to_string())
        for i in range(len(valid_names)):
            for j in range(i+1, len(valid_names)):
                if valid_names[i] in corr and valid_names[j] in corr.columns:
                    c = corr.loc[valid_names[i], valid_names[j]]
                    if abs(c) > 0.6:
                        print(f"  ⚠️  {valid_names[i]} ↔ {valid_names[j]}: {c:+.2f}（中高相关）")


if __name__ == "__main__":
    main()
