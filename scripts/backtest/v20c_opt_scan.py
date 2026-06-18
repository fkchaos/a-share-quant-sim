#!/usr/bin/env python3
"""
v20c 参数优化扫描
=================
单因素实验 + 组合验证

实验设计：
A. hold_days_max: [3, 4, 5]
B. stop_profit: [10%, 15%, 20%, 25%]
C. 因子简化: [全因子, 仅price_vs_ma5 + recent_limit_up]
D. 最优组合验证
"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime
from itertools import product

from core.db import load_panel_from_db
from scripts.strategies.v20_tail_pick import V20Config, calc_tail_pick_factors, select_stocks_tail_pick

REPORT_DIR = os.path.join(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "backtest_results"))

# ── 加载数据 ──
print("📥 加载数据...")
panels, codes = load_panel_from_db(need_hl=True)
close_panel, volume_panel, amount_panel = panels[0], panels[1], panels[2]
high_panel, low_panel = panels[3], panels[4]
open_panel = panels[5] if len(panels) > 5 else panels[0]
print(f"  {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只")

# 计算全部因子
print("🔢 计算因子...")
factors = calc_tail_pick_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)

# 保存原始配置
ORIG = {
    'min_liquidity': V20Config.min_liquidity,
    'max_liquidity': V20Config.max_liquidity,
    'stop_loss': V20Config.stop_loss,
    'stop_profit': V20Config.stop_profit,
    'hold_days_max': V20Config.hold_days_max,
    'hold_days_min': V20Config.hold_days_min,
    'max_daily_buy': V20Config.max_daily_buy,
    'max_holdings': V20Config.max_holdings,
    'max_position': V20Config.max_position,
    'amount_vs_avg_max': V20Config.amount_vs_avg_max,
}

def run_backtest(hold_days_max, stop_profit, factor_mode='full'):
    """回测引擎，支持因子模式切换"""
    # 设置参数
    V20Config.min_liquidity = ORIG['min_liquidity']
    V20Config.max_liquidity = ORIG['max_liquidity']
    V20Config.stop_loss = ORIG['stop_loss']
    V20Config.stop_profit = stop_profit
    V20Config.hold_days_max = hold_days_max
    V20Config.hold_days_min = ORIG['hold_days_min']
    V20Config.max_daily_buy = ORIG['max_daily_buy']
    V20Config.max_holdings = ORIG['max_holdings']
    V20Config.max_position = ORIG['max_position']
    V20Config.amount_vs_avg_max = ORIG['amount_vs_avg_max']

    # 因子简化模式：调整权重让只有核心因子生效
    if factor_mode == 'core_only':
        # 把非核心因子的阈值设得很宽松，让它们都得高分
        vol_vs_avg_max_save = V20Config.vol_vs_avg_max
        range_vs_avg_save = V20Config.range_vs_avg
        amount_vs_avg_min_save = V20Config.amount_vs_avg_min
        amount_vs_avg_max_save = V20Config.amount_vs_avg_max
        # 放宽到几乎不贡献分数
        V20Config.vol_vs_avg_max = 10.0
        V20Config.range_vs_avg = 10.0
        V20Config.amount_vs_avg_min = 0.01
        V20Config.amount_vs_avg_max = 100.0

    cfg = V20Config()
    initial_capital = cfg.initial_capital
    cash = initial_capital
    holdings = {}
    nav_list = []
    trade_log = []
    dates = close_panel.index
    pending_buy = []

    for i, date in enumerate(dates):
        if i < 20:
            nav_list.append(initial_capital)
            continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else initial_capital)
            continue

        price_data = close_panel.loc[date]
        open_data = open_panel.loc[date]

        # 买入
        if pending_buy and cash > initial_capital * 0.1 and len(holdings) < cfg.max_holdings:
            available_cash = cash - initial_capital * 0.1
            n_buy = min(len(pending_buy), cfg.max_daily_buy, cfg.max_holdings - len(holdings))
            per_stock = available_cash / n_buy if n_buy > 0 else 0
            per_stock = min(per_stock, initial_capital * cfg.max_position)

            for code, score in pending_buy[:n_buy]:
                if code not in open_data.index:
                    continue
                buy_price = open_data[code]
                if pd.isna(buy_price) or buy_price <= 0:
                    continue
                if i > 0:
                    prev_close = close_panel.iloc[i-1].get(code, None) if code in close_panel.columns else None
                    if prev_close and not pd.isna(prev_close) and prev_close > 0:
                        if buy_price >= prev_close * 1.10 * 0.99:
                            continue
                adj = buy_price * (1 + cfg.commission_rate + cfg.slippage_rate)
                shares = int(per_stock / adj / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * adj
                if cost > cash:
                    continue
                cash -= cost
                holdings[code] = {'shares': shares, 'cost': buy_price, 'hold_days': 0}
                trade_log.append({'date': date, 'code': code, 'action': 'buy', 'price': buy_price, 'shares': shares, 'score': score})

        pending_buy = []

        # 卖出
        to_sell = []
        for code, h in list(holdings.items()):
            h['hold_days'] += 1
            if code not in price_data.index:
                continue
            cp = price_data[code]
            if pd.isna(cp) or cp <= 0:
                continue
            pnl = (cp - h['cost']) / h['cost']
            if pnl <= cfg.stop_loss:
                to_sell.append((code, 'SL', pnl))
                continue
            if pnl >= cfg.stop_profit:
                to_sell.append((code, 'TP', pnl))
                continue
            if h['hold_days'] >= cfg.hold_days_max:
                to_sell.append((code, 'TO', pnl))
                continue

        for code, reason, pnl in to_sell:
            if code not in price_data.index:
                continue
            sp = price_data[code]
            if pd.isna(sp) or sp <= 0:
                continue
            h = holdings[code]
            proceeds = h['shares'] * sp * (1 - cfg.commission_rate - cfg.stamp_tax - cfg.slippage_rate)
            cash += proceeds
            trade_log.append({'date': date, 'code': code, 'action': 'sell', 'reason': reason, 'pnl_pct': pnl * 100})
            holdings.pop(code, None)

        # 选股
        if date in factors['vol_ratio'].index:
            cands = select_stocks_tail_pick(factors, date, close_panel, volume_panel, amount_panel,
                                            high_panel, low_panel, current_holdings=holdings)
            pending_buy = [(c, 0.0) for c in cands[:cfg.max_daily_buy]]

        nav = cash
        for code, h in holdings.items():
            if code in price_data.index:
                cp = price_data[code]
                if not pd.isna(cp) and cp > 0:
                    nav += h['shares'] * cp
        nav_list.append(nav)

    # 恢复因子阈值
    if factor_mode == 'core_only':
        V20Config.vol_vs_avg_max = vol_vs_avg_max_save
        V20Config.range_vs_avg = range_vs_avg_save
        V20Config.amount_vs_avg_min = amount_vs_avg_min_save
        V20Config.amount_vs_avg_max = amount_vs_avg_max_save

    nav_series = pd.Series(nav_list, index=dates[:len(nav_list)])

    # 指标
    total_return = nav_series.iloc[-1] / nav_series.iloc[0] - 1
    n_days = len(nav_series)
    ann_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1
    daily_returns = nav_series.pct_change().dropna()
    sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252) if daily_returns.std() > 1e-10 else 0
    cummax = nav_series.cummax()
    max_dd = ((nav_series - cummax) / cummax).min()

    sells = [t for t in trade_log if t['action'] == 'sell']
    tp = len([t for t in sells if t.get('reason') == 'TP'])
    sl = len([t for t in sells if t.get('reason') == 'SL'])
    to = len([t for t in sells if t.get('reason') == 'TO'])

    # 恢复原始配置
    for k, v in ORIG.items():
        setattr(V20Config, k, v)

    return {
        'ann_return': ann_return, 'sharpe': sharpe, 'max_dd': max_dd,
        'total_buys': len([t for t in trade_log if t['action'] == 'buy']),
        'total_sells': len(sells), 'tp': tp, 'sl': sl, 'to': to,
        'tp_rate': tp / max(len(sells), 1),
        'sl_rate': sl / max(len(sells), 1),
        'to_rate': to / max(len(sells), 1),
    }

# ── 实验 A：hold_days_max ──
print("\n" + "="*80)
print("实验 A：持有周期 (hold_days_max)")
print("="*80)

exp_a = []
for hdm in [2, 3, 4, 5]:
    r = run_backtest(hold_days_max=hdm, stop_profit=V20Config.stop_profit)
    exp_a.append({'hold_days_max': hdm, **r})
    print(f"  hold_days_max={hdm}: 年化={r['ann_return']*100:.1f}%, 夏普={r['sharpe']:.2f}, "
          f"回撤={r['max_dd']*100:.1f}%, TP={r['tp_rate']*100:.1f}%, TO={r['to_rate']*100:.1f}%")

# ── 实验 B：stop_profit ──
print("\n" + "="*80)
print("实验 B：止盈阈值 (stop_profit)")
print("="*80)

exp_b = []
for sp in [0.10, 0.15, 0.20, 0.25]:
    r = run_backtest(hold_days_max=V20Config.hold_days_max, stop_profit=sp)
    exp_b.append({'stop_profit': sp, **r})
    print(f"  stop_profit={sp*100:.0f}%: 年化={r['ann_return']*100:.1f}%, 夏普={r['sharpe']:.2f}, "
          f"回撤={r['max_dd']*100:.1f}%, TP={r['tp_rate']*100:.1f}%, TO={r['to_rate']*100:.1f}%")

# ── 实验 C：因子简化 ──
print("\n" + "="*80)
print("实验 C：因子简化")
print("="*80)

exp_c = []
for mode in ['full', 'core_only']:
    r = run_backtest(hold_days_max=V20Config.hold_days_max, stop_profit=V20Config.stop_profit, factor_mode=mode)
    mode_label = '全因子(vol+range+amount+pm+lu)' if mode == 'full' else '仅核心因子(pm+lu)'
    exp_c.append({'factor_mode': mode, 'mode_label': mode_label, **r})
    print(f"  {mode_label}: 年化={r['ann_return']*100:.1f}%, 夏普={r['sharpe']:.2f}, "
          f"回撤={r['max_dd']*100:.1f}%")

# ── 实验 D：最优组合 ──
print("\n" + "="*80)
print("实验 D：组合验证")
print("="*80)

# 根据 A/B/C 结果选最优组合
best_a = max(exp_a, key=lambda x: x['sharpe'])
best_b = max(exp_b, key=lambda x: x['sharpe'])
best_c = max(exp_c, key=lambda x: x['sharpe'])

combos = [
    ('baseline', V20Config.hold_days_max, V20Config.stop_profit, 'full'),
    ('opt_hold', best_a['hold_days_max'], V20Config.stop_profit, 'full'),
    ('opt_profit', V20Config.hold_days_max, best_b['stop_profit'], 'full'),
    ('opt_both', best_a['hold_days_max'], best_b['stop_profit'], 'full'),
    ('opt_core_only', best_a['hold_days_max'], best_b['stop_profit'], 'core_only'),
]

exp_d = []
for name, hdm, sp, fm in combos:
    r = run_backtest(hold_days_max=hdm, stop_profit=sp, factor_mode=fm)
    exp_d.append({'name': name, 'hold_days_max': hdm, 'stop_profit': sp, 'factor_mode': fm, **r})
    print(f"  {name:20s} (hold={hdm}, tp={sp*100:.0f}%, {fm:10s}): "
          f"年化={r['ann_return']*100:.1f}%, 夏普={r['sharpe']:.2f}, "
          f"回撤={r['max_dd']*100:.1f}%, TP={r['tp_rate']*100:.1f}%, TO={r['to_rate']*100:.1f}%")

# ── 汇总表 ──
print("\n" + "="*100)
print("汇总对比")
print("="*100)

all_results = []
for r in exp_a:
    all_results.append({'config': f"hold={r['hold_days_max']}", **r})
for r in exp_b:
    all_results.append({'config': f"tp={r['stop_profit']*100:.0f}%", **r})
for r in exp_c:
    all_results.append({'config': f"factor={r['mode_label'][:10]}", **r})
for r in exp_d:
    all_results.append({'config': r['name'], **r})

print(f"{'配置':>20s} | {'年化':>8s} | {'夏普':>6s} | {'回撤':>8s} | {'TP占比':>7s} | {'SL占比':>7s} | {'TO占比':>7s}")
print(f"{'-'*20}-+-{'-'*8}-+-{'-'*6}-+-{'-'*8}-+-{'-'*7}-+-{'-'*7}-+-{'-'*7}")
for r in all_results:
    print(f"{r['config']:>20s} | {r['ann_return']*100:8.1f}% | {r['sharpe']:6.2f} | "
          f"{r['max_dd']*100:8.1f}% | {r['tp_rate']*100:7.1f}% | "
          f"{r['sl_rate']*100:7.1f}% | {r['to_rate']*100:7.1f}%")

# 保存
os.makedirs(REPORT_DIR, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out = os.path.join(REPORT_DIR, f"v20c_opt_scan_{ts}.json")
with open(out, "w") as f:
    json.dump({'exp_a': exp_a, 'exp_b': exp_b, 'exp_c': exp_c, 'exp_d': exp_d}, f,
              indent=2, ensure_ascii=False, default=str)
print(f"\n✅ 结果保存 → {out}")
