#!/usr/bin/env python3
"""
v20c 流动性阈值回测对比（v20 专用引擎）
=========================================
用 v20c 自己的回测引擎，对比不同流动性阈值。
"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime

from core.db import load_panel_from_db
from scripts.strategies.v20_tail_pick import V20Config, calc_tail_pick_factors, select_stocks_tail_pick

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")

def run_v20_backtest_with_liquidity(close_panel, volume_panel, amount_panel,
                                     high_panel, low_panel, factors,
                                     min_liquidity, max_liquidity):
    """用 v20c 回测引擎跑，但修改流动性阈值"""
    cfg = V20Config()
    cfg.min_liquidity = min_liquidity / 1e4  # 转成万元
    cfg.max_liquidity = max_liquidity / 1e4

    initial_capital = cfg.initial_capital
    cash = initial_capital
    holdings = {}  # {code: {shares, cost, hold_days, buy_date}}
    nav_list = []
    trade_log = []
    dates = close_panel.index
    n = len(dates)
    pending_buy = []  # [(code, score)]

    for i, date in enumerate(dates):
        if i < 20:  # 预热
            nav_list.append(initial_capital)
            continue

        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else initial_capital)
            continue

        price_data = close_panel.loc[date]

        # 1. 执行待买入队列（T日选股，T+1日开盘买）
        if pending_buy and cash > initial_capital * 0.1 and len(holdings) < cfg.max_holdings:
            available_cash = cash - initial_capital * 0.1
            n_buy = min(len(pending_buy), cfg.max_daily_buy, cfg.max_holdings - len(holdings))
            per_stock = available_cash / n_buy if n_buy > 0 else 0
            per_stock = min(per_stock, initial_capital * cfg.max_position)

            for code, score in pending_buy[:n_buy]:
                if code not in price_data.index:
                    continue
                buy_price = price_data[code]
                if pd.isna(buy_price) or buy_price <= 0:
                    continue
                # 涨停检查
                if i > 0:
                    prev_close = close_panel.iloc[i-1].get(code, None) if code in close_panel.columns else None
                    if prev_close and not pd.isna(prev_close) and prev_close > 0:
                        limit_up = prev_close * 1.10
                        if buy_price >= limit_up * 0.99:
                            continue
                adj = buy_price * (1 + cfg.commission_rate + cfg.slippage_rate)
                shares = int(per_stock / adj / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * adj
                cash -= cost
                holdings[code] = {'shares': shares, 'cost': buy_price, 'hold_days': 0, 'buy_date': date}
                trade_log.append({'date': date, 'code': code, 'action': 'buy', 'shares': shares, 'price': buy_price})

        pending_buy = []

        # 2. 更新持有天数 + 止损/止盈/超时检查
        to_sell = []
        for code, h in holdings.items():
            h['hold_days'] += 1
            if code not in price_data.index:
                continue
            cp = price_data[code]
            if pd.isna(cp) or cp <= 0:
                continue
            pnl = (cp - h['cost']) / h['cost']

            # 止损
            if pnl <= cfg.stop_loss:
                to_sell.append((code, 'SL'))
                continue
            # 止盈
            if pnl >= cfg.stop_profit:
                to_sell.append((code, 'TP'))
                continue
            # 超时
            if h['hold_days'] >= cfg.hold_days_max:
                to_sell.append((code, 'TO'))
                continue

        for code, reason in to_sell:
            if code not in price_data.index:
                continue
            sp = price_data[code]
            if pd.isna(sp) or sp <= 0:
                continue
            h = holdings[code]
            proceeds = h['shares'] * sp * (1 - cfg.commission_rate - cfg.stamp_tax - cfg.slippage_rate)
            cash += proceeds
            trade_log.append({'date': date, 'code': code, 'action': 'sell', 'shares': h['shares'], 'price': sp, 'reason': reason})
            holdings.pop(code, None)

        # 3. 选股（T日尾盘选，T+1日开盘买）
        if date in factors['vol_ratio'].index:
            cands = select_stocks_tail_pick(
                factors, date, close_panel, volume_panel, amount_panel,
                high_panel, low_panel, current_holdings=holdings
            )
            pending_buy = [(c, 1.0) for c in cands[:cfg.max_daily_buy]]

        # 计算 NAV
        nav = cash
        for code, h in holdings.items():
            if code in price_data.index:
                cp = price_data[code]
                if not pd.isna(cp) and cp > 0:
                    nav += h['shares'] * cp
        nav_list.append(nav)

    nav_series = pd.Series(nav_list, index=dates[:len(nav_list)])
    return nav_series, trade_log

def calc_metrics(nav_series):
    if nav_series is None or len(nav_series) < 2:
        return {'ann_return': 0, 'sharpe': 0, 'max_dd': 0, 'sortino': 0, 'total_trades': 0}
    total_return = nav_series.iloc[-1] / nav_series.iloc[0] - 1
    n_days = len(nav_series)
    ann_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1
    daily_returns = nav_series.pct_change().dropna()
    if len(daily_returns) < 2 or daily_returns.std() < 1e-10:
        return {'ann_return': ann_return, 'sharpe': 0, 'max_dd': 0, 'sortino': 0, 'total_trades': 0}
    sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252)
    cummax = nav_series.cummax()
    drawdown = (nav_series - cummax) / cummax
    max_dd = drawdown.min()
    neg_returns = daily_returns[daily_returns < 0]
    sortino = daily_returns.mean() / neg_returns.std() * np.sqrt(252) if len(neg_returns) > 0 and neg_returns.std() > 1e-10 else 0
    return {'ann_return': ann_return, 'sharpe': sharpe, 'max_dd': max_dd, 'sortino': sortino, 'total_trades': 0}

# 加载数据
print("📥 加载数据...")
t0 = time.time()
panels, codes = load_panel_from_db(need_hl=True)
close_panel, volume_panel, amount_panel = panels[0], panels[1], panels[2]
high_panel, low_panel = panels[3], panels[4]
print(f"  {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只 ({time.time()-t0:.1f}s)")

# 计算因子
print("\n🔢 计算因子...")
t1 = time.time()
factors = calc_tail_pick_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
print(f"  {len(factors)} 个因子 ({time.time()-t1:.1f}s)")

# 不同流动性阈值
thresholds = [
    (300e4, 10000e4, "300万~1亿（当前）"),
    (100e4, 20000e4, "100万~2亿"),
    (300e4, 20000e4, "300万~2亿"),
    (100e4, 10000e4, "100万~1亿"),
    (500e4, 20000e4, "500万~2亿"),
    (100e4, 15000e4, "100万~1.5亿"),
]

results = []

for lo, hi, desc in thresholds:
    print(f"\n{'='*60}")
    print(f"回测: {desc}")
    print(f"{'='*60}")

    t2 = time.time()
    nav, trades = run_v20_backtest_with_liquidity(
        close_panel, volume_panel, amount_panel, high_panel, low_panel, factors,
        min_liquidity=lo, max_liquidity=hi
    )
    bt_time = time.time() - t2

    m = calc_metrics(nav)
    buy_trades = [t for t in trades if t['action'] == 'buy']

    print(f"  回测完成 ({bt_time:.1f}s)")
    print(f"  年化: {m['ann_return']:.1%}")
    print(f"  夏普: {m['sharpe']:.2f}")
    print(f"  回撤: {m['max_dd']:.1%}")
    print(f"  买入: {len(buy_trades)} 次")

    results.append({
        'desc': desc,
        'ann_return': m['ann_return'],
        'sharpe': m['sharpe'],
        'max_dd': m['max_dd'],
        'sortino': m['sortino'],
        'total_buys': len(buy_trades),
    })

# 汇总
print(f"\n{'='*80}")
print(f"汇总对比")
print(f"{'='*80}")
print(f"{'配置':20s} | {'年化':>8s} | {'夏普':>6s} | {'回撤':>8s} | {'买入':>6s}")
print(f"{'-'*20}-+-{'-'*8}-+-{'-'*6}-+-{'-'*8}-+-{'-'*6}")
for r in results:
    print(f"{r['desc']:20s} | {r['ann_return']:8.1%} | {r['sharpe']:6.2f} | {r['max_dd']:8.1%} | {r['total_buys']:6d}")

# 保存
os.makedirs(REPORT_DIR, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
out_file = os.path.join(REPORT_DIR, f"v20c_liquidity_scan_{ts}.json")
with open(out_file, "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print(f"\n✅ 结果已保存 → {out_file}")
