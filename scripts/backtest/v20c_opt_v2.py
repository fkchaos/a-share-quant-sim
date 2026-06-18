#!/usr/bin/env python3
"""
v20c 精简参数扫描 - 分步版（避免输出缓冲）
分批跑实验，每批 3-4 组，结果保存到文件
"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime

# unbuffered output
sys.stdout.reconfigure(line_buffering=True)

from core.db import load_panel_from_db
from scripts.strategies.v20_tail_pick import V20Config, calc_tail_pick_factors, select_stocks_tail_pick

REPORT_DIR = os.path.join(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "backtest_results"))
os.makedirs(REPORT_DIR, exist_ok=True)
OUT_FILE = os.path.join(REPORT_DIR, "v20c_opt_results.jsonl")

print("📥 加载数据...", flush=True)
panels, codes = load_panel_from_db(need_hl=True)
close_panel, volume_panel, amount_panel = panels[0], panels[1], panels[2]
high_panel, low_panel = panels[3], panels[4]
open_panel = panels[5] if len(panels) > 5 else panels[0]
factors = calc_tail_pick_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
print(f"  {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只", flush=True)

ORIG_HDM = V20Config.hold_days_max
ORIG_TP = V20Config.stop_profit
ORIG_VM = V20Config.vol_vs_avg_max
ORIG_RM = V20Config.range_vs_avg
ORIG_AMIN = V20Config.amount_vs_avg_min
ORIG_AMAX = V20Config.amount_vs_avg_max

def run_bt(hdm, tp, mode='full'):
    V20Config.hold_days_max = hdm
    V20Config.stop_profit = tp
    if mode == 'core':
        V20Config.vol_vs_avg_max = 10.0
        V20Config.range_vs_avg = 10.0
        V20Config.amount_vs_avg_min = 0.01
        V20Config.amount_vs_avg_max = 100.0

    cfg = V20Config()
    ic = 200000
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
    V20Config.vol_vs_avg_max = ORIG_VM
    V20Config.range_vs_avg = ORIG_RM
    V20Config.amount_vs_avg_min = ORIG_AMIN
    V20Config.amount_vs_avg_max = ORIG_AMAX

    ns = pd.Series(nav_list, index=dates[:len(nav_list)])
    tr = ns.iloc[-1] / ns.iloc[0] - 1
    nd = len(ns)
    ar = (1 + tr) ** (252 / max(nd, 1)) - 1
    dr = ns.pct_change().dropna()
    sh = dr.mean() / dr.std() * np.sqrt(252) if dr.std() > 1e-10 else 0
    mdd = ((ns - ns.cummax()) / ns.cummax()).min()
    ts_ = sum(sells.values())
    return {
        'ann_return': round(ar * 100, 2),
        'sharpe': round(sh, 3),
        'max_dd': round(mdd * 100, 2),
        'buys': nbuy, 'sells': ts_,
        'tp_rate': round(sells['TP'] / max(ts_, 1) * 100, 2),
        'sl_rate': round(sells['SL'] / max(ts_, 1) * 100, 2),
        'to_rate': round(sells['TO'] / max(ts_, 1) * 100, 2),
    }

experiments = [
    ('A1_hold2',          2, 0.25, 'full'),
    ('A2_hold3',          3, 0.25, 'full'),
    ('A3_hold5_baseline', 5, 0.25, 'full'),
    ('B1_tp10',           5, 0.10, 'full'),
    ('B2_tp15',           5, 0.15, 'full'),
    ('B3_tp20',           5, 0.20, 'full'),
    ('C1_hold2_tp15',     2, 0.15, 'full'),
    ('C2_hold3_tp15',     3, 0.15, 'full'),
    ('D1_core_hold5_tp25', 5, 0.25, 'core'),
    ('D2_core_hold3_tp15', 3, 0.15, 'core'),
]

all_results = []
t_start = time.time()

for idx, (name, hdm, tp, mode) in enumerate(experiments):
    t0 = time.time()
    r = run_bt(hdm, tp, mode)
    elapsed = time.time() - t0
    row = {'name': name, 'hold': hdm, 'tp': tp, 'mode': mode, 'time_s': round(elapsed, 1), **r}
    all_results.append(row)

    # 实时写入
    with open(OUT_FILE, 'a') as f:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')

    print(f"  [{idx+1}/{len(experiments)}] {name:25s} {elapsed:.1f}s → "
          f"年化={r['ann_return']:6.1f}% 夏普={r['sharpe']:5.2f} "
          f"回撤={r['max_dd']:6.1f}% TP={r['tp_rate']:5.1f}% TO={r['to_rate']:5.1f}%",
          flush=True)

# 汇总
print(f"\n总耗时: {time.time()-t_start:.1f}s", flush=True)
print(f"\n{'name':>25s} | {'hold':>4s} | {'tp':>5s} | {'mode':>5s} | {'年化':>7s} | {'夏普':>5s} | {'回撤':>7s} | {'TP%':>6s} | {'TO%':>6s}",
      flush=True)
print(f"{'-'*25}-+-{'-'*4}-+-{'-'*5}-+-{'-'*5}-+-{'-'*7}-+-{'-'*5}-+-{'-'*7}-+-{'-'*6}-+-{'-'*6}",
      flush=True)
for r in all_results:
    print(f"{r['name']:>25s} | {r['hold']:>4d} | {r['tp']*100:>4.0f}% | {r['mode']:>5s} | "
          f"{r['ann_return']:7.1f}% | {r['sharpe']:5.2f} | {r['max_dd']:7.1f}% | "
          f"{r['tp_rate']:6.1f}% | {r['to_rate']:6.1f}%", flush=True)

# best
best = max(all_results, key=lambda x: x['sharpe'])
print(f"\n🏆 最优夏普: {best['name']} (夏普={best['sharpe']:.2f}, 年化={best['ann_return']}%)", flush=True)

# 汇总 JSON
out2 = os.path.join(REPORT_DIR, "v20c_opt_summary.json")
with open(out2, 'w') as f:
    json.dump(all_results, f, indent=2, ensure_ascii=False)
print(f"\n✅ {out2}", flush=True)
