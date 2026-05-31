#!/usr/bin/env python3
"""
准日内波动因子调研
==================
利用日线 OHLC 信息构造"日内结构"因子：
1. 上影线 / 下影线（压力的信号）
2. 实体比例（收盘-开盘 / 高低）
3. 日内动量方向（收盘相对位置）
4. 跳空缺口
5. 日内波动率 / 收盘价比
6. 连续同方向天数
7. 放量突破（价格新高 + 成交量新高）

与已有因子的相关性 → IC 分析
"""
import sys, os, time, warnings
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
# 加载 OHLCV
data = {}
for c in codes:
    f = os.path.join(DAILY_DIR, f"{c}.csv")
    if os.path.exists(f):
        df = pd.read_csv(f, index_col='date', parse_dates=True)
        if len(df) > 100 and 'high' in df.columns and 'low' in df.columns and 'open' in df.columns:
            data[c] = df

print(f"  有效股票: {len(data)}", flush=True)

close_p = pd.DataFrame({c: d['close'] for c, d in data.items()})
open_p = pd.DataFrame({c: d['open'] for c, d in data.items()})
high_p = pd.DataFrame({c: d['high'] for c, d in data.items()})
low_p = pd.DataFrame({c: d['low'] for c, d in data.items()})
vol_p = pd.DataFrame({c: d['volume'] for c, d in data.items()})
amt_p = pd.DataFrame({c: d['amount'] for c, d in data.items()})

dates = close_p.dropna(how='all').index.sort_values()
stocks = list(close_p.columns)
eps = 1e-10

# ── 构造日内因子 ──────────────────────────────────────────────────
print("构造日内因子...", flush=True)

upper_shadow = high_p - np.maximum(close_p, open_p)
lower_shadow = np.minimum(close_p, open_p) - low_p
body = close_p - open_p
range_ = high_p - low_p

factors_new = {}

# 1. 上影线比例（上影线 / 全价区间）→ 上影线长 = 卖压
factors_new['upper_shadow_ratio'] = upper_shadow / (range_ + eps)

# 2. 下影线比例 → 下影线长 = 买盘支撑
factors_new['lower_shadow_ratio'] = lower_shadow / (range_ + eps)

# 3. 实体比例 → 强趋势
factors_new['body_ratio'] = body.abs() / (range_ + eps)

# 4. 收盘位置（收盘在高低区间的相对位置）
factors_new['close_position'] = (close_p - low_p) / (range_ + eps)

# 5. 日内动量方向（+1=收高, -1=收低, 0=十字星）
factors_new['intraday_direction'] = np.sign(body)

# 6. 跳空缺口比例
prev_close = close_p.shift(1)
factors_new['gap_ratio'] = (open_p - prev_close) / (prev_close + eps)

# 7. 实体方向（标准化）
factors_new['body_normalized'] = body / (close_p + eps)

# 8. 日内波动率（range / close）→ 已有 amplitude
factors_new['intraday_range'] = range_ / (close_p + eps)

# 9. 上下影线不对称性
factors_new['shadow_asymmetry'] = (upper_shadow - lower_shadow) / (range_ + eps)

# 10. 连续同方向天数（向量化）
direction = np.sign(close_p.diff())
consecutive = pd.DataFrame(0, index=dates, columns=stocks)
for c in direction.columns:
    s = direction[c]
    # 计算连续同号
    sign_change = (s != s.shift(1)).cumsum()
    groups = s.groupby(sign_change)
    consecutive[c] = s.groupby(sign_change).cumcount() + 1
    consecutive[c] = consecutive[c] * s  # 赋予正负号
factors_new['consecutive_direction'] = consecutive

# 11. 放量突破：价格 20 日新高 + 成交量 20 日新高
price_20h = close_p.rolling(20).max()
vol_20h = vol_p.rolling(20).max()
factors_new['breakout_volume'] = ((close_p >= price_20h * 0.98) & (vol_p >= vol_20h * 0.8)).astype(float) * np.sign(body)

# 12. 缩量回调：价格下跌 + 成交量缩小
factors_new['pullback_low_vol'] = ((close_p < close_p.shift(1)) & (vol_p < vol_p.rolling(20).mean() * 0.7)).astype(float)

# 13. 尾盘拉升（收盘位置 > 0.8）
factors_new['late_rally'] = (factors_new['close_position'] > 0.8).astype(float) * factors_new['body_normalized']

# 14. 开盘冲高回落（上影线 > 2x 实体）
factors_new['open_reversal'] = ((upper_shadow > 2 * body.abs()) & (body < 0)).astype(float)

# 15. 长下影线反转（下影线 > 2x 实体 + 收阳）
factors_new['hammer'] = ((lower_shadow > 2 * body.abs()) & (body > 0)).astype(float)

print(f"  新因子: {len(factors_new)} 个", flush=True)

# ── 加载已有因子 ──────────────────────────────────────────────────
print("加载已有因子...", flush=True)
factors_all = calc_factors_panel(close_p, vol_p, amt_p)
factors_all.update(factors_new)
print(f"  总因子: {len(factors_all)}", flush=True)

# ── IC 分析 ───────────────────────────────────────────────────────
print("\nIC 分析...", flush=True)
fwd_5 = close_p.pct_change(5).shift(-5)
fwd_20 = close_p.pct_change(20).shift(-20)

def calc_ic_stats(factor_df, fwd):
    ics = []
    for dt in factor_df.index:
        if dt not in fwd.index: continue
        fv = factor_df.loc[dt].dropna()
        rv = fwd.loc[dt].dropna()
        common = fv.index.intersection(rv.index)
        if len(common) < 10: continue
        corr = np.corrcoef(fv[common], rv[common])[0,1]
        if not np.isnan(corr): ics.append(corr)
    if len(ics) < 5: return None
    return {'IC': round(np.mean(ics),4), 'IR': round(np.mean(ics)/np.std(ics),4), 
            'pos': round(sum(1 for x in ics if x>0)/len(ics),3), 'N': len(ics)}

# 新因子 IC
print(f"\n{'因子':>25} | {'IC5':>7} | {'IR5':>7} | {'IC20':>7} | {'IR20':>7} | {'+%':>5}")
print("-"*70)

new_ic = {}
for name, df in factors_new.items():
    r5 = calc_ic_stats(df, fwd_5)
    r20 = calc_ic_stats(df, fwd_20)
    if r5:
        new_ic[name] = {'5d': r5, '20d': r20}
        ic20s = f"{r20['IC']:+.4f} | {r20['IR']:+.4f}" if r20 else "   —    |    —  "
        print(f"  {name:>23} | {r5['IC']:+.4f} | {r5['IR']:+.4f} | {ic20s} | {r5['pos']:>5.1%}")

# 与已有 v8 因子对比
print(f"\n\n与 v8 因子 IC 对比 (IR5):")
v8w = STRATEGY_PROFILES['v8_all_icir'].factor_weights
v8_ics = {}
for name in v8w:
    if name in factors_all:
        r5 = calc_ic_stats(factors_all[name], fwd_5)
        if r5: v8_ics[name] = r5['IR']

print(f"\n  v8 因子 IR5:")
for name, ir in sorted(v8_ics.items(), key=lambda x: x[1], reverse=True):
    print(f"    {name:>20}: {ir:+.4f}")

if new_ic:
    print(f"\n  新日内因子 IR5:")
    for name, r in sorted(new_ic.items(), key=lambda x: x[1]['5d']['IR'], reverse=True):
        print(f"    {name:>20}: {r['5d']['IR']:+.4f}")

# 相关性分析
print(f"\n\n新因子与现有因子相关性（最新截面）:")
latest = dates[-1]
for nn in list(new_ic.keys())[:5]:
    if nn not in factors_all: continue
    vals_new = factors_all[nn].loc[latest].dropna() if latest in factors_all[nn].index else None
    if vals_new is None or len(vals_new) < 10: continue
    print(f"\n  {nn} vs v8 因子:")
    for vn in list(v8w.keys())[:8]:
        if vn not in factors_all: continue
        vals_v8 = factors_all[vn].loc[latest].dropna() if latest in factors_all[vn].index else None
        if vals_v8 is None: continue
        common = vals_new.index.intersection(vals_v8.index)
        if len(common) < 10: continue
        corr = np.corrcoef(vals_new[common], vals_v8[common])[0,1]
        if not np.isnan(corr):
            print(f"    {nn[:12]:>12} vs {vn:>18}: {corr:+.4f}")

# ── 日内因子长期 IC 稳定性 ────────────────────────────────────────
if new_ic:
    print(f"\n\n日内因子 IC 稳定性（按年）:")
    for year in ['2021','2022','2023','2024','2025']:
        for nn in list(new_ic.keys())[:5]:
            df = factors_all[nn]
            y_ics = []
            for dt in dates:
                if dt.strftime('%Y') != year: continue
                if dt not in fwd_5.index or dt not in df.index: continue
                fv = df.loc[dt].dropna()
                rv = fwd_5.loc[dt].dropna()
                common = fv.index.intersection(rv.index)
                if len(common) < 10: continue
                c = np.corrcoef(fv[common], rv[common])[0,1]
                if not np.isnan(c): y_ics.append(c)
            if y_ics:
                print(f"    {nn[:15]:>15} {year}: IC={np.mean(y_ics):+.4f} ({len(y_ics)}周)")

print("\n完成")
