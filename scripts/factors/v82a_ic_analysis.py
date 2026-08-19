"""v82a IC分析: 120日平均换手率 (优化版)"""
import pandas as pd
import numpy as np
import sqlite3
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

print("=" * 60)
print("v82a: 120日平均换手率 IC分析")
print("=" * 60)

db = sqlite3.connect('/root/a-share-quant-sim/data/quant_stocks.db')

# 1. 加载zz1800成分股
pool = pd.read_sql_query(
    'SELECT code, float_shares FROM stock_pool_zz1800 WHERE is_active=1',
    db, index_col='code'
)
codes = pool.index.tolist()
print(f"zz1800成分股: {len(codes)}只")

# 2. 加载日线数据 (只取2020年后)
placeholders = ','.join(['?' for _ in codes])
kline = pd.read_sql_query(
    f'SELECT code, date, close, volume FROM daily_kline WHERE date >= "2020-01-01" AND code IN ({placeholders})',
    db, params=codes
)
kline['date'] = pd.to_datetime(kline['date'])
print(f"日线数据: {len(kline)}条, 股票数: {kline['code'].nunique()}")

# 3. pivot成面板
kline = kline.sort_values(['date', 'code'])
close_panel = kline.pivot(index='date', columns='code', values='close')
volume_panel = kline.pivot(index='date', columns='code', values='volume')

fs = pool['float_shares']
turnover = volume_panel.mul(100).div(fs, axis=1)

# 4. 计算120日平均换手率
factor = turnover.rolling(120, min_periods=80).mean()
print(f"因子面板: {factor.shape}")

# 5. 未来5日收益率
returns_5d = close_panel.pct_change(5).shift(-5)

# 6. 逐日计算Rank IC (采样提速: 每5天算一次)
common_dates = factor.index.intersection(returns_5d.index)
sample_dates = common_dates[::5]  # 每5天采样
common_codes = factor.columns.intersection(returns_5d.columns)

ic_series = []
for dt in sample_dates:
    f = factor.loc[dt, common_codes]
    r = returns_5d.loc[dt, common_codes]
    valid = f.notna() & r.notna()
    if valid.sum() < 50:
        continue
    ic, _ = stats.spearmanr(f[valid], r[valid])
    ic_series.append({'date': dt, 'ic': ic})

ic_df = pd.DataFrame(ic_series).set_index('date')
print(f"IC序列长度: {len(ic_df)} (每5天采样)")

# 7. IC统计
ic_mean = ic_df['ic'].mean()
ic_std = ic_df['ic'].std()
ir = ic_mean / ic_std if ic_std > 0 else 0
p_positive = (ic_df['ic'] > 0).mean()

print(f"\n{'='*60}")
print(f"IC Mean:    {ic_mean:.4f}")
print(f"IC Std:     {ic_std:.4f}")
print(f"IR:         {ir:.4f}")
print(f"P(IC>0):    {p_positive:.1%}")
print(f"{'='*60}")

effective = abs(ic_mean) > 0.03 and abs(ir) > 0.3
print(f"\n判定: {'✅ 有效' if effective else '❌ 无效'}")
print(f"  |IC Mean|={abs(ic_mean):.4f} {'>' if abs(ic_mean)>0.03 else '<='} 0.03")
print(f"  |IR|={abs(ir):.4f} {'>' if abs(ir)>0.3 else '<='} 0.3")

ic_df.to_csv('/root/a-share-quant-sim/alpha-research/reports/xuntou/v82a_120d_turnover_ic.csv')
print("IC序列已保存")
