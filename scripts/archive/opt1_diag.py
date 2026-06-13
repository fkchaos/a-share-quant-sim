"""
opt-1 诊断：测试风格因子组的有效性
1. 计算候选因子在最近6个月的截面 IC
2. 确认因子方向后再加入 ensemble_groups
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
from core.config import STRATEGY_PROFILES
from core.factors import calc_factors_panel
from core.scoring import standardize, composite_score
import time

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
PANEL_DIR = os.path.join(DATA_DIR, "panel_zz800")

def load_panel(start=None, end=None):
    """加载面板数据"""
    import pickle
    with open(os.path.join(PANEL_DIR, "close.pkl"), "rb") as f:
        close = pickle.load(f)
    with open(os.path.join(PANEL_DIR, "volume.pkl"), "rb") as f:
        volume = pickle.load(f)
    with open(os.path.join(PANEL_DIR, "amount.pkl"), "rb") as f:
        amount = pickle.load(f)
    with open(os.path.join(PANEL_DIR, "high.pkl"), "rb") as f:
        high = pickle.load(f)
    with open(os.path.join(PANEL_DIR, "low.pkl"), "rb") as f:
        low = pickle.load(f)

    if start:
        close = close.loc[close.index >= start]
        volume = volume.loc[volume.index >= start]
        amount = amount.loc[amount.index >= start]
        high = high.loc[high.index >= start]
        low = low.loc[low.index >= start]
    if end:
        close = close.loc[close.index <= end]
        volume = volume.loc[volume.index <= end]
        amount = amount.loc[amount.index <= end]
        high = high.loc[high.index <= end]
        low = low.loc[low.index <= end]

    return close, volume, amount, high, low


def calc_ic(factors, close_panel, forward_days=5):
    """计算截面 IC（因子值 vs 未来 N 日收益）"""
    rets = close_panel.pct_change(forward_days).shift(-forward_days)
    ic_data = {}

    for fname, fdf in factors.items():
        if not isinstance(fdf, pd.DataFrame):
            continue
        ics = []
        for date in fdf.index:
            if date not in rets.index:
                continue
            fvals = fdf.loc[date].dropna()
            rvals = rets.loc[date].dropna()
            common = fvals.index.intersection(rvals.index)
            if len(common) < 20:
                continue
            fv = fvals[common].values
            rv = rvals[common].values
            mask = ~(np.isnan(fv) | np.isnan(rv))
            if mask.sum() < 20:
                continue
            corr = np.corrcoef(fv[mask], rv[mask])[0, 1]
            if not np.isnan(corr):
                ics.append(corr)

        if ics:
            ic_mean = np.mean(ics)
            ic_std = np.std(ics)
            ir = ic_mean / ic_std if ic_std > 0 else 0
            ic_data[fname] = {"IC": ic_mean, "IR": ir, "N": len(ics)}
        else:
            ic_data[fname] = {"IC": 0, "IR": 0, "N": 0}

    return ic_data


def main():
    start = "2025-01-01"
    end = "2026-06-11"

    print(f"=== opt-1 因子诊断 ({start} ~ {end}) ===\n")

    # 加载数据
    print("加载面板数据...")
    close, volume, amount, high, low = load_panel(start, end)
    print(f"  {close.shape[0]} 天 × {close.shape[1]} 只股票")

    # 计算因子
    print("计算因子...")
    t0 = time.time()
    factors = calc_factors_panel(close, volume, amount, high, low)
    print(f"  {len(factors)} 个因子, {time.time() - t0:.1f}s\n")

    # 候选风格因子
    candidates = [
        'illiquidity', 'vol_ratio_20', 'amount_ratio',
        'turnover_skew', 'turnover_change', 'price_impact',
        'skew_20', 'kurt_20', 'vwap_mom',
        'amplitude', 'pv_corr', 'obv_slope',
    ]

    # 计算 IC
    print("计算截面 IC (5日)...")
    ic5 = calc_ic({k: factors[k] for k in candidates if k in factors}, close, 5)

    print(f"\n{'因子':<20s} {'IC5':>8s} {'IR5':>8s} {'N':>6s}  | 方向")
    print("-" * 60)
    valid = []
    for fname in sorted(candidates):
        if fname not in ic5:
            continue
        d = ic5[fname]
        direction = "✅ 正向" if d["IC"] > 0.01 else ("❌ 负向" if d["IC"] < -0.01 else "➖ 噪声")
        print(f"  {fname:<18s} {d['IC']:>+8.4f} {d['IR']:>+8.4f} {d['N']:>6d}  | {direction}")
        if abs(d["IC"]) > 0.01:
            valid.append((fname, d["IC"], d["IR"]))

    # 推荐组合
    print(f"\n=== 推荐 ===")
    if valid:
        valid.sort(key=lambda x: abs(x[2]), reverse=True)
        print(f"有效因子（|IC5|>0.01，按|IR|排序）：")
        for fname, ic, ir in valid:
            sign = "+" if ic > 0 else "-"
            print(f"  {fname}: IC={ic:+.4f}, IR={ir:+.4f} → 权重符号 {sign}")
    else:
        print("  没有显著因子，窗口可能太短或因子失效")

    # 相关性矩阵
    print(f"\n=== 候选因子相关性 ===")
    valid_names = [v[0] for v in valid]
    if len(valid_names) >= 2:
        fdf = pd.DataFrame({k: factors[k].iloc[-1] for k in valid_names if k in factors}).dropna()
        if not fdf.empty:
            corr = fdf.corr()
            print(corr.round(2))
            # 高相关对
            high_corr = []
            for i in range(len(valid_names)):
                for j in range(i + 1, len(valid_names)):
                    c = corr.iloc[i, j] if valid_names[i] in corr and valid_names[j] in corr.columns else 0
                    if abs(c) > 0.7:
                        high_corr.append((valid_names[i], valid_names[j], c))
            if high_corr:
                print(f"\n高相关对（|r|>0.7）：")
                for a, b, c in high_corr:
                    print(f"  {a} ↔ {b}: {c:+.2f}")


if __name__ == "__main__":
    main()
