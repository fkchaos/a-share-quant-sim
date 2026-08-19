"""批量IC分析: v82c-v82h"""
import pandas as pd
import numpy as np
import sqlite3
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

def calc_ic(factor, returns_5d, name):
    common_dates = factor.index.intersection(returns_5d.index)
    sample_dates = common_dates[::5]
    common_codes = factor.columns.intersection(returns_5d.columns)
    ic_series = []
    for dt in sample_dates:
        f = factor.loc[dt, common_codes]
        r = returns_5d.loc[dt, common_codes]
        valid = f.notna() & r.notna()
        if valid.sum() < 50: continue
        ic, _ = stats.spearmanr(f[valid], r[valid])
        ic_series.append({'date': dt, 'ic': ic})
    ic_df = pd.DataFrame(ic_series).set_index('date')
    ic_mean = ic_df['ic'].mean()
    ic_std = ic_df['ic'].std()
    ir = ic_mean / ic_std if ic_std > 0 else 0
    p_pos = (ic_df['ic'] > 0).mean()
    effective = abs(ic_mean) > 0.03 and abs(ir) > 0.3
    print(f"  {name}: IC={ic_mean:.4f} IR={ir:.4f} P(IC>0)={p_pos:.1%} → {'✅有效' if effective else '❌无效'}")
    ic_df.to_csv(f'/root/a-share-quant-sim/alpha-research/reports/xuntou/v82_{name}_ic.csv')
    return ic_mean, ir, p_pos, effective

db = sqlite3.connect('/root/a-share-quant-sim/data/quant_stocks.db')
pool = pd.read_sql_query('SELECT code FROM stock_pool_zz1800 WHERE is_active=1', db, index_col='code')
codes = pool.index.tolist()
placeholders = ','.join(['?' for _ in codes])

kline = pd.read_sql_query(
    f'SELECT code, date, close, volume FROM daily_kline WHERE date >= "2020-01-01" AND code IN ({placeholders})',
    db, params=codes
)
kline['date'] = pd.to_datetime(kline['date'])
kline = kline.sort_values(['date', 'code'])
close_panel = kline.pivot(index='date', columns='code', values='close')
volume_panel = kline.pivot(index='date', columns='code', values='volume')

fs = pd.read_sql_query('SELECT code, float_shares FROM stock_pool_zz1800', db, index_col='code')['float_shares']
turnover = volume_panel.mul(100).div(fs, axis=1)
daily_ret = close_panel.pct_change()
returns_5d = close_panel.pct_change(5).shift(-5)

print("=" * 60)
print("v82c-h 批量IC分析")
print("=" * 60)

# v82c: 10日移动均线 (price/MA10 偏离度)
ma10 = close_panel.rolling(10, min_periods=7).mean()
factor_c = close_panel / ma10 - 1
print("\n[v82c] 10日移动均线偏离度")
calc_ic(factor_c, returns_5d, "v82c_ma10_deviation")

# v82d: 应收账款周转天数 (需要财务数据，先跳过用成交量代理)
# 用20日成交金额标准差 / 均值 作为流动性代理
amt_std = close_panel.rolling(20, min_periods=15).std()
amt_mean = close_panel.rolling(20, min_periods=15).mean()
factor_d = amt_std / amt_mean  # 变异系数
print("\n[v82d] 价格变异系数(20日) 作为流动性代理")
calc_ic(factor_d, returns_5d, "v82d_price_cv20")

# v82e: BBI动量 = (MA5+MA10+MA20+MA60)/4
ma5 = close_panel.rolling(5, min_periods=3).mean()
ma20 = close_panel.rolling(20, min_periods=15).mean()
ma60 = close_panel.rolling(60, min_periods=40).mean()
bbi = (ma5 + ma10 + ma20 + ma60) / 4
factor_e = close_panel / bbi - 1
print("\n[v82e] BBI动量")
calc_ic(factor_e, returns_5d, "v82e_bbi")

# v82f: ARBR (简化版: 上涨日成交量占比)
up_vol = (volume_panel * (daily_ret > 0).astype(float)).rolling(14, min_periods=10).sum()
total_vol = volume_panel.rolling(14, min_periods=10).sum()
factor_f = up_vol / total_vol
print("\n[v82f] ARBR简化(14日上涨成交量占比)")
calc_ic(factor_f, returns_5d, "v82f_arbr")

# v82g: 流动比率代理 (用20日均量/60日均量)
vol20 = volume_panel.rolling(20, min_periods=15).mean()
vol60 = volume_panel.rolling(60, min_periods=40).mean()
factor_g = vol20 / vol60
print("\n[v82g] 流动性比率(20日均量/60日均量)")
calc_ic(factor_g, returns_5d, "v82g_liquidity_ratio")

# v82h: 10日/120日换手率比
turn10 = turnover.rolling(10, min_periods=7).mean()
turn120 = turnover.rolling(120, min_periods=80).mean()
factor_h = turn10 / turn120
print("\n[v82h] 10日/120日换手率比")
calc_ic(factor_h, returns_5d, "v82h_turnover_ratio")

print("\n" + "=" * 60)
print("批量分析完成")
