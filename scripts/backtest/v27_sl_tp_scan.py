#!/usr/bin/env python3
"""
v27 止损止盈参数扫描
基于 opt_fast 框架，每天调用 select_stocks_v27 选股
"""
import sys, os, numpy as np, pandas as pd, datetime
sys.path.insert(0, os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.environ.get('PROJECT_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'scripts', 'strategies'))

from core.db import load_panel_from_db
from scripts.strategies.v27_select import calc_factors, select_stocks_v27

REPORT_DIR = os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data" + "/backtest_results"
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

print("  计算 v27 因子...")
factors = calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
print(f"  因子就绪")

# ── 参数 ──
IC = 100000
MAX_H = 12
MAX_DB = 8
MAX_POS = 0.25
HOLD_MAX = 5
SL = -0.015  # v27 当前止损
SP = 0.03    # v27 当前止盈
CR = 0.0003
ST = 0.001
SR = 0.002

results = []

for sl in [-0.015, -0.02, -0.03, -0.05, -0.07, -0.10]:
    for sp in [0.03, 0.05, 0.08, 0.10, 0.15, 0.20]:
        cash = IC
        holdings = {}  # code: {sh, cost, days}
        nav_list = []
        sells = {'TP': 0, 'SL': 0, 'TO': 0}
        nbuy = 0
        pending = []
        params = {'STOP_LOSS': sl, 'TAKE_PROFIT': sp, 'HOLD_DAYS_MAX': HOLD_MAX, 'MOM_THRESHOLD': 0.02}

        for i, date in enumerate(dates):
            if i < 20:
                nav_list.append(IC)
                continue

            pd_ = close_panel.loc[date]
            od_ = open_panel.loc[date]

            # 买入（pending 是前一天选好的）
            if pending and cash > IC * 0.05 and len(holdings) < MAX_H:
                nb = min(len(pending), MAX_DB, MAX_H - len(holdings))
                ac = cash - IC * 0.05
                ps = ac / nb if nb else 0
                ps = min(ps, IC * MAX_POS)
                for code in pending[:nb]:
                    if code not in od_.index:
                        continue
                    bp = od_[code]
                    if np.isnan(bp) or bp <= 0:
                        continue
                    # 涨停过滤
                    if i > 0:
                        pc = close_panel.iloc[i-1].get(code)
                        if pc and not np.isnan(pc) and pc > 0 and bp >= pc * 1.09:
                            continue
                    adj = bp * (1 + CR + SR)
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
            to_remove = []
            for code, h in holdings.items():
                h['days'] += 1
                if code not in pd_.index:
                    continue
                cp = pd_[code]
                if np.isnan(cp) or cp <= 0:
                    continue
                pnl = (cp - h['cost']) / h['cost']
                reason = None
                if pnl <= sl:
                    reason = 'SL'
                elif pnl >= sp:
                    reason = 'TP'
                elif h['days'] >= HOLD_MAX:
                    reason = 'TO'
                if reason:
                    sv = h['sh'] * cp * (1 - CR - ST)
                    cash += sv
                    sells[reason] += 1
                    to_remove.append(code)
            for code in to_remove:
                holdings.pop(code, None)

            # 选股（传给下一天）
            try:
                cands = select_stocks_v27(factors, date, current_holdings=list(holdings.keys()), params=params)
                if cands and len(cands) > 0:
                    pending = [c[0] for c in cands if c[0] not in holdings][:MAX_DB]
                else:
                    pending = []
            except:
                pending = []

            # NAV
            nav = cash
            for code, h in holdings.items():
                if code in pd_.index:
                    p = pd_[code]
                    if not np.isnan(p) and p > 0:
                        nav += h['sh'] * p
            nav_list.append(nav)

        nav_arr = np.array(nav_list)
        daily_ret = np.diff(nav_arr) / nav_arr[:-1]
        tr = nav_arr[-1] / nav_arr[0] - 1
        n = len(daily_ret)
        ar = (1 + tr) ** (252 / max(n, 1)) - 1
        sh = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 1e-10 else 0
        cm = np.maximum.accumulate(nav_arr)
        mdd = ((nav_arr - cm) / cm).min()
        ts = max(sells['TP'] + sells['SL'] + sells['TO'], 1)

        r = {
            'SL': f"{sl:.1%}", 'TP': f"{sp:.0%}",
            '年化': f"{ar:.1%}", '夏普': f"{sh:.2f}", '回撤': f"{mdd:.1%}",
            'TP率': f"{sells['TP']/ts:.1%}",
            'SL率': f"{sells['SL']/ts:.1%}",
            '超时率': f"{sells['TO']/ts:.1%}",
            '买入': nbuy,
        }
        results.append(r)
        print(f"  SL={r['SL']:>5} TP={r['TP']:>4} → 年化={r['年化']:>6} 夏普={r['夏普']:>5} 回撤={r['回撤']:>6} TP={r['TP率']} SL={r['SL率']} 超时={r['超时率']}")

# ── 汇总 ──
print(f"\n{'='*60}")
print("📊 v27 结果（按夏普降序）")
print("="*60)
df = pd.DataFrame(results)
df['sv'] = df['夏普'].astype(float)
print(df.sort_values('sv', ascending=False)[['SL','TP','年化','夏普','回撤','TP率','SL率','超时率','买入']].to_string(index=False))

ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
df.to_csv(f"{REPORT_DIR}/v27_sl_tp_scan_{ts}.csv", index=False)
print(f"\n✅ 结果已保存")
