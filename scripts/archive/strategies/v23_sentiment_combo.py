#!/usr/bin/env python3
"""
v23_sentiment_combo — 情绪周期 + 动量因子组合策略
===============================================

架构：
- 选股层：v22 的动量+辅助因子选股（不变）
- 仓位层：情绪周期信号作为仓位乘数
  - 情绪热（heat_pct > 0.8）→ 仓位乘数 1.0（满仓）
  - 情绪正常（0.2 < heat_pct < 0.8）→ 仓位乘数 0.5（半仓）
  - 情绪冷（heat_pct < 0.2）→ 仓位乘数 0.1（轻仓/空仓）
  - 情绪回升（heat_ma5 > heat_ma20）→ 仓位乘数 +0.2
  - 情绪退潮（heat_ma5 < heat_ma20）→ 仓位乘数 -0.2

核心假设：
- 动量因子在情绪热时更有效（追涨动能强）
- 动量因子在情绪冷时容易止损（无承接盘）
- 情绪择时不选股，只控制仓位
"""

import sys, os
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

DATA_DIR = os.environ.get('BACKTEST_DATA_DIR', '/root/data')
REPORT_DIR = os.path.join(DATA_DIR, 'backtest_results')
os.makedirs(REPORT_DIR, exist_ok=True)


def build_limit_up(close_panel, high_panel, pct=0.095):
    """向量化涨停判断"""
    prev_close = close_panel.shift(1)
    limit_up = (high_panel >= prev_close * (1 + pct)) & (high_panel == close_panel)
    return limit_up.fillna(False)


def build_streak(limit_up):
    """向量化连板数"""
    streak = pd.DataFrame(0, index=limit_up.index, columns=limit_up.columns, dtype=int)
    for i in range(1, len(limit_up)):
        mask = limit_up.iloc[i]
        streak.iloc[i] = ((streak.iloc[i - 1] + 1) * mask.astype(int)).astype(int)
    return streak


def build_sentiment_score(limit_up, streak, close_panel, lookback=20):
    """情绪热度得分"""
    max_streak = streak.max(axis=1).astype(float)
    lu_count = limit_up.sum(axis=1).astype(float)

    # 次日溢价
    next_ret = close_panel.pct_change().shift(-1)
    premium = pd.Series(0.0, index=close_panel.index)
    for i in range(len(close_panel) - 1):
        stocks = limit_up.iloc[i]
        cnt = stocks.sum()
        if cnt > 0:
            avg = next_ret.iloc[i][stocks].mean()
            if not pd.isna(avg):
                premium.iloc[i] = float(avg)

    heat = (
        lu_count.rolling(lookback).mean() / lookback +
        max_streak / 10 * 3 +
        premium.rolling(lookback).mean() * 10
    )
    return heat


def get_position_multiplier(heat, lookback=20):
    """将情绪热度转为仓位乘数"""
    heat_pct = heat.rolling(lookback * 3).rank(pct=True)
    heat_ma5 = heat.rolling(5).mean()
    heat_ma20 = heat.rolling(20).mean()

    mult = pd.Series(0.5, index=heat.index)  # 默认半仓
    mult[heat_pct > 0.8] = 1.0   # 热
    mult[heat_pct < 0.2] = 0.1   # 冷
    mult[(heat_pct > 0.3) & (heat_pct <= 0.8) & (heat_ma5 > heat_ma20)] = 0.7  # 回升
    mult[(heat_pct < 0.7) & (heat_pct >= 0.2) & (heat_ma5 < heat_ma20)] = 0.3  # 退潮
    return mult


def run_v23(initial_capital=100000, start='2022-01-01', end='2026-05-31',
            hold_days_max=5, stop_loss=-0.015, stop_profit=0.03,
            mom_threshold=0.02, max_holdings=8, max_daily_buy=6,
            max_position=0.20, commission_rate=0.0003, stamp_tax=0.001,
            slippage_rate=0.002, verbose=True):
    """
    v23 情绪+动量组合策略回测
    """
    from core.db import load_panel_from_db

    start_time = time.time()

    tpl, _ = load_panel_from_db(start, end, need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]

    if verbose:
        print("面板: %d 天 x %d 只 | %s ~ %s" % (
            close_panel.shape[0], close_panel.shape[1],
            close_panel.index[0].strftime('%Y-%m-%d'),
            close_panel.index[-1].strftime('%Y-%m-%d')))

    eps = 1e-10

    # 因子
    mom_5 = close_panel.pct_change(5)
    prev_close = close_panel.shift(1)
    gap_ratio = (open_panel - prev_close) / (prev_close + eps)
    avg_amount = amount_panel.rolling(20).mean()
    illiq = 1.0 / (avg_amount / 1e4 + eps)
    ma20 = close_panel.rolling(20).mean()
    std20 = close_panel.rolling(20).std()
    boll_w = (4 * std20) / (ma20 + eps)

    # 情绪指标
    limit_up = build_limit_up(close_panel, high_panel)
    streak = build_streak(limit_up)
    heat = build_sentiment_score(limit_up, streak, close_panel)
    pos_mult = get_position_multiplier(heat)

    cash = initial_capital
    holdings = {}
    trade_log = []
    nav_list = []
    mult_list = []

    for i, date in enumerate(close_panel.index):
        if i < 30:
            nav_list.append(cash)
            mult_list.append(0.5)
            continue

        pd_ = close_panel.loc[date]
        od = open_panel.loc[date]

        for c in holdings:
            holdings[c]['hold_days'] = holdings[c].get('hold_days', 0) + 1

        # 卖出
        to_sell = []
        for c, h in holdings.items():
            if c not in pd_.index: continue
            cp = pd_[c]
            if pd.isna(cp) or cp <= 0: continue
            pnl = (cp - h['cost']) / h['cost']
            if pnl <= stop_loss: to_sell.append(c); continue
            if pnl >= stop_profit: to_sell.append(c); continue
            hd = h.get('hold_days', 0)
            if hd >= hold_days_max: to_sell.append(c)

        sold = set()
        for c in to_sell:
            if c not in pd_.index: continue
            sp = pd_[c]
            if pd.isna(sp) or sp <= 0: continue
            h = holdings[c]
            sv = h['shares'] * sp * (1 - commission_rate - stamp_tax - slippage_rate)
            cash += sv
            trade_log.append({
                'date': date, 'code': c, 'action': 'SELL',
                'pnl': (sp - h['cost']) / h['cost'],
                'shares': h['shares']
            })
            sold.add(c)
        for c in sold: holdings.pop(c, None)

        # 选股
        if date not in mom_5.index:
            nav_list.append(cash); mult_list.append(0.5); continue

        m5 = mom_5.loc[date].dropna()
        scores = {}
        for code in m5.index:
            score = 0.0
            m = m5[code]
            if m > mom_threshold:
                score += m * 100
                if date in gap_ratio.index and code in gap_ratio.columns:
                    gr = gap_ratio.loc[date, code]
                    if not pd.isna(gr) and gr > 0.02: score += 0.5
                if date in illiq.index and code in illiq.columns:
                    il = illiq.loc[date, code]
                    if not pd.isna(il) and il > 0: score += 0.8
                if date in boll_w.index and code in boll_w.columns:
                    bw = boll_w.loc[date, code]
                    if not pd.isna(bw) and bw > 1.2: score += 0.3
            if score > 0: scores[code] = score

        if holdings:
            scores = {c: s for c, s in scores.items() if c not in holdings}

        cands = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)[:max_holdings]

        # 仓位乘数
        mult = float(pos_mult.get(date, 0.5))
        mult_list.append(mult)

        if cands and cash > initial_capital * 0.1 and len(holdings) < max_holdings:
            avail = cash - initial_capital * 0.1
            avail *= mult  # 情绪仓位乘数
            nb = min(len(cands), max_daily_buy, max_holdings - len(holdings))
            if nb > 0:
                per = min(avail / nb, initial_capital * max_position * mult)
                for c in cands[:max_daily_buy]:
                    if len(holdings) >= max_holdings or nb <= 0: break
                    bp2 = od[c] if c in od.index else pd_[c]
                    if pd.isna(bp2) or bp2 <= 0: continue
                    adj = bp2 * (1 + commission_rate + slippage_rate)
                    sh = int(per / adj / 100) * 100
                    if sh <= 0: continue
                    cost = sh * adj
                    if cost > cash: continue
                    cash -= cost
                    holdings[c] = {'shares': sh, 'cost': bp2, 'hold_days': 0}
                    trade_log.append({
                        'date': date, 'code': c, 'action': 'BUY',
                        'shares': sh, 'cost': bp2
                    })
                    nb -= 1

        nav = cash
        for c, h in holdings.items():
            if c in pd_.index:
                cp = pd_[c]
                if not pd.isna(cp) and cp > 0: nav += h['shares'] * cp
        nav_list.append(nav)

    nav_s = pd.Series(nav_list)
    daily_ret = nav_s.pct_change().dropna()
    total_ret = nav_s.iloc[-1] / nav_s.iloc[0] - 1
    annual = (1 + total_ret) ** (365 / max(len(nav_list) - 30, 1)) - 1
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    max_dd = ((nav_s.cummax() - nav_s) / nav_s.cummax()).max()
    n_trades = len(trade_log)

    buy_trades = [t for t in trade_log if t['action'] == 'SELL']
    win_rate = np.mean([1 for t in buy_trades if t.get('pnl', 0) > 0]) if buy_trades else 0

    elapsed = time.time() - start_time

    if verbose:
        print("\n" + "=" * 60)
        print("v23 情绪+动量组合策略回测结果")
        print("=" * 60)
        print("  时间段:    %s ~ %s" % (
            close_panel.index[0].strftime('%Y-%m-%d'),
            close_panel.index[-1].strftime('%Y-%m-%d')))
        print("  初始资金:  %d" % initial_capital)
        print("  最终资金:  %.0f" % nav_s.iloc[-1])
        print("  总收益率:  %.2f%%" % (total_ret * 100))
        print("  年化收益:  %.2f%%" % (annual * 100))
        print("  夏普比率:  %.3f" % sharpe)
        print("  最大回撤:  %.2f%%" % (max_dd * 100))
        print("  交易次数:  %d" % n_trades)
        print("  胜率:      %.1f%%" % (win_rate * 100))
        print("  耗时:      %.1fs" % elapsed)
        print("=" * 60)

    return {
        'nav': nav_s, 'mult': pd.Series(mult_list, index=nav_s.index),
        'trade_log': trade_log,
        'total_ret': total_ret, 'annual': annual,
        'sharpe': sharpe, 'max_dd': max_dd,
        'n_trades': n_trades, 'win_rate': win_rate,
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="v23 情绪+动量组合策略")
    parser.add_argument("--start", type=str, default="2022-01-01")
    parser.add_argument("--end", type=str, default="2026-05-31")
    parser.add_argument("--capital", type=int, default=100000)
    parser.add_argument("--hold", type=int, default=5)
    parser.add_argument("--sl", type=float, default=-0.015)
    parser.add_argument("--tp", type=float, default=0.03)
    parser.add_argument("--mom", type=float, default=0.02)
    args = parser.parse_args()

    result = run_v23(
        initial_capital=args.capital,
        start=args.start, end=args.end,
        hold_days_max=args.hold,
        stop_loss=args.sl, stop_profit=args.tp,
        mom_threshold=args.mom,
    )
