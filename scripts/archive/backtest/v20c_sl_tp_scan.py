#!/usr/bin/env python3
"""
v20c 止损止盈参数扫描（正确版：每天根据实际持仓选股）
"""
import sys, os, numpy as np, pandas as pd, datetime

from core.db import load_panel_from_db
from scripts.strategies.v20_tail_pick import V20Config, calc_tail_pick_factors, select_stocks_tail_pick

REPORT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data" + "/backtest_results")
os.makedirs(REPORT_DIR, exist_ok=True)

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

print("  计算 v20c 因子...")
factors = calc_tail_pick_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
print("  因子就绪")

SL_RANGE = [-0.02, -0.03, -0.04, -0.05, -0.07, -0.10]
TP_RANGE = [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]

results = []

for sl in SL_RANGE:
    for tp in TP_RANGE:
        V20Config.stop_loss = sl
        V20Config.stop_profit = tp
        V20Config.hold_days_max = 2
        V20Config.max_holdings = 12
        V20Config.max_daily_buy = 5
        V20Config.max_position = 0.25
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
                    if np.isnan(bp) or bp <= 0:
                        continue
                    if i > 0:
                        pc = close_panel.iloc[i-1].get(code)
                        if pc and not np.isnan(pc) and pc > 0 and bp >= pc * 1.09:
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
                    to_remove.append(code)
            for code in to_remove:
                holdings.pop(code, None)

            # 选股（传给下一天）
            try:
                cands = select_stocks_tail_pick(factors, date, close_panel, volume_panel, amount_panel,
                                                high_panel, low_panel, current_holdings=list(holdings.keys()))
                if cands and len(cands) > 0:
                    # 过滤已持有的
                    cands_new = [c for c in cands if c not in holdings][:cfg.max_daily_buy]
                    pending = cands_new
                else:
                    pending = []
            except:
                pending = []

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
            'SL': f"{sl:.0%}", 'TP': f"{tp:.0%}",
            '年化': f"{ar:.1%}", '夏普': f"{sh:.2f}", '回撤': f"{mdd:.1%}",
            'TP率': f"{sells['TP']/ts:.1%}",
            'SL率': f"{sells['SL']/ts:.1%}",
            '超时率': f"{sells['TO']/ts:.1%}",
            '买入': nbuy,
        }
        results.append(r)
        print(f"  SL={r['SL']:>4} TP={r['TP']:>4} → 年化={r['年化']:>6} 夏普={r['夏普']:>5} 回撤={r['回撤']:>6} TP={r['TP率']} SL={r['SL率']} 超时={r['超时率']}")

print(f"\n{'='*60}")
print("📊 v20c 结果（按夏普降序）")
print("="*60)
df = pd.DataFrame(results)
df['sv'] = df['夏普'].astype(float)
print(df.sort_values('sv', ascending=False)[['SL','TP','年化','夏普','回撤','TP率','SL率','超时率','买入']].to_string(index=False))

ts = datetime.datetime.now().strftime("%Y%m%d_%H%M")
df.to_csv(f"{REPORT_DIR}/v20c_sl_tp_scan_{ts}.csv", index=False)
print(f"\n✅ 结果已保存")
