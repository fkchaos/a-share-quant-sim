"""Round 1: IC计算方法根因排查
测试: 秩相关 vs 回归IC, 不同前瞻收益周期, pool方式"""
import pandas as pd
import numpy as np
import sqlite3
from scipy import stats
import akshare as ak
import warnings
warnings.filterwarnings('ignore')

print("=" * 70)
print("Round 1: IC计算方法根因排查")
print("=" * 70)

# === 数据准备 (HS300, 2022-06 ~ 2023-10) ===
df300 = ak.index_stock_cons(symbol='000300')
codes = df300['品种代码'].tolist()

conn = sqlite3.connect('data/quant_stocks.db')
placeholders = ','.join(['?'] * len(codes))

kline = pd.read_sql_query(f"""
    SELECT code, date, close, volume
    FROM daily_kline
    WHERE code IN ({placeholders}) AND date BETWEEN '2022-06-01' AND '2023-10-31'
    ORDER BY code, date
""", conn, params=codes)

fs = pd.read_sql_query(f"""
    SELECT code, float_shares FROM stock_pool_zz1800 WHERE code IN ({placeholders})
""", conn, params=codes)
fs_dict = dict(zip(fs['code'], fs['float_shares']))
conn.close()

kline['date'] = pd.to_datetime(kline['date'])
kline['float_shares'] = kline['code'].map(fs_dict)
kline['turnover'] = kline['volume'] * 100 / kline['float_shares']

pivot = kline.set_index(['date', 'code']).sort_index()
close = pivot['close'].unstack('code')
turnover = pivot['turnover'].unstack('code')

# === 因子计算 ===
ret_daily = close.pct_change(1)
ma3 = close.rolling(3).mean()
ma6 = close.rolling(6).mean()
ma12 = close.rolling(12).mean()
ma24 = close.rolling(24).mean()
bbi = (ma3 + ma6 + ma12 + ma24) / 4
bbi_momentum = bbi / close

turn_10 = turnover.rolling(10).mean()
turn_120 = turnover.rolling(120, min_periods=90).mean()
turn_ratio = turn_10 / turn_120

var_60 = ret_daily.rolling(60, min_periods=40).var() * 252

factors = {
    'BBI动量': bbi_momentum,
    '换手率比': turn_ratio,
    '60日方差': var_60,
}
xt_ic = {'BBI动量': 0.746, '换手率比': 0.566, '60日方差': 0.286}

def spearman_ic(factor, fwd_ret):
    common = factor.index.intersection(fwd_ret.index)
    ics = []
    for dt in common:
        f = factor.loc[dt].dropna()
        r = fwd_ret.loc[dt].dropna()
        c = f.index.intersection(r.index)
        if len(c) < 50: continue
        ic, _ = stats.spearmanr(f[c], r[c])
        ics.append(ic)
    return np.array(ics) if ics else np.array([np.nan])

def pearson_ic(factor, fwd_ret):
    common = factor.index.intersection(fwd_ret.index)
    ics = []
    for dt in common:
        f = factor.loc[dt].dropna()
        r = fwd_ret.loc[dt].dropna()
        c = f.index.intersection(r.index)
        if len(c) < 50: continue
        ic, _ = stats.pearsonr(f[c], r[c])
        ics.append(ic)
    return np.array(ics) if ics else np.array([np.nan])

def regression_ic(factor, fwd_ret):
    common = factor.index.intersection(fwd_ret.index)
    ics = []
    for dt in common:
        f = factor.loc[dt].dropna()
        r = fwd_ret.loc[dt].dropna()
        c = f.index.intersection(r.index)
        if len(c) < 50: continue
        f_z = (f[c].values - f[c].mean()) / (f[c].std() + 1e-10)
        r_z = (r[c].values - r[c].mean()) / (r[c].std() + 1e-10)
        beta = np.mean(f_z * r_z)
        ics.append(beta)
    return np.array(ics) if ics else np.array([np.nan])

def pool_ic(factor, fwd_ret):
    common = factor.index.intersection(fwd_ret.index)
    f_all, r_all = [], []
    for dt in common:
        f = factor.loc[dt].dropna()
        r = fwd_ret.loc[dt].dropna()
        c = f.index.intersection(r.index)
        if len(c) < 50: continue
        f_all.append(f[c])
        r_all.append(r[c])
    if not f_all: return np.nan, np.nan
    f_arr = np.concatenate(f_all)
    r_arr = np.concatenate(r_all)
    sp_ic, _ = stats.spearmanr(f_arr, r_arr)
    pe_ic, _ = stats.pearsonr(f_arr, r_arr)
    return sp_ic, pe_ic

# === R1-1: IC方法对比 ===
print("\n" + "=" * 70)
print("R1-1: IC计算方法对比 (前瞻收益=5日)")
print("=" * 70)

fwd5 = close.pct_change(5).shift(-5)

for name, factor in factors.items():
    print(f"\n--- {name} (迅投IC={xt_ic[name]:+.4f}) ---")
    sp_ics = spearman_ic(factor, fwd5)
    reg_ics = regression_ic(factor, fwd5)
    pe_ics = pearson_ic(factor, fwd5)

    sp_m, sp_s = np.mean(sp_ics), np.std(sp_ics)
    pe_m, pe_s = np.mean(pe_ics), np.std(pe_ics)
    rg_m, rg_s = np.mean(reg_ics), np.std(reg_ics)

    print(f"  Spearman IC:   mean={sp_m:+.4f}  std={sp_s:.4f}  IR={sp_m/sp_s:+.4f}")
    print(f"  Pearson IC:    mean={pe_m:+.4f}  std={pe_s:.4f}  IR={pe_m/pe_s:+.4f}")
    print(f"  Regression IC: mean={rg_m:+.4f}  std={rg_s:.4f}  IR={rg_m/rg_s:+.4f}")
    best = max(abs(sp_m), abs(pe_m), abs(rg_m))
    print(f"  最强方法绝对值: {best:.4f} vs 迅投: {abs(xt_ic[name]):.4f}  差距: {abs(xt_ic[name])-best:.4f}")

# === R1-2: 前瞻收益周期对比 ===
print("\n" + "=" * 70)
print("R1-2: 前瞻收益周期对比 (Spearman IC)")
print("=" * 70)

periods = [1, 2, 3, 5, 10, 20]
for name, factor in factors.items():
    print(f"\n--- {name} (迅投IC={xt_ic[name]:+.4f}) ---")
    print(f"  {'周期':>6s}  {'IC Mean':>8s}  {'IC Std':>8s}  {'IR':>8s}  {'P(IC>0)':>8s}")
    for p in periods:
        fwd = close.pct_change(p).shift(-p)
        ics = spearman_ic(factor, fwd)
        ic_m = np.mean(ics)
        ic_s = np.std(ics)
        ir = ic_m / ic_s if ic_s > 0 else 0
        p_pos = np.mean(ics > 0) * 100
        print(f"  {p:>6d}d  {ic_m:>+8.4f}  {ic_s:>8.4f}  {ir:>+8.4f}  {p_pos:>7.1f}%")

# === R1-3: Pool IC vs 截面IC均值 ===
print("\n" + "=" * 70)
print("R1-3: Pool IC vs 截面IC均值 (5日前瞻)")
print("=" * 70)

for name, factor in factors.items():
    print(f"\n--- {name} (迅投IC={xt_ic[name]:+.4f}) ---")
    sp_ics = spearman_ic(factor, fwd5)
    sec_mean = np.mean(sp_ics)
    sec_ir = np.mean(sp_ics) / np.std(sp_ics) if np.std(sp_ics) > 0 else 0
    pool_sp, pool_pe = pool_ic(factor, fwd5)
    print(f"  截面IC均值:    {sec_mean:+.4f}  IR={sec_ir:+.4f}")
    print(f"  Pool Spearman: {pool_sp:+.4f}")
    print(f"  Pool Pearson:  {pool_pe:+.4f}")
    print(f"  差异(Pool-截面): {pool_sp - sec_mean:+.4f}")

print("\n" + "=" * 70)
print("Round 1 完成")
print("=" * 70)
