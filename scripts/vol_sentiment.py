#!/usr/bin/env python3
"""交易量/换手率情绪因子 IC 分析"""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")
import numpy as np, pandas as pd
from core.config import STRATEGY_PROFILES

codes = sorted([f.replace(".csv","") for f in os.listdir(DAILY_DIR) if f.endswith(".csv")])
vp, volp = {}, {}
for c in codes:
    f = os.path.join(DAILY_DIR, f"{c}.csv")
    if os.path.exists(f):
        d = pd.read_csv(f, index_col='date', parse_dates=True)['2021-01-01':]
        if len(d)>100: vp[c]=d['close']; volp[c]=d['volume']
close = pd.DataFrame(vp); vol = pd.DataFrame(volp)
dates = close.dropna(how='all').index.sort_values()
print(f"面板: {close.shape}")

ret = close.pct_change()
eps = 1e-10

# 成交量情绪因子
factors = {}
factors['vol_ratio_20'] = vol / (vol.rolling(20).mean() + eps)
factors['vol_ratio_5'] = vol / (vol.rolling(5).mean() + eps)
factors['vol_ratio_60'] = vol / (vol.rolling(60).mean() + eps)

# 换手率截面 z-score
turn = vol / (vol.rolling(20).mean() + eps)
turn_mean = turn.mean(axis=1); turn_std = turn.std(axis=1)
factors['turnover_z'] = (turn.sub(turn_mean, axis=0)).div(turn_std + eps, axis=0)

# 量价背离：价跌+放量
factors['pv_diverge_bear'] = -ret.rolling(5).sum() * (vol / (vol.rolling(20).mean() + eps)).rolling(5).mean()

# 缩量上涨
factors['low_vol_up'] = ret.rolling(5).sum() / ((vol / (vol.rolling(20).mean() + eps)).rolling(5).mean() + eps)

# 放量突破
high_20 = close.rolling(20).max(); vol_20h = vol.rolling(20).max()
factors['vol_breakout'] = ((close >= high_20*0.98) & (vol >= vol_20h*0.8)).astype(float)

# 天量/缩量
factors['extreme_vol'] = (vol / (vol.rolling(20).mean() + eps) > 3).astype(float)
factors['extreme_low_vol'] = (vol / (vol.rolling(20).mean() + eps) < 0.3).astype(float)

# 量价同向
factors['vp_align'] = np.sign(ret.rolling(5).sum()) * np.sign((vol/(vol.rolling(20).mean()+eps)).rolling(5).mean() - 1)

# 成交额比（已有 amount_ratio）
factors['amount_ratio'] = (close*vol) / ((close*vol).rolling(20).mean() + eps)

# 量能趋势（量比的变化率）
factors['vol_accel'] = (vol / (vol.rolling(20).mean() + eps)).diff(5)

# 放量下跌（恐慌）dump
factors['panic_sell'] = ((ret < -0.03) & (vol > vol.rolling(20).mean() * 2)).astype(float)

# 缩量筑底（量比持续低位）
vol_low = (vol < vol.rolling(20).mean() * 0.5).rolling(10).sum()
factors['low_vol_accum'] = vol_low / 10

fwd_5 = close.pct_change(5).shift(-5)

def calc_ic(df):
    ics = []
    for dt in df.index:
        if dt not in fwd_5.index: continue
        fv = df.loc[dt].dropna(); rv = fwd_5.loc[dt].dropna()
        common = fv.index.intersection(rv.index)
        if len(common)<10: continue
        c = np.corrcoef(fv[common], rv[common])[0,1]
        if not np.isnan(c): ics.append(c)
    if len(ics)<5: return None
    return {'IC': round(np.mean(ics),4),'IR': round(np.mean(ics)/np.std(ics),4),'N':len(ics)}

W = STRATEGY_PROFILES['v6b_8f_pos_ic'].factor_weights

print("\n量价情绪因子 IC:")
for name, df in factors.items():
    r = calc_ic(df)
    if r:
        tag = ' [在v6b中]' if name in W else ''
        print(f"  {name:>22}: IC5={r['IC']:+.4f}, IR5={r['IR']:+.4f}, N={r['N']}{tag}")

print("\n与 v6b 8因子 IC 对比:")
v8w = STRATEGY_PROFILES['v8_all_icir'].factor_weights
from core.factors import calc_factors_panel
all_fac = calc_factors_panel(close, vol, close*vol)
for name in sorted(W.keys(), key=lambda x: abs(W.get(x,0)), reverse=True):
    if name in all_fac:
        r = calc_ic(all_fac[name])
        if r:
            print(f"  {name:>22}: IC5={r['IC']:+.4f}, IR5={r['IR']:+.4f} (w={W[name]:+.4f})")
