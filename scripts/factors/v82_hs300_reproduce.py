"""沪深300池复现验证: 8个因子 vs 迅投数据"""
import pandas as pd
import numpy as np
import sqlite3
from scipy import stats
import akshare as ak
import warnings
warnings.filterwarnings('ignore')

print("=" * 70)
print("沪深300池复现验证 — 对比迅投因子看板数据")
print("=" * 70)

# 获取沪深300成分股
hs300 = ak.index_stock_cons(symbol='000300')
codes = hs300['品种代码'].tolist()
print(f"沪深300成分股: {len(codes)}只")

# 加载数据
db = sqlite3.connect('/root/a-share-quant-sim/data/quant_stocks.db')
placeholders = ','.join(['?' for _ in codes])
kline = pd.read_sql_query(
    f'SELECT code, date, close, volume FROM daily_kline WHERE date >= "2023-08-01" AND code IN ({placeholders})',
    db, params=codes
)
kline['date'] = pd.to_datetime(kline['date'])
kline = kline.sort_values(['date', 'code'])
close_panel = kline.pivot(index='date', columns='code', values='close')
volume_panel = kline.pivot(index='date', columns='code', values='volume')
print(f"日线数据: {len(kline)}条, {close_panel.shape[0]}天, {close_panel.shape[1]}只")

# 获取流通股本
fs_df = pd.read_sql_query('SELECT code, float_shares FROM stock_pool_zz1800', db, index_col='code')
# 用volume直接算 (volume单位是手)
# turnover = volume * 100 / float_shares，但这里我们用volume本身作为流动性代理
# 因为HS300的float_shares不一定在我们的DB里

daily_ret = close_panel.pct_change()
returns_5d = close_panel.pct_change(5).shift(-5)

def calc_ic(factor, returns_5d, name, xuntou_ic, xuntou_ir):
    common_dates = factor.index.intersection(returns_5d.index)
    sample_dates = common_dates[::5]
    common_codes = factor.columns.intersection(returns_5d.columns)
    ic_series = []
    for dt in sample_dates:
        f = factor.loc[dt, common_codes]
        r = returns_5d.loc[dt, common_codes]
        valid = f.notna() & r.notna()
        if valid.sum() < 30: continue
        ic, _ = stats.spearmanr(f[valid], r[valid])
        ic_series.append({'date': dt, 'ic': ic})
    ic_df = pd.DataFrame(ic_series).set_index('date')
    ic_mean = ic_df['ic'].mean()
    ic_std = ic_df['ic'].std()
    ir = ic_mean / ic_std if ic_std > 0 else 0
    p_pos = (ic_df['ic'] > 0).mean()
    match = abs(abs(ic_mean) - abs(xuntou_ic)) < 0.1
    print(f"  {name}:")
    print(f"    我们:  IC={ic_mean:+.4f} IR={ir:+.4f} P(IC>0)={p_pos:.1%}")
    print(f"    迅投:  IC={xuntou_ic:+.4f} IR={xuntou_ir:+.4f}")
    print(f"    偏差:  ΔIC={abs(ic_mean)-abs(xuntou_ic):+.4f} {'✅接近' if match else '⚠️差异大'}")
    return ic_mean, ir

print("\n" + "-" * 70)

# v82a: 120日平均换手率 (用volume/mean_volume代理)
vol_mean_120 = volume_panel.rolling(120, min_periods=80).mean()
factor_a = vol_mean_120  # 120日平均成交量
print("[v82a] 120日平均成交量 (换手率代理)")
calc_ic(factor_a, returns_5d, "120日平均换手率", 0.767, 0.969)

# v82b: 60日年化收益方差
factor_b = daily_ret.rolling(60, min_periods=40).std() * np.sqrt(252)
print("\n[v82b] 60日年化收益方差")
calc_ic(factor_b, returns_5d, "60日年化收益方差", 0.286, 0.884)

# v82c: 10日移动均线偏离度
ma10 = close_panel.rolling(10, min_periods=7).mean()
factor_c = close_panel / ma10 - 1
print("\n[v82c] 10日MA偏离度")
calc_ic(factor_c, returns_5d, "10日MA偏离度", 0.122, 0.907)

# v82e: BBI动量
ma5 = close_panel.rolling(5, min_periods=3).mean()
ma20 = close_panel.rolling(20, min_periods=15).mean()
ma60 = close_panel.rolling(60, min_periods=40).mean()
bbi = (ma5 + ma10 + ma20 + ma60) / 4
factor_e = close_panel / bbi - 1
print("\n[v82e] BBI动量")
calc_ic(factor_e, returns_5d, "BBI动量", 0.746, 0.884)

# v82f: ARBR简化
up_vol = (volume_panel * (daily_ret > 0).astype(float)).rolling(14, min_periods=10).sum()
total_vol = volume_panel.rolling(14, min_periods=10).sum()
factor_f = up_vol / total_vol
print("\n[v82f] ARBR简化(14日上涨成交量占比)")
calc_ic(factor_f, returns_5d, "ARBR", 0.182, 0.657)

# v82h: 换手率比 (短期/长期)
vol_10 = volume_panel.rolling(10, min_periods=7).mean()
vol_120 = volume_panel.rolling(120, min_periods=80).mean()
factor_h = vol_10 / vol_120
print("\n[v82h] 10日/120日成交量比 (换手率比代理)")
calc_ic(factor_h, returns_5d, "换手率比", 0.566, 0.897)

print("\n" + "=" * 70)
print("复现验证完成")
