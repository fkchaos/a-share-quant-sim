#!/usr/bin/env python3
"""
v20c 精简参数扫描
只跑关键实验，减少回测轮次
"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime

from core.db import load_panel_from_db
from scripts.strategies.v20_tail_pick import V20Config, calc_tail_pick_factors, select_stocks_tail_pick

REPORT_DIR = os.path.join(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "backtest_results"))

# ── 加载数据 ──
print("📥 加载数据...")
panels, codes = load_panel_from_db(need_hl=True)
close_panel, volume_panel, amount_panel = panels[0], panels[1], panels[2]
high_panel, low_panel = panels[3], panels[4]
open_panel = panels[5] if len(panels) > 5 else panels[0]
factors = calc_tail_pick_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
print(f"  {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只, {len(factors)} 因子")

ORIG_HDM = V20Config.hold_days_max
ORIG_TP = V20Config.stop_profit

def run_bt(hdm, tp, mode='full'):
    V20Config.hold_days_max = hdm
    V20Config.stop_profit = tp
    if mode == 'core':
        V20Config.vol_vs_avg_max = 10.0
        V20Config.range_vs_avg = 10.0
        V20Config.amount_vs_avg_min = 0.01
        V20Config.amount_vs_avg_max = 100.0

    cfg = V20Config()
    ic = cfg.initial_capital
    cash = ic
    holdings = {}
    nav_list = []
    sells = {'TP': 0, 'SL': 0, 'TO': 0}
    nbuy = 0
    dates = close_panel.index
    pending = []

    for i, date in enumerate(dates):
        if i < 20:
            nav_list.append(ic)
            continue

        pd_ = close_panel.loc[date]
        od_ = open_panel.loc[date]

        # buy
        if pending and cash > ic * 0.1 and len(holdings) < cfg.max_holdings:
            ac = cash - ic * 0.1
            nb = min(len(pending), cfg.max_daily_buy, cfg.max_holdings - len(holdings))
            ps = ac / nb if nb else 0
            ps = min(ps, ic * cfg.max_position)
            for code, _ in pending[:nb]:
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

        # sell
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

        # select
        cands = select_stocks_tail_pick(factors, date, close_panel, volume_panel, amount_panel,
                                        high_panel, low_panel, current_holdings=holdings)
        pending = [(c, 0.0) for c in cands[:cfg.max_daily_buy]]

        nav = cash
        for code, h in holdings.items():
            if code in pd_.index:
                p = pd_[code]
                if not pd.isna(p) and p > 0:
                    nav += h['sh'] * p
        nav_list.append(nav)

    V20Config.hold_days_max = ORIG_HDM
    V20Config.stop_profit = ORIG_TP
    if mode == 'core':
        V20Config.vol_vs_avg_max = 1.0
        V20Config.range_vs_avg = 1.0
        V20Config.amount_vs_avg_min = 0.5
        V20Config.amount_vs_avg_max = 5.0

    ns = pd.Series(nav_list, index=dates[:len(nav_list)])
    tr = ns.iloc[-1] / ns.iloc[0] - 1
    nd = len(ns)
    ar = (1 + tr) ** (252 / max(nd, 1)) - 1
    dr = ns.pct_change().dropna()
    sh = dr.mean() / dr.std() * np.sqrt(252) if dr.std() > 1e-10 else 0
    cm = ns.cummax()
    mdd = ((ns - cm) / cm).min()
    ts_ = sum(sells.values())
    return {
        'ann_return': ar, 'sharpe': sh, 'max_dd': mdd,
        'buys': nbuy, 'sells': ts_,
        'tp_rate': sells['TP'] / max(ts_, 1),
        'sl_rate': sells['SL'] / max(ts_, 1),
        'to_rate': sells['TO'] / max(ts_, 1),
    }

# 实验矩阵（精简版）
experiments = [
    # A: hold_days_max（当前TP=25%）
    ('A1_hold2', 2, 0.25, 'full'),
    ('A2_hold3', 3, 0.25, 'full'),
    ('A3_hold5', 5, 0.25, 'full'),
    # B: stop_profit（当前hold=5）
    ('B1_tp10', 5, 0.10, 'full'),
    ('B2_tp15', 5, 0.15, 'full'),
    ('C3_tp25', 5, 0.25, 'full'),
    # C: 因子简化
    ('D1_full_hold5_tp25', 5, 0.25, 'full'),
    ('D2_full_hold3_tp15', 3, 0.15, 'full'),
    ('D3_core_hold5_tp25', 5, 0.25, 'core'),
    ('D4_core_hold3_tp15', 3, 0.15, 'core'),
    # 极端测试
    ('E1_tp08_hold3', 3, 0.08, 'full'),
    ('E2_tp20_hold3', 3, 0.20, 'full'),
]

results = []
for name, hdm, tp, mode in experiments:
    r = run_bt(hdm, tp, mode)
    label = f"{name} (h={hdm},tp={tp*100:.0f}%,{mode})"
    results.append({'name': name, 'hold': hdm, 'tp': tp, 'mode': mode, **r})
    print(f"  {label:40s}: 年化={r['ann_return']*100:6.1f}% 夏普={r['sharpe']:5.2f} "
          f"回撤={r['max_dd']*100:6.1f}% TP={r['tp_rate']*100:5.1f}% TO={r['to_rate']*100:5.1f}%")

# 分 3 组打印
print(f"\n{'='*90}")
print("结果汇总 (12 组 × 60s = ~12 分钟)")
print(f"{'='*90}")

print(f"\n{'name':>20s} | {'hold':>4s} | {'tp':>5s} | {'mode':>5s} | {'年化':>7s} | {'夏普':>5s} | {'回撤':>7s} | {'TP%':>6s} | {'TO%':>6s}")
print(f"{'-'*20}-+-{'-'*4}-+-{'-'*5}-+-{'-'*5}-+-{'-'*7}-+-{'-'*5}-+-{'-'*7}-+-{'-'*6}-+-{'-'*6}")
for r in results:
    print(f"{r['name']:>20s} | {r['hold']:>4d} | {r['tp']*100:>4.0f}% | {r['mode']:>5s} | "
          f"{r['ann_return']*100:7.1f}% | {r['sharpe']:5.2f} | {r['max_dd']*100:7.1f}% | "
          f"{r['tp_rate']*100:6.1f}% | {r['to_rate']*100:6.1f}%")

# 最优
best = max(results, key=lambda x: x['sharpe'])
print(f"\n🏆 最优夏普: {best['name']} (夏普={best['sharpe']:.2f}, 年化={best['ann_return']*100:.1f}%)")
best2 = max(results, key=lambda x: x['ann_return'])
print(f"🏆 最高年化: {best2['name']} (年化={best2['ann_return']*100:.1f}%, 夏普={best2['sharpe']:.2f})")

# 保存
os.makedirs(REPORT_DIR, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out = os.path.join(REPORT_DIR, f"v20c_opt_fast_{ts}.json")
with open(out, "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False, default=str)
print(f"\n✅ {out}")
