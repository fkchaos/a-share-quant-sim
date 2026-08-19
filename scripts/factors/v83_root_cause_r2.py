"""Round 2: 因子预处理根因排查
测试: 行业中性化、市值中性化、双中性化、z-score"""
import pandas as pd
import numpy as np
import sqlite3
from scipy import stats
import akshare as ak
import warnings
warnings.filterwarnings('ignore')

print("=" * 70)
print("Round 2: 因子预处理根因排查")
print("=" * 70)

# === 数据准备 ===
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
    SELECT code, float_shares, industry FROM stock_pool_zz1800 WHERE code IN ({placeholders})
""", conn, params=codes)
conn.close()

kline['date'] = pd.to_datetime(kline['date'])
fs_dict = dict(zip(fs['code'], fs['float_shares']))
ind_dict = dict(zip(fs['code'], fs['industry']))

pivot = kline.set_index(['date', 'code']).sort_index()
close = pivot['close'].unstack('code')
turnover_panel = pivot['volume'].unstack('code') * 100  # 手→股，后面再除float_shares

# 对齐float_shares和行业到实际有数据的股票
active_codes = close.columns.tolist()
fs_aligned = pd.Series({c: fs_dict.get(c, np.nan) for c in active_codes}).dropna()
ind_aligned = pd.Series({c: ind_dict.get(c, np.nan) for c in active_codes}).dropna()

# 换手率
turnover_panel = turnover_panel.div(fs_aligned, axis=1)

# 市值面板
market_cap = close.mul(fs_aligned, axis=1)

# === 因子计算 ===
ret_daily = close.pct_change(1)
ma3, ma6, ma12, ma24 = close.rolling(3).mean(), close.rolling(6).mean(), close.rolling(12).mean(), close.rolling(24).mean()
bbi = (ma3 + ma6 + ma12 + ma24) / 4
bbi_momentum = bbi / close

turn_10 = turnover_panel.rolling(10).mean()
turn_120 = turnover_panel.rolling(120, min_periods=90).mean()
turn_ratio = turn_10 / turn_120

var_60 = ret_daily.rolling(60, min_periods=40).var() * 252

factors_raw = {
    'BBI动量': bbi_momentum,
    '换手率比': turn_ratio,
    '60日方差': var_60,
}

def neutralize_industry(factor_df, industry_series):
    """行业中性化"""
    result = factor_df.copy()
    for dt in factor_df.index:
        row = factor_df.loc[dt].dropna()
        if len(row) < 50: continue
        ind = industry_series.reindex(row.index).dropna()
        common = row.index.intersection(ind.index)
        if len(common) < 50: continue
        means = row[common].groupby(ind[common]).transform('mean')
        result.loc[dt, common] = row[common] - means
    return result

def neutralize_mcap(factor_df, mcap_df):
    """市值中性化: 对log(市值)取残差"""
    result = factor_df.copy()
    for dt in factor_df.index:
        row = factor_df.loc[dt].dropna()
        mc = mcap_df.loc[dt].dropna()
        common = row.index.intersection(mc.index)
        if len(common) < 50: continue
        f_vals = row[common].values
        m_vals = np.log(mc[common].values + 1)
        m_z = (m_vals - m_vals.mean()) / (m_vals.std() + 1e-10)
        f_z = (f_vals - f_vals.mean()) / (f_vals.std() + 1e-10)
        b = np.mean(f_z * m_z)
        a = f_vals.mean() - b * m_vals.mean() * (f_vals.std() / (m_vals.std() + 1e-10))
        residual = f_vals - (a + b * m_vals)
        result.loc[dt, common] = residual
    return result

def zscore(factor_df):
    result = factor_df.copy()
    for dt in factor_df.index:
        row = factor_df.loc[dt].dropna()
        if len(row) < 50: continue
        result.loc[dt, row.index] = (row - row.mean()) / (row.std() + 1e-10)
    return result

def calc_ic_series(factor, fwd_ret):
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

fwd5 = close.pct_change(5).shift(-5)
xt_ic = {'BBI动量': 0.746, '换手率比': 0.566, '60日方差': 0.286}

methods = {
    '原始': lambda f: f,
    '行业中性化': lambda f: neutralize_industry(f, ind_aligned),
    '市值中性化': lambda f: neutralize_mcap(f, market_cap),
    '行业+市值双中性化': lambda f: neutralize_mcap(neutralize_industry(f, ind_aligned), market_cap),
    'z-score标准化': lambda f: zscore(f),
    '行业中性化+zscore': lambda f: zscore(neutralize_industry(f, ind_aligned)),
}

for fname, factor_raw in factors_raw.items():
    print(f"\n{'='*70}")
    print(f"{fname} (迅投IC={xt_ic[fname]:+.4f})")
    print(f"{'='*70}")
    print(f"  {'预处理方法':<20s}  {'IC Mean':>8s}  {'IR':>8s}  {'ΔIC':>8s}")

    for mname, mfunc in methods.items():
        factor_treated = mfunc(factor_raw)
        ics = calc_ic_series(factor_treated, fwd5)
        ic_m = np.mean(ics)
        ic_s = np.std(ics)
        ir = ic_m / ic_s if ic_s > 0 else 0
        delta = ic_m - xt_ic[fname]
        marker = " ← 接近!" if abs(ic_m) > abs(xt_ic[fname]) * 0.5 else ""
        print(f"  {mname:<20s}  {ic_m:>+8.4f}  {ir:>+8.4f}  {delta:>+8.4f}{marker}")

print("\n" + "=" * 70)
print("Round 2 完成")
print("=" * 70)
