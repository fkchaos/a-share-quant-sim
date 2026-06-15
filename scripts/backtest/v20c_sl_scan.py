#!/usr/bin/env python3
"""v20c 止损阈值精细扫描 (hold=2, tp=25%)"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime

sys.path.insert(0, '/root/a-share-quant-sim')
sys.path.insert(0, '/root/a-share-quant-sim/scripts/strategies')

from core.db import load_panel_from_db
from scripts.strategies.v20_tail_pick import V20Config, calc_tail_pick_factors, select_stocks_tail_pick

REPORT_DIR = "/root/data/backtest_results"
os.makedirs(REPORT_DIR, exist_ok=True)

log_path = os.path.join(REPORT_DIR, "v20c_sl_scan.txt")
def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(log_path, 'a') as f:
        f.write(line + '\n')

log("📥 加载数据...")
panels, codes = load_panel_from_db(need_hl=True)
close_panel, volume_panel, amount_panel = panels[0], panels[1], panels[2]
high_panel, low_panel = panels[3], panels[4]
factors = calc_tail_pick_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
log(f"  {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只")

ORIG_HDM = V20Config.hold_days_max
ORIG_TP = V20Config.stop_profit

def run_bt(sl, tp=0.25, hdm=2):
    V20Config.stop_loss = sl
    V20Config.stop_profit = tp
    V20Config.hold_days_max = hdm
    cfg = V20Config()
    ic = cfg.initial_capital

    holdings = {}
    cash = ic
    trade_log = []
    nav_history = []
    pending = []

    dates = close_panel.index[60:]

    for di, date in enumerate(dates):
        cp = close_panel.loc[date] if date in close_panel.index else pd.Series()

        # 风控
        to_sell = []
        for code, h in list(holdings.items()):
            if code not in cp.index:
                continue
            p = cp[code]
            if pd.isna(p) or p <= 0:
                continue
            pnl = (p - h['cost_price']) / h['cost_price']
            if pnl <= sl:
                to_sell.append((code, 'SL', pnl))
            elif pnl >= tp:
                to_sell.append((code, 'TP', pnl))
            elif h['hold_days'] >= hdm:
                to_sell.append((code, 'TO', pnl))

        for code, reason, pnl in to_sell:
            if code in holdings and code in cp.index:
                p = cp[code]
                if not pd.isna(p) and p > 0:
                    h = holdings[code]
                    proceeds = h['shares'] * p
                    cash += proceeds
                    trade_log.append({'date': str(date), 'code': code, 'action': 'SELL',
                                      'reason': reason, 'pnl': round(pnl, 4), 'proceeds': proceeds})
                    del holdings[code]

        # 选股（每5天或空仓时）
        if di % 5 == 0 or (not holdings and not pending):
            try:
                cands = select_stocks_tail_pick(factors, date, close_panel, volume_panel, amount_panel, high_panel, low_panel, holdings)
                pending = cands[:cfg.max_daily_buy]
            except:
                pending = []

        # 买入
        if pending and cash > ic * 0.1 and len(holdings) < cfg.max_holdings:
            ac = cash - ic * 0.1
            nb = min(len(pending), cfg.max_daily_buy, cfg.max_holdings - len(holdings))
            ps = ac / nb if nb > 0 else 0
            ps = min(ps, ic * cfg.max_position)
            bought = []
            for code in pending:
                if code not in cp.index:
                    continue
                p = cp[code]
                if pd.isna(p) or p <= 0:
                    continue
                shares = int(ps / p / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * p
                if cost > ac:
                    continue
                holdings[code] = {'shares': shares, 'cost_price': p, 'buy_date': date, 'hold_days': 0}
                cash -= cost
                ac -= cost
                trade_log.append({'date': str(date), 'code': code, 'action': 'BUY', 'shares': shares, 'price': p})
                bought.append(code)
                if len(holdings) >= cfg.max_holdings:
                    break
            pending = [c for c in pending if c not in bought]

        for code in holdings:
            holdings[code]['hold_days'] += 1

        mv = sum(h['shares'] * cp.get(code, 0) for code, h in holdings.items()
                 if code in cp.index and not pd.isna(cp.get(code, 0)))
        nav = cash + mv
        nav_history.append({'date': str(date), 'nav': nav})

    nav_series = pd.Series([n['nav'] for n in nav_history])
    returns = nav_series.pct_change().dropna()
    if len(returns) < 10:
        return None

    ann_return = (nav_series.iloc[-1] / nav_series.iloc[0]) ** (252 / len(returns)) - 1
    sharpe = returns.mean() / (returns.std() + 1e-10) * np.sqrt(252)
    max_dd = ((nav_series.cummax() - nav_series) / nav_series.cummax()).max()

    sells = [t for t in trade_log if t['action'] == 'SELL']
    tp_count = len([t for t in sells if t['reason'] == 'TP'])
    sl_count = len([t for t in sells if t['reason'] == 'SL'])
    to_count = len([t for t in sells if t['reason'] == 'TO'])
    total_sells = max(len(sells), 1)

    return {
        'ann_return': ann_return, 'sharpe': sharpe, 'max_dd': max_dd,
        'tp_rate': tp_count / total_sells, 'sl_rate': sl_count / total_sells,
        'to_rate': to_count / total_sells, 'total_trades': len(trade_log),
        'final_nav': nav_series.iloc[-1],
    }

for k, v in [('hold_days_max', ORIG_HDM), ('stop_profit', ORIG_TP)]:
    setattr(V20Config, k, v)

# 扫描止损 (hold=2, tp=25%)
log("🔬 扫描止损阈值 (hold=2, tp=25%)...")
sl_values = [-0.03, -0.05, -0.08, -0.10, -0.12, -0.15, -0.20, -0.25, -0.30]
results = []
for i, sl in enumerate(sl_values):
    r = run_bt(sl)
    if r is None:
        continue
    results.append({'sl': sl, **r})
    log(f"  [{i+1}/{len(sl_values)}] SL={sl*100:5.0f}%: 年化={r['ann_return']*100:6.1f}% 夏普={r['sharpe']:5.2f} "
        f"回撤={r['max_dd']*100:6.1f}% TP={r['tp_rate']*100:5.1f}% SL={r['sl_rate']*100:5.1f}%")

results.sort(key=lambda x: x['sharpe'], reverse=True)
log(f"\n{'='*80}")
log("结果（按夏普降序）")
log(f"{'SL':>6s} | {'年化':>7s} | {'夏普':>5s} | {'回撤':>7s} | {'TP%':>6s} | {'SL%':>6s} | {'TO%':>6s}")
for r in results:
    log(f"{r['sl']*100:5.0f}% | {r['ann_return']*100:7.1f}% | {r['sharpe']:5.2f} | {r['max_dd']*100:7.1f}% | "
        f"{r['tp_rate']*100:6.1f}% | {r['sl_rate']*100:6.1f}% | {r['to_rate']*100:6.1f}%")

if results:
    best = results[0]
    log(f"\n🏆 最优: SL={best['sl']*100:.0f}% (夏普={best['sharpe']:.2f}, 年化={best['ann_return']*100:.1f}%)")

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out = os.path.join(REPORT_DIR, f"v20c_sl_scan_{ts}.json")
with open(out, "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False, default=str)
log(f"\n✅ {out}")
