#!/usr/bin/env python3
"""
市场情绪代理因子（纯日K构造，无需外部API）
============================================
用已有 OHLCV 数据构造情绪代理：

1. 涨跌家数比（市场宽度）
2. 创新高/新低比例
3. 放量/缩量家数比
4. 涨停/跌停家数比（需要涨跌停数据 → 用涨幅>9.5%近似）
5. 波动率偏度（恐惧指数代理）
6. 资金流入比例（成交额加权涨幅）
7. 换手率 z-score（情绪过热/过冷）
8. 连续上涨/下跌家数比
9. Put-Call Ratio 代理（用下跌量/上涨量）
10. 隐含波动率偏度（用个股波动率截面标准差）

与 v6b 因子相关性 → IC 分析
"""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

import numpy as np
import pandas as pd
from core.factors import calc_factors_panel
from core.config import STRATEGY_PROFILES

print("加载数据...", flush=True)
codes = sorted([f.replace(".csv","") for f in os.listdir(DAILY_DIR) if f.endswith(".csv")])
data = {}
for c in codes:
    f = os.path.join(DAILY_DIR, f"{c}.csv")
    if os.path.exists(f):
        df = pd.read_csv(f, index_col='date', parse_dates=True)
        if len(df)>100 and all(k in df.columns for k in ['close','volume','high','low']):
            data[c] = df

stocks = sorted(data.keys())
dates = pd.DatetimeIndex(sorted(set().union(*[df.index for df in data.values()])))
print(f"  {len(stocks)} 只股票, {len(dates)} 个交易日")

close_p = pd.DataFrame({c: data[c]['close'] for c in stocks})
vol_p = pd.DataFrame({c: data[c]['volume'] for c in stocks})
high_p = pd.DataFrame({c: data[c]['high'] for c in stocks})
low_p = pd.DataFrame({c: data[c]['low'] for c in stocks})
amt_p = close_p * vol_p

ret = close_p.pct_change()
eps = 1e-10

# ── 情绪代理因子（截面日频）──────────────────────────────────────
print("构造情绪因子...", flush=True)

sentiment_factors = {}

# 1. 市场宽度：上涨家数占比
up = (ret > 0).sum(axis=1)
total = ret.notna().sum(axis=1)
sentiment_factors['breadth_up_ratio'] = up / (total + eps)

# 2. 创新高比例（20日新高）
high_20 = close_p.rolling(20).max()
new_high = (close_p >= high_20 * 0.98).sum(axis=1) / (total + eps)
sentiment_factors['new_high_ratio'] = new_high

# 3. 创新低比例
low_20 = close_p.rolling(20).min()
new_low = (close_p <= low_20 * 1.02).sum(axis=1) / (total + eps)
sentiment_factors['new_low_ratio'] = new_low

# 4. 涨跌停近似（涨幅>9.5% = 涨停，<-9.5% = 跌停）
limit_up = (ret > 0.095).sum(axis=1) / (total + eps)
limit_down = (ret < -0.095).sum(axis=1) / (total + eps)
sentiment_factors['limit_up_ratio'] = limit_up
sentiment_factors['limit_down_ratio'] = limit_down
sentiment_factors['limit_net'] = limit_up - limit_down

# 5. 放量家数比（量比>1.5）
vol_20 = vol_p.rolling(20).mean()
vol_ratio = vol_p / (vol_20 + eps)
high_vol = (vol_ratio > 1.5).sum(axis=1) / (total + eps)
sentiment_factors['high_vol_ratio'] = high_vol

# 6. 资金流入比例（正收益成交额 / 总成交额）
pos_amt = amt_p.where(ret > 0, 0).sum(axis=1)
total_amt = amt_p.sum(axis=1)
sentiment_factors['money_inflow_ratio'] = pos_amt / (total_amt + eps)

# 7. 截面波动率偏度（个股收益截面偏度 → 恐惧指数）
sentiment_factors['return_skew'] = ret.skew(axis=1)

# 8. 截面收益标准差（市场分歧度）
sentiment_factors['cross_vol'] = ret.std(axis=1)

# 9. 连续上涨家数占比
direction = (ret > 0).astype(int) * 2 - 1
consec_up = pd.DataFrame(0, index=dates, columns=stocks)
for c in stocks[:100]:  # 加速：只用100只
    if c in direction.columns:
        s = direction[c]
        groups = (s != s.shift(1)).cumsum()
        consec_up[c] = (s.groupby(groups).cumcount() + 1) * s

consec_up_ratio = (consec_up > 3).sum(axis=1) / (total + eps)
sentiment_factors['consec_up_ratio'] = consec_up_ratio

# 10. 下跌量/上涨量（Put-Call 代理）
down_vol = vol_p.where(ret < 0, 0).sum(axis=1)
up_vol = vol_p.where(ret > 0, 0).sum(axis=1)
sentiment_factors['put_call_vol'] = down_vol / (up_vol + eps)

# 11. 振幅加权涨幅（情绪强度）
amplitude = (high_p - low_p) / (close_p + eps)
sentiment_factors['amp_weighted_ret'] = (amplitude * ret).mean(axis=1)

# 12. 换手率截面 z-score
turnover = vol_p / (vol_p.rolling(20).mean() + eps)
sentiment_factors['turnover_z'] = ((turnover.sub(turnover.mean(axis=1), axis=0))
                                      .div(turnover.std(axis=1) + eps, axis=0))

print(f"  情绪因子: {len(sentiment_factors)} 个", flush=True)

# ── IC 分析 ──────────────────────────────────────────────────────
print("\nIC 分析...", flush=True)
fwd_5 = close_p.pct_change(5).shift(-5)
fwd_20 = close_p.pct_change(20).shift(-20)

def calc_ic(factor_df, fwd):
    if isinstance(factor_df, pd.Series):
        factor_df = factor_df.to_frame(name='factor')
    ics = []
    for dt in factor_df.index:
        if dt not in fwd.index: continue
        fv = factor_df.loc[dt].dropna()
        rv = fwd.loc[dt].dropna()
        if isinstance(fv, pd.Series):
            common = fv.index.intersection(rv.index)
            if len(common)<10: continue
            corr = np.corrcoef(fv[common], rv[common])[0,1]
        else:
            continue
        if not np.isnan(corr): ics.append(corr)
    if len(ics)<5: return None
    return {'IC': round(np.mean(ics),4),'IR': round(np.mean(ics)/np.std(ics),4),'N':len(ics)}

# 截面因子（截面因子 → 需要对个股做映射）
# 情绪因子是市场级别（每个日期一个值），需要映射到个股
# 方法：用个股相对市场的情绪暴露（beta to sentiment）

print("\n情绪因子 → 市场级因子映射到个股的方法:")
print("  方法1: 直接用市场级因子（截面无区分度 → IC=0）")
print("  方法2: 个股相对情绪 = 个股收益 × 情绪因子")
print("  方法3: 情绪因子变化率 → 择时信号")

# 方法2：构造个股情绪暴露因子
print("\n方法2 IC（个股情绪暴露）:")
for sent_name, sent_df in sentiment_factors.items():
    if isinstance(sent_df, pd.Series):
        # 个股情绪暴露 = 个股收益 × 情绪因子（滞后一期）
        # 或者用换手率作为权重
        # 简化：用个股换手率 z-score × 情绪因子方向
        # 这里直接用市场级因子的变化率作为择时信号
        sent_change = sent_df.diff(5)  # 5日变化
        # 映射到个股：用个股波动率作为暴露代理
        stock_vol = ret.rolling(20).std()
        exposure = sent_change.reindex(stock_vol.index).ffill().values.reshape(-1,1) * stock_vol.values
        exposure_df = pd.DataFrame(exposure, index=stock_vol.index, columns=stock_vol.columns)
        ic5 = calc_ic(exposure_df, fwd_5)
        if ic5:
            print(f"  {sent_name:>22}: IC5={ic5['IC']:+.4f}, IR5={ic5['IR']:+.4f}")

# 方法1：直接用市场级因子（应该 IC≈0）
print("\n方法1 IC（直接用市场级因子，截面无区分）:")
for sent_name, sent_df in list(sentiment_factors.items())[:5]:
    if isinstance(sent_df, pd.Series):
        # 扩展到截面（每只股票相同值）
        expanded = pd.DataFrame(np.tile(sent_df.values, (len(stocks), 1)).T,
                                index=sent_df.index, columns=stocks[:len(sent_df.columns)] if len(sent_df.columns)==len(stocks) else sent_df.index)
        # 实际上市场级因子截面无区分度，IC 应该接近 0
        # 直接计算：把市场级因子值赋给每只股票
        ic5 = calc_ic(sent_df, fwd_5)
        if ic5:
            print(f"  {sent_name:>22}: IC5={ic5['IC']:+.4f}")

print("\n完成")
