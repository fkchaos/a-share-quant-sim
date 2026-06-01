#!/usr/bin/env python3
"""
基本面因子调研（简化版）
========================
用腾讯 API 获取截面财务数据 + 已有日K因子对比 IC
因子：EP(1/PE), BP(1/PB), log(市值), 换手率
"""
import sys, os, re, time, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

import numpy as np
import pandas as pd
import urllib.request

# ── 股票列表 ──────────────────────────────────────────────────────
codes = sorted([f.replace(".csv","") for f in os.listdir(DAILY_DIR) if f.endswith(".csv")])
print(f"股票池: {len(codes)} 只")

# ── 腾讯 API 获取财务数据 ─────────────────────────────────────────
def fetch_tencent_batch(code_batch):
    """获取一批股票的腾讯实时数据"""
    symbols = ['sh'+c if c.startswith('6') else 'sz'+c for c in code_batch]
    url = f'http://qt.gtimg.cn/q={",".join(symbols)}'
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    resp = urllib.request.urlopen(req, timeout=10)
    return resp.read().decode('gbk')

def parse_tencent(data):
    results = {}
    for line in data.strip().split('\n'):
        m = re.search(r'"(.+?)"', line)
        if not m: continue
        fields = m.group(1).split('~')
        if len(fields) < 50: continue
        code = fields[2]
        try:
            results[code] = {
                'pe':  float(fields[39]) if fields[39] and float(fields[39]) > 0 else np.nan,
                'pb':  float(fields[46]) if fields[46] and float(fields[46]) > 0 else np.nan,
                'mv':  float(fields[44]) if fields[44] and float(fields[44]) > 0 else np.nan,  # 总市值(亿)
            }
        except: pass
    return results

print("\n获取腾讯财务数据...")
fund_data = {}
for i in range(0, len(codes), 50):
    batch = codes[i:i+50]
    try:
        data = fetch_tencent_batch(batch)
        fund_data.update(parse_tencent(data))
    except Exception as e:
        print(f"  批次 {i//50+1} 失败: {e}")
    if i % 200 == 0 and i > 0:
        print(f"  {i}/{len(codes)}")

print(f"  成功: {len(fund_data)} 只")

# ── 加载价格面板 ──────────────────────────────────────────────────
print("\n加载价格面板...")
close_panels, vol_panels = {}, {}
for code in codes:
    f = os.path.join(DAILY_DIR, f"{code}.csv")
    if os.path.exists(f):
        df = pd.read_csv(f, index_col='date', parse_dates=True)
        df = df[df.index >= '2021-01-01']
        if len(df) > 100:
            close_panels[code] = df['close']
            vol_panels[code] = df['volume']

close_panel = pd.DataFrame(close_panels)
vol_panel = pd.DataFrame(vol_panels)
amt_panel = close_panel * vol_panel
dates = close_panel.dropna(how='all').index.sort_values()
print(f"  面板: {close_panel.shape}")

# ── 计算因子 ──────────────────────────────────────────────────────
from core.factors import calc_factors_panel
from core.config import STRATEGY_PROFILES

print("计算因子...")
all_factors = calc_factors_panel(close_panel, vol_panel, amt_panel)

# 基本面因子（截面静态 → 扩展到日频）
fund_df = pd.DataFrame(fund_data).T  # index=code, columns=pe/pb/mv

# EP = 1/PE
ep_map = (1.0 / fund_df['pe']).clip(-5, 5).to_dict()
all_factors['ep'] = pd.DataFrame({code: ep_map.get(code, np.nan) for code in close_panel.columns}, index=dates).apply(lambda x: x)

# BP = 1/PB
bp_map = (1.0 / fund_df['pb']).clip(-5, 5).to_dict()
all_factors['bp'] = pd.DataFrame({code: bp_map.get(code, np.nan) for code in close_panel.columns}, index=dates).apply(lambda x: x)

# log(市值)
mv_map = np.log(fund_df['mv'].clip(1)).to_dict()
all_factors['log_mv'] = pd.DataFrame({code: mv_map.get(code, np.nan) for code in close_panel.columns}, index=dates).apply(lambda x: x)

print(f"  因子数: {len(all_factors)}")

# ── IC 分析 ───────────────────────────────────────────────────────
print("\nIC 分析...")

fwd_5 = close_panel.pct_change(5).shift(-5)
fwd_20 = close_panel.pct_change(20).shift(-20)

def calc_ic(factor_df, fwd):
    ics = []
    for dt in factor_df.index:
        if dt not in fwd.index: continue
        fv = factor_df.loc[dt].dropna()
        rv = fwd.loc[dt].dropna()
        common = fv.index.intersection(rv.index)
        if len(common) < 10: continue
        c = np.corrcoef(fv[common], rv[common])[0,1]
        if not np.isnan(c): ics.append(c)
    if len(ics) < 5: return None
    return {'mean': round(np.mean(ics),4), 'std': round(np.std(ics),4),
            'ir': round(np.mean(ics)/np.std(ics),4), 'pos': round(sum(1 for x in ics if x>0)/len(ics),3), 'n': len(ics)}

v8 = STRATEGY_PROFILES['v8_all_icir'].factor_weights

print(f"\n{'因子':>15} | {'IC5':>7} | {'IR5':>7} | {'IC20':>7} | {'IR20':>7} | {'+%':>5} | {'v8权重':>8}")
print("-" * 75)

results = {}
for fac in sorted(set(list(v8.keys()) + ['ep', 'bp', 'log_mv'])):
    if fac not in all_factors: continue
    r5 = calc_ic(all_factors[fac], fwd_5)
    r20 = calc_ic(all_factors[fac], fwd_20)
    if not r5: continue
    results[fac] = {'5d': r5, '20d': r20}
    w = f"{v8.get(fac, 0):+.4f}" if fac in v8 else "  —  "
    ic20s = f"{r20['mean']:+.4f} | {r20['ir']:+.4f}" if r20 else "    —    |     —   "
    print(f"  {fac:>13} | {r5['mean']:+.4f} | {r5['ir']:+.4f} | {ic20s} | {r5['pos']:>5.1%} | {w}")

# ── 排名 ──────────────────────────────────────────────────────────
print("\n\nIC_IR 排名 (|IR5|, 降序):")
for fac, r in sorted(results.items(), key=lambda x: abs(x[1]['5d']['ir']), reverse=True)[:15]:
    tag = " [基本面]" if fac in ('ep','bp','log_mv') else ""
    print(f"  {fac:>15}: IC5={r['5d']['mean']:+.4f}, IR5={r['5d']['ir']:+.4f}{tag}")

# ── 相关性 ────────────────────────────────────────────────────────
print("\n\n基本面因子与量价因子相关性 (最新截面):")
latest = all_factors[list(all_factors.keys())[0]].index[-1]
cross = {}
for fac in ['ep','bp','log_mv'] + [f for f in v8.keys() if f in all_factors]:
    if fac in all_factors:
        v = all_factors[fac].loc[latest].dropna()
        if len(v) > 10: cross[fac] = v
cdf = pd.DataFrame(cross)
for ff in ['ep','bp','log_mv']:
    if ff not in cdf.columns: continue
    print(f"\n  {ff} 与 v8 因子:")
    for pf in [f for f in v8.keys() if f in cdf.columns]:
        corr = cdf[ff].corr(cdf[pf])
        if not np.isnan(corr):
            print(f"    {ff} vs {pf:>15}: {corr:+.4f}")

# 保存
out = os.path.join(DATA_DIR, 'backtest_results', 'fundamental_ic.json')
os.makedirs(os.path.dirname(out), exist_ok=True)
with open(out,'w') as f: json.dump(results, f, ensure_ascii=False, indent=2)
print(f"\n结果已保存: {out}")
print("\n完成")
