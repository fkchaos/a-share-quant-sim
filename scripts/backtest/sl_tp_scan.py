#!/usr/bin/env python3
"""
v20c + v27 止损止盈参数扫描（预计算选股结果版）
"""
import sys, os, numpy as np, pandas as pd, warnings, datetime
warnings.filterwarnings('ignore')

from core.db import load_panel_from_db

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data" + "/backtest_results")
os.makedirs(REPORT_DIR, exist_ok=True)

# ── 加载数据 ──
print("📥 加载数据...")
panels, codes = load_panel_from_db(need_hl=True)
close_panel = panels[0]
volume_panel = panels[1]
amount_panel = panels[2]
high_panel = panels[3]
low_panel = panels[4]
open_panel = panels[5] if len(panels) > 5 else panels[0]
dates = close_panel.index
print(f"  {len(dates)} 天 × {close_panel.shape[1]} 只")

# ══════════════════════════════════════
# 预计算选股结果
# ══════════════════════════════════════
print("\n⏳ 预计算 v20c 选股结果...")
from scripts.strategies.v20_tail_pick import V20Config, calc_tail_pick_factors, select_stocks_tail_pick

factors_v20 = calc_tail_pick_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
pick_v20 = {}
for i, date in enumerate(dates):
    if i % 200 == 0:
        print(f"  v20c {i}/{len(dates)}...")
    try:
        cands = select_stocks_tail_pick(factors_v20, date, close_panel, volume_panel, amount_panel,
                                        high_panel, low_panel, current_holdings=None)
        pick_v20[date] = list(cands) if cands else []
    except:
        pick_v20[date] = []
print(f"  v20c 选股缓存: {len(pick_v20)} 天")

print("⏳ 预计算 v27 选股结果...")
from scripts.strategies.v27_select import calc_factors, select_stocks_v27

factors_v27 = calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
params_v27_base = {'STOP_LOSS': -0.015, 'TAKE_PROFIT': 0.03, 'HOLD_DAYS_MAX': 5, 'MOM_THRESHOLD': 0.02}
pick_v27 = {}
for i, date in enumerate(dates):
    if i % 200 == 0:
        print(f"  v27 {i}/{len(dates)}...")
    try:
        cands = select_stocks_v27(factors_v27, date, current_holdings=None, params=params_v27_base)
        pick_v27[date] = [c[0] for c in cands] if cands else []
    except:
        pick_v27[date] = []
print(f"  v27 选股缓存: {len(pick_v27)} 天")

# ══════════════════════════════════════
# 回测引擎
# ══════════════════════════════════════
def run_v20c(sl, tp, pick_dict):
    V20Config.stop_loss = sl
    V20Config.stop_profit = tp
    V20Config.hold_days_max = 2
    cfg = V20Config()
    ic = cfg.initial_capital
    cash = ic
    holdings = {}
    nav_list = []
    sells = {'TP': 0, 'SL': 0, 'TO': 0}
    nbuy = 0
    pending = []

    for i, date in enumerate(dates):
        if i < 20:
            nav_list.append(ic)
            continue

        pd_ = close_panel.loc[date]
        od_ = open_panel.loc[date]

        # 买入
        if pending and cash > ic * 0.05 and len(holdings) < cfg.max_holdings:
            nb = min(len(pending), cfg.max_daily_buy, cfg.max_holdings - len(holdings))
            ac = cash - ic * 0.05
            ps = ac / nb if nb else 0
            ps = min(ps, ic * cfg.max_position)
            for code in pending[:nb]:
                if code not in od_.index:
                    continue
                bp = od_[code]
                if pd.isna(bp) or bp <= 0:
                    continue
                if i > 0:
                    pc = close_panel.iloc[i-1].get(code)
                    if pc and not pd.isna(pc) and pc > 0 and bp >= pc * 1.09:
                        continue
                adj = bp * 1.0023
                sh = int(ps / adj / 100) * 100
                if sh <= 0:
                    continue
                c = sh * adj
                if c > cash:
                    continue
                cash -= c
                holdings[code] = {'sh': sh, 'cost': bp, 'days': 0}
                nbuy += 1
        pending = []

        # 卖出
        for code in list(holdings.keys()):
            h = holdings[code]
            h['days'] += 1
            if code not in pd_.index:
                continue
            cp = pd_[code]
            if pd.isna(cp) or cp <= 0:
                continue
            pnl = (cp - h['cost']) / h['cost']
            reason = None
            if pnl <= cfg.stop_loss:
                reason = 'SL'
            elif pnl >= cfg.stop_profit:
                reason = 'TP'
            elif h['days'] >= cfg.hold_days_max:
                reason = 'TO'
            if reason:
                sv = h['sh'] * cp * 0.9967
                cash += sv
                sells[reason] += 1
                holdings.pop(code, None)

        # 选股（留给明天）
        cands = pick_dict.get(date, [])
        pending = list(cands[:cfg.max_daily_buy])

        nav = cash
        for code, h in holdings.items():
            if code in pd_.index:
                p = pd_[code]
                if not pd.isna(p) and p > 0:
                    nav += h['sh'] * p
        nav_list.append(nav)

    nav_arr = np.array(nav_list)
    daily_ret = np.diff(nav_arr) / nav_arr[:-1]
    annual_ret = (nav_arr[-1] / nav_arr[0]) ** (252 / len(daily_ret)) - 1
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    peak = np.maximum.accumulate(nav_arr)
    mdd = ((nav_arr - peak) / peak).min()
    total = sells['TP'] + sells['SL'] + sells['TO']

    return {
        'SL': f"{sl:.0%}", 'TP': f"{tp:.0%}",
        '年化': f"{annual_ret:.1%}", '夏普': f"{sharpe:.2f}", '回撤': f"{mdd:.1%}",
        'TP率': f"{sells['TP']/max(total,1):.1%}",
        'SL率': f"{sells['SL']/max(total,1):.1%}",
        '超时率': f"{sells['TO']/max(total,1):.1%}",
        '买入': nbuy,
    }

def run_v27(sl, tp, pick_dict):
    ic = 100000
    cash = ic
    holdings = {}
    nav_list = []
    sells = {'TP': 0, 'SL': 0, 'TO': 0}
    nbuy = 0
    hold_max = 5
    pending = []

    for i, date in enumerate(dates):
        if i < 20:
            nav_list.append(ic)
            continue

        pd_ = close_panel.loc[date]
        od_ = open_panel.loc[date]

        # 买入
        if pending and cash > ic * 0.05 and len(holdings) < 12:
            nb = min(len(pending), 8, 12 - len(holdings))
            ac = cash - ic * 0.05
            ps = ac / nb if nb else 0
            ps = min(ps, ic * 0.25)
            for code in pending[:nb]:
                if code not in od_.index:
                    continue
                bp = od_[code]
                if pd.isna(bp) or bp <= 0:
                    continue
                if i > 0:
                    pc = close_panel.iloc[i-1].get(code)
                    if pc and not pd.isna(pc) and pc > 0 and bp >= pc * 1.09:
                        continue
                adj = bp * 1.0023
                sh = int(ps / adj / 100) * 100
                if sh <= 0:
                    continue
                c = sh * adj
                if c > cash:
                    continue
                cash -= c
                holdings[code] = {'sh': sh, 'cost': bp, 'days': 0}
                nbuy += 1
        pending = []

        # 卖出
        for code in list(holdings.keys()):
            h = holdings[code]
            h['days'] += 1
            if code not in pd_.index:
                continue
            cp = pd_[code]
            if pd.isna(cp) or cp <= 0:
                continue
            pnl = (cp - h['cost']) / h['cost']
            reason = None
            if pnl <= sl:
                reason = 'SL'
            elif pnl >= tp:
                reason = 'TP'
            elif h['days'] >= hold_max:
                reason = 'TO'
            if reason:
                sv = h['sh'] * cp * 0.9967
                cash += sv
                sells[reason] += 1
                holdings.pop(code, None)

        cands = pick_dict.get(date, [])
        pending = list(cands[:8])

        nav = cash
        for code, h in holdings.items():
            if code in pd_.index:
                p = pd_[code]
                if not pd.isna(p) and p > 0:
                    nav += h['sh'] * p
        nav_list.append(nav)

    nav_arr = np.array(nav_list)
    daily_ret = np.diff(nav_arr) / nav_arr[:-1]
    annual_ret = (nav_arr[-1] / nav_arr[0]) ** (252 / len(daily_ret)) - 1
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    peak = np.maximum.accumulate(nav_arr)
    mdd = ((nav_arr - peak) / peak).min()
    total = sells['TP'] + sells['SL'] + sells['TO']

    return {
        'SL': f"{sl:.1%}", 'TP': f"{tp:.0%}",
        '年化': f"{annual_ret:.1%}", '夏普': f"{sharpe:.2f}", '回撤': f"{mdd:.1%}",
        'TP率': f"{sells['TP']/max(total,1):.1%}",
        'SL率': f"{sells['SL']/max(total,1):.1%}",
        '超时率': f"{sells['TO']/max(total,1):.1%}",
        '买入': nbuy,
    }

# ══════════════════════════════════════
# 运行扫描
# ══════════════════════════════════════
SL_V20 = [-0.02, -0.03, -0.04, -0.05, -0.07, -0.10]
TP_V20 = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]
SL_V27 = [-0.015, -0.02, -0.03, -0.05, -0.07, -0.10]
TP_V27 = [0.03, 0.05, 0.08, 0.10, 0.15, 0.20]

print("\n" + "="*60)
print("🔬 v20c 止损止盈扫描")
print("="*60)
results_v20 = []
for sl in SL_V20:
    for tp in TP_V20:
        r = run_v20c(sl, tp, pick_v20)
        results_v20.append(r)
        print(f"  SL={r['SL']:>4} TP={r['TP']:>4} → 年化={r['年化']:>6} 夏普={r['夏普']:>5} 回撤={r['回撤']:>6} TP={r['TP率']} SL={r['SL率']} 超时={r['超时率']}")

print("\n" + "="*60)
print("🔬 v27 止损止盈扫描")
print("="*60)
results_v27 = []
for sl in SL_V27:
    for tp in TP_V27:
        r = run_v27(sl, tp, pick_v27)
        results_v27.append(r)
        print(f"  SL={r['SL']:>5} TP={r['TP']:>4} → 年化={r['年化']:>6} 夏普={r['夏普']:>5} 回撤={r['回撤']:>6} TP={r['TP率']} SL={r['SL率']} 超时={r['超时率']}")

# ── 汇总 ──
print("\n" + "="*60)
print("📊 v20c 结果（按夏普降序）")
print("="*60)
df20 = pd.DataFrame(results_v20)
df20['sv'] = df20['夏普'].astype(float)
print(df20.sort_values('sv', ascending=False)[['SL','TP','年化','夏普','回撤','TP率','SL率','超时率','买入']].to_string(index=False))

print("\n" + "="*60)
print("📊 v27 结果（按夏普降序）")
print("="*60)
df27 = pd.DataFrame(results_v27)
df27['sv'] = df27['夏普'].astype(float)
print(df27.sort_values('sv', ascending=False)[['SL','TP','年化','夏普','回撤','TP率','SL率','超时率','买入']].to_string(index=False))

ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
df20.to_csv(f"{REPORT_DIR}/v20c_sl_tp_scan_{ts}.csv", index=False)
df27.to_csv(f"{REPORT_DIR}/v27_sl_tp_scan_{ts}.csv", index=False)
print(f"\n✅ 结果已保存到 {REPORT_DIR}/")
