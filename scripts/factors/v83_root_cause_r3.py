"""Round 3: 因子计算公式 + 数据源排查
测试: 不同公式变体, 数据源一致性"""
import pandas as pd
import numpy as np
import sqlite3
from scipy import stats
import akshare as ak
import warnings
warnings.filterwarnings('ignore')

print("=" * 70)
print("Round 3: 因子计算公式 + 数据源排查")
print("=" * 70)

# === 数据准备 ===
df300 = ak.index_stock_cons(symbol='000300')
codes = df300['品种代码'].tolist()

conn = sqlite3.connect('data/quant_stocks.db')
placeholders = ','.join(['?'] * len(codes))

kline = pd.read_sql_query(f"""
    SELECT code, date, close, volume, amount
    FROM daily_kline
    WHERE code IN ({placeholders}) AND date BETWEEN '2022-06-01' AND '2023-10-31'
    ORDER BY code, date
""", conn, params=codes)

fs = pd.read_sql_query(f"""
    SELECT code, float_shares FROM stock_pool_zz1800 WHERE code IN ({placeholders})
""", conn, params=codes)
conn.close()

kline['date'] = pd.to_datetime(kline['date'])
fs_dict = dict(zip(fs['code'], fs['float_shares']))

pivot = kline.set_index(['date', 'code']).sort_index()
close = pivot['close'].unstack('code')
volume = pivot['volume'].unstack('code')  # 手
amount = pivot['amount'].unstack('code')  # 元

active_codes = close.columns.tolist()
fs_aligned = pd.Series({c: fs_dict.get(c, np.nan) for c in active_codes}).dropna()

fwd5 = close.pct_change(5).shift(-5)
xt_ic = {'BBI动量': 0.746, '换手率比': 0.566, '60日方差': 0.286}

def calc_ic(factor, fwd_ret):
    common = factor.index.intersection(fwd_ret.index)
    ics = []
    for dt in common:
        f = factor.loc[dt].dropna()
        r = fwd_ret.loc[dt].dropna()
        c = f.index.intersection(r.index)
        if len(c) < 50: continue
        ic, _ = stats.spearmanr(f[c], r[c])
        ics.append(ic)
    arr = np.array(ics)
    return np.mean(arr), np.std(arr), np.mean(arr)/np.std(arr) if np.std(arr) > 0 else 0

# === R3-1: BBI动量公式变体 ===
print(f"\n{'='*70}")
print(f"R3-1: BBI动量公式变体 (迅投IC=+0.746)")
print(f"{'='*70}")

ma3 = close.rolling(3).mean()
ma6 = close.rolling(6).mean()
ma12 = close.rolling(12).mean()
ma24 = close.rolling(24).mean()

# 变体1: SMA (当前)
bbi_sma = (ma3 + ma6 + ma12 + ma24) / 4
f1 = bbi_sma / close

# 变体2: EMA
ema3 = close.ewm(span=3).mean()
ema6 = close.ewm(span=6).mean()
ema12 = close.ewm(span=12).mean()
ema24 = close.ewm(span=24).mean()
bbi_ema = (ema3 + ema6 + ema12 + ema24) / 4
f2 = bbi_ema / close

# 变体3: WMA (加权)
def wma(s, n):
    weights = np.arange(1, n+1)
    return s.rolling(n).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)

bbi_wma = (wma(close, 3) + wma(close, 6) + wma(close, 12) + wma(close, 24)) / 4
f3 = bbi_wma / close

# 变体4: BBI - close (偏离度)
f4 = (bbi_sma - close) / close

# 变体5: close / BBI
f5 = close / bbi_sma

# 变体6: BBI本身(不做除法)
f6 = bbi_sma

# 变体7: rank标准化后
f7 = f1.rank(axis=1, pct=True)

variants = {
    'SMA/price (当前)': f1,
    'EMA/price': f2,
    'WMA/price': f3,
    '(BBI-price)/price': f4,
    'price/BBI': f5,
    'BBI raw': f6,
    'rank(SMA/price)': f7,
}

print(f"  {'变体':<22s}  {'IC Mean':>8s}  {'IR':>8s}")
for vname, fvar in variants.items():
    ic_m, ic_s, ir = calc_ic(fvar, fwd5)
    marker = " ← !!!" if abs(ic_m) > 0.3 else ""
    print(f"  {vname:<22s}  {ic_m:>+8.4f}  {ir:>+8.4f}{marker}")

# === R3-2: 换手率计算变体 ===
print(f"\n{'='*70}")
print(f"R3-2: 换手率计算变体 (迅投IC=+0.566)")
print(f"{'='*70}")

# 变体1: volume(手)*100 / float_shares (当前)
t1 = volume * 100 / fs_aligned

# 变体2: amount / (close * float_shares) — 用金额反推
t2 = amount / (close * fs_aligned)

# 变体3: volume / (amount/close) — 用均价反推股数
t3 = volume / (amount / close / 100)  # 手

# 变体4: 直接用volume(手)/1000000 — 归一化
t4 = volume / 1000000

# 变体5: amount / 1e8 — 归一化
t5 = amount / 1e8

turn_variants = {
    'vol*100/float (当前)': t1,
    'amount/(close*float)': t2,
    'vol/(amount/close/100)': t3,
    'vol/100万手': t4,
    'amount/亿元': t5,
}

# 10日/120日比值
for tname, tdata in turn_variants.items():
    t10 = tdata.rolling(10).mean()
    t120 = tdata.rolling(120, min_periods=90).mean()
    ratio = t10 / t120
    ic_m, ic_s, ir = calc_ic(ratio, fwd5)
    marker = " ← !!!" if abs(ic_m) > 0.3 else ""
    print(f"  {tname:<24s}  IC={ic_m:>+8.4f}  IR={ir:>+8.4f}{marker}")

# === R3-3: 60日方差计算变体 ===
print(f"\n{'='*70}")
print(f"R3-3: 60日方差计算变体 (迅投IC=+0.286)")
print(f"{'='*70}")

ret = close.pct_change(1)

# 变体1: variance * 252 (当前)
v1 = ret.rolling(60, min_periods=40).var() * 252

# 变体2: std * sqrt(252) — 波动率
v2 = ret.rolling(60, min_periods=40).std() * np.sqrt(252)

# 变体3: 只用var不年化
v3 = ret.rolling(60, min_periods=40).var()

# 变体4: 20日方差
v4 = ret.rolling(20, min_periods=15).var() * 252

# 变体5: 120日方差
v5 = ret.rolling(120, min_periods=90).var() * 252

# 变体6: downsize variance (只算负收益的方差)
neg_ret = ret.copy()
neg_ret[neg_ret > 0] = 0
v6 = neg_ret.rolling(60, min_periods=40).var() * 252

var_variants = {
    'var*252 (当前)': v1,
    'std*sqrt(252)': v2,
    'var(不年化)': v3,
    'var20*252': v4,
    'var120*252': v5,
    'downside_var*252': v6,
}

for vname, vdata in var_variants.items():
    ic_m, ic_s, ir = calc_ic(vdata, fwd5)
    marker = " ← !!!" if abs(ic_m) > 0.2 else ""
    print(f"  {vname:<20s}  IC={ic_m:>+8.4f}  IR={ir:>+8.4f}{marker}")

print("\n" + "=" * 70)
print("Round 3 完成")
print("=" * 70)
