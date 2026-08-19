"""v82b IC分析: 60日年化收益方差"""
import pandas as pd
import numpy as np
import sqlite3
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

print("=" * 60)
print("v82b: 60日年化收益方差 IC分析")
print("=" * 60)

db = sqlite3.connect('/root/a-share-quant-sim/data/quant_stocks.db')
pool = pd.read_sql_query('SELECT code FROM stock_pool_zz1800 WHERE is_active=1', db, index_col='code')
codes = pool.index.tolist()

placeholders = ','.join(['?' for _ in codes])
kline = pd.read_sql_query(
    f'SELECT code, date, close FROM daily_kline WHERE date >= "2020-01-01" AND code IN ({placeholders})',
    db, params=codes
)
kline['date'] = pd.to_datetime(kline['date'])
kline = kline.sort_values(['date', 'code'])
close_panel = kline.pivot(index='date', columns='code', values='close')

# 日收益率
daily_ret = close_panel.pct_change()

# 60日年化收益方差 = std(daily_ret, 60d) * sqrt(252)
factor = daily_ret.rolling(60, min_periods=40).std() * np.sqrt(252)
print(f"因子面板: {factor.shape}")

# 未来5日收益率
returns_5d = close_panel.pct_change(5).shift(-5)

# IC分析
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

print(f"\nIC Mean: {ic_mean:.4f} | IC Std: {ic_std:.4f} | IR: {ir:.4f} | P(IC>0): {p_pos:.1%}")
effective = abs(ic_mean) > 0.03 and abs(ir) > 0.3
print(f"判定: {'✅ 有效' if effective else '❌ 无效'}")

ic_df.to_csv('/root/a-share-quant-sim/alpha-research/reports/xuntou/v82b_60d_vol_ic.csv')
