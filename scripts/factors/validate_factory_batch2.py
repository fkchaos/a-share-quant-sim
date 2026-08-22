#!/usr/bin/env python3
"""
批量验证factor factory第二批因子在zz1800池上的IC/IR + 与v75j因子的相关性
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import sqlite3
from pathlib import Path

# ── 参数 ──
START = '2021-01-01'
END = '2026-06-30'
POOL = 'zz1800'
IC_ROLLING = 20  # 滚动IC窗口

# ── 加载面板数据 ──
from core.db import load_panel_from_db

print("📦 加载面板数据...")
(close, vol, amt, open_, high, low), codes = load_panel_from_db(
    pool=POOL, start_date=START, end_date=END,
    need_open=True, need_hl=True
)
print(f"  面板: {close.shape[0]}天 x {close.shape[1]}只股票")

# 计算常用中间量
turnover = vol  # volume已经是换手率(手→股已在db层处理)

# ── 因子计算函数 ──
def calc_factor(name):
    """计算单个因子，返回DataFrame(date x code)"""
    if name == 'f0011a':  # 120日平均换手率
        return turnover.rolling(120, min_periods=60).mean()
    elif name == 'f0012a':  # 10日平均换手率
        return turnover.rolling(10, min_periods=5).mean()
    elif name == 'f0013a':  # 240日平均换手率
        return turnover.rolling(240, min_periods=120).mean()
    elif name == 'f0016a':  # 20日成交金额标准差
        return amt.rolling(20, min_periods=10).std()
    elif name == 'f0017a':  # 5日平均换手率
        return turnover.rolling(5, min_periods=3).mean()
    elif name == 'f0018a':  # 5日EMA
        return close.ewm(span=5, min_periods=3).mean()
    elif name == 'f0019a':  # 10日EMA
        return close.ewm(span=10, min_periods=5).mean()
    elif name == 'f0020a':  # 12日EMA
        return close.ewm(span=12, min_periods=6).mean()
    elif name == 'f0021a':  # 120日EMA
        return close.ewm(span=120, min_periods=60).mean()
    elif name == 'f0022a':  # 5日MA
        return close.rolling(5, min_periods=3).mean()
    elif name == 'f0023a':  # 20日成交金额MA
        return amt.rolling(20, min_periods=10).mean()
    elif name == 'f0024a':  # 20日资金流量 (amount * sign(close-open))
        flow = amt * np.sign(close - open_)
        return flow.rolling(20, min_periods=10).mean()
    elif name == 'f0025a':  # 布林上轨(20日)
        ma20 = close.rolling(20, min_periods=10).mean()
        std20 = close.rolling(20, min_periods=10).std()
        return ma20 + 2 * std20
    else:
        return None

# ── IC/IR计算 ──
def calc_ic_series(factor_df, forward_ret):
    """计算截面Rank IC序列"""
    ic_list = []
    dates = factor_df.index
    for i in range(20, len(dates)):
        date = dates[i]
        f = factor_df.loc[date].dropna()
        r = forward_ret.loc[date].dropna()
        common = f.index.intersection(r.index)
        if len(common) < 50:
            continue
        ic = f[common].rank().corr(r[common].rank())
        if not np.isnan(ic):
            ic_list.append((date, ic))
    if not ic_list:
        return pd.Series(dtype=float)
    return pd.Series(dict(ic_list))

# ── v75j流动性因子(作为参照) ──
print("\n📊 计算v75j流动性参照因子...")
liq_20d = amt.rolling(20, min_periods=10).mean()  # v75j用的就是20日均成交额

# ── 前向收益 ──
forward_ret = close.pct_change(20).shift(-20)  # 20日未来收益

# ── 批量验证 ──
factors_to_test = ['f0011a','f0012a','f0013a','f0016a','f0017a',
                   'f0018a','f0019a','f0020a','f0021a','f0022a',
                   'f0023a','f0024a','f0025a']

results = []
factor_data = {}

for fname in factors_to_test:
    print(f"  计算 {fname}...", end=' ')
    fdf = calc_factor(fname)
    if fdf is None:
        print("SKIP")
        continue
    
    ic_series = calc_ic_series(fdf, forward_ret)
    if len(ic_series) < 100:
        print(f"IC样本不足({len(ic_series)})")
        continue
    
    ic_mean = ic_series.mean()
    ic_std = ic_series.std()
    icir = ic_mean / ic_std if ic_std > 0 else 0
    ic_win = (ic_series > 0).mean() if ic_mean > 0 else (ic_series < 0).mean()
    
    # 和v75j流动性因子的相关性
    # 取截面rank相关的时间序列均值
    corr_list = []
    common_dates = fdf.index.intersection(liq_20d.index)
    for date in common_dates[::20]:  # 每20天采样一次
        f = fdf.loc[date].dropna()
        l = liq_20d.loc[date].dropna()
        c = f.index.intersection(l.index)
        if len(c) > 50:
            corr = f[c].rank().corr(l[c].rank())
            if not np.isnan(corr):
                corr_list.append(corr)
    avg_corr = np.mean(corr_list) if corr_list else 0
    
    factor_data[fname] = fdf
    
    results.append({
        'factor': fname,
        'ic_mean': ic_mean,
        'icir': icir,
        'ic_win_rate': ic_win,
        'corr_with_liq': avg_corr,
        'ic_abs': abs(ic_mean),
        'icir_abs': abs(icir),
    })
    print(f"IC={ic_mean:.4f} ICIR={icir:.3f} corr_liq={avg_corr:.3f}")

# ── 汇总排序 ──
df = pd.DataFrame(results)
if df.empty:
    print("\n❌ 没有有效因子")
    sys.exit(0)

# 按|IC|降序
df = df.sort_values('ic_abs', ascending=False).reset_index(drop=True)

print("\n" + "="*80)
print("📊 Factor Factory第二批因子 — zz1800池验证结果")
print("="*80)
print(f"\n{'因子':<10} {'IC Mean':>10} {'ICIR':>10} {'|IC|':>8} {'|ICIR|':>8} {'IC胜率':>8} {'与流动性相关':>12} {'评级':>6}")
print("-"*80)

for _, row in df.iterrows():
    # 评级
    if row['icir_abs'] >= 0.3 and row['ic_abs'] >= 0.03:
        rating = '✅ 有效'
    elif row['icir_abs'] >= 0.2 and row['ic_abs'] >= 0.02:
        rating = '⚠️ 边缘'
    elif row['corr_with_liq'] > 0.7:
        rating = '❌ 冗余'
    else:
        rating = '❌ 弱'
    
    print(f"{row['factor']:<10} {row['ic_mean']:>10.4f} {row['icir']:>10.3f} {row['ic_abs']:>8.4f} {row['icir_abs']:>8.3f} {row['ic_win_rate']:>7.1%} {row['corr_with_liq']:>12.3f} {rating:>6}")

print("\n" + "="*80)
print("评级标准:")
print("  ✅ 有效: |IC|≥0.03 且 |ICIR|≥0.3")
print("  ⚠️ 边缘: |IC|≥0.02 且 |ICIR|≥0.2")
print("  ❌ 冗余: 与v75j流动性因子相关性>0.7")
print("  ❌ 弱: 不满足以上条件")

# 保存结果
df.to_csv('/tmp/factor_factory第二批_zz1800验证.csv', index=False)
print(f"\n📁 详细数据已保存: /tmp/factor_factory第二批_zz1800验证.csv")
