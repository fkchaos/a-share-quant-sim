"""沪深300池复现验证: 用迅投的时间段 2022-10 ~ 2023-10"""
import pandas as pd
import numpy as np
import sqlite3
from scipy import stats
import akshare as ak
import warnings
warnings.filterwarnings('ignore')

print("=" * 70)
print("v82复现验证 — 迅投时间段 2022-10 ~ 2023-10")
print("=" * 70)

# 获取沪深300成分股
df300 = ak.index_stock_cons(symbol='000300')
codes = df300['品种代码'].tolist()
print(f"沪深300成分股: {len(codes)}只")

conn = sqlite3.connect('data/quant_stocks.db')

# 用迅投的时间段: 2022-06-01 ~ 2023-10-31 (需要前120天热身)
start_date = '2022-06-01'
end_date = '2023-10-31'

placeholders = ','.join(['?'] * len(codes))
kline = pd.read_sql_query(f"""
    SELECT code, date, close, volume, amount
    FROM daily_kline
    WHERE code IN ({placeholders})
      AND date BETWEEN ? AND ?
    ORDER BY code, date
""", conn, params=codes + [start_date, end_date])

print(f"日线数据: {len(kline)}条, {kline['code'].nunique()}只股票")

# 获取float_shares
fs = pd.read_sql_query(f"""
    SELECT code, float_shares FROM stock_pool_zz1800
    WHERE code IN ({placeholders})
""", conn, params=codes)
fs_dict = dict(zip(fs['code'], fs['float_shares']))

# 构建面板
kline['date'] = pd.to_datetime(kline['date'])
kline['float_shares'] = kline['code'].map(fs_dict)
kline['turnover'] = kline['volume'] * 100 / kline['float_shares']

pivot = kline.set_index(['date', 'code']).sort_index()
close = pivot['close'].unstack('code')
turnover = pivot['turnover'].unstack('code')

print(f"面板: {close.shape}")

# 5日前瞻收益
fwd_ret = close.pct_change(5).shift(-5)

def calc_ic(factor, fwd_ret, name):
    common_dates = factor.index.intersection(fwd_ret.index)
    ics = []
    for dt in common_dates:
        f = factor.loc[dt].dropna()
        r = fwd_ret.loc[dt].dropna()
        common = f.index.intersection(r.index)
        if len(common) < 50:
            continue
        ic, _ = stats.spearmanr(f[common], r[common])
        ics.append(ic)
    if not ics:
        print(f"\n  [{name}] 样本不足")
        return None
    ic_arr = np.array(ics)
    ic_mean = np.mean(ic_arr)
    ic_std = np.std(ic_arr)
    ir = ic_mean / ic_std if ic_std > 0 else 0
    p_pos = np.mean(ic_arr > 0) * 100
    pct_sig = np.mean(np.abs(ic_arr) > 0.02) * 100
    print(f"\n  [{name}]")
    print(f"    IC Mean: {ic_mean:+.4f} | IC Std: {ic_std:.4f} | IR: {ir:+.4f} | P(IC>0): {p_pos:.1f}%")
    print(f"    |IC|>0.02比率: {pct_sig:.1f}%")
    if abs(ic_mean) > 0.03 and abs(ir) > 0.3:
        print(f"    → ✅ 有效")
    elif abs(ic_mean) > 0.01:
        print(f"    → ⚠️ 弱")
    else:
        print(f"    → ❌ 无效")
    return ic_mean, ir

# v82a: 120日平均换手率
turn_120 = turnover.rolling(120, min_periods=90).mean()
calc_ic(turn_120, fwd_ret, "v82a: 120日平均换手率")

# v82b: 60日年化收益方差
ret_daily = close.pct_change(1)
var_60 = ret_daily.rolling(60, min_periods=40).var() * 252
calc_ic(var_60, fwd_ret, "v82b: 60日年化收益方差")

# v82c: 10日MA偏离度
ma10 = close.rolling(10).mean()
ma10_dev = (close - ma10) / ma10
calc_ic(ma10_dev, fwd_ret, "v82c: 10日MA偏离度")

# v82d: 价格变异系数(20日)
std20 = close.rolling(20).std()
mean20 = close.rolling(20).mean()
price_cv = std20 / mean20
calc_ic(price_cv, fwd_ret, "v82d: 价格变异系数(20日)")

# v82e: BBI动量
ma3 = close.rolling(3).mean()
ma6 = close.rolling(6).mean()
ma12 = close.rolling(12).mean()
ma24 = close.rolling(24).mean()
bbi = (ma3 + ma6 + ma12 + ma24) / 4
bbi_momentum = bbi / close
calc_ic(bbi_momentum, fwd_ret, "v82e: BBI动量")

# v82f: ARBR简化(14日)
up_vol = (ret_daily > 0).astype(float) * turnover
ar = up_vol.rolling(14).sum() / turnover.rolling(14).sum()
calc_ic(ar, fwd_ret, "v82f: ARBR(14日上涨成交量占比)")

# v82g: 流动性比率
vol_ma20 = turnover.rolling(20).mean()
vol_ma60 = turnover.rolling(60).mean()
liq_ratio = vol_ma20 / vol_ma60
calc_ic(liq_ratio, fwd_ret, "v82g: 流动性比率(20日/60日)")

# v82h: 10日/120日换手率比
turn_10 = turnover.rolling(10).mean()
turn_ratio = turn_10 / turn_120
calc_ic(turn_ratio, fwd_ret, "v82h: 10日/120日换手率比")

conn.close()
print("\n" + "=" * 70)
print("复现验证完成")
