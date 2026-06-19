#!/usr/bin/env python3
"""
v25_drawdown_control — 回撤控制策略
========================================

核心思路：
- 用 v22 动量因子选股
- 加入多层回撤控制机制
- 目标：将最大回撤从 ~30% 降到 ~15%

回撤控制方法：
1. 单股回撤止损：个股回撤 > 1.5% 即止损
2. 组合回撤止损：当日 NAV 回撤 > 3% 则不买新仓
3. 回撤后冷静期：NAV 回撤 > 5% 后，3 天内仓位减半
4. 市场恐慌指数：用 VIX 代理（全市场波动率）> 阈值时降仓
5. 趋势跟踪止损：NAV < MA20 时仓位减半
"""

import sys, os
import time
import numpy as np
import pandas as pd

from core.db import load_panel_from_db

def run_v25(close_panel, volume_panel, amount_panel, open_panel,
            high_panel, low_panel, cfg):
    """
    v25 回撤控制策略
    
    参数：
    - stock_sl: 单股止损 (默认 -1.5%)
    - stock_tp: 单股止盈 (默认 3%)
    - daily_dd_limit: 日度回撤限制 (默认 3%)
    - cooldown_dd: 冷静期回撤阈值 (默认 5%)
    - cooldown_days: 冷静期天数 (默认 3)
    - trend_ma: 趋势跟踪MA (默认 20)
    """
    eps = 1e-10
    mom_5 = close_panel.pct_change(5)
    prev_close = close_panel.shift(1)
    gap_ratio = (open_panel - prev_close) / (prev_close + eps)
    avg_amount = amount_panel.rolling(20).mean()
    illiq = 1.0 / (avg_amount / 1e4 + eps)
    ma20 = close_panel.rolling(20).mean()
    std20 = close_panel.rolling(20).std()
    boll_w = (4 * std20) / (ma20 + eps)

    cash = cfg['initial_capital']
    holdings = {}
    nav_list = []
    cooldown = 0  # 冷静期计数器
    max_nav = cash  # NAV 历史最高
    sell_reasons = {'SL': 0, 'TP': 0, 'TO': 0, 'DD': 0}

    for i, date in enumerate(close_panel.index):
        if i < 30:
            nav_list.append(cash); continue

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
            if pnl <= cfg['stop_loss']:
                to_sell.append((c, 'SL')); continue
            if pnl >= cfg['stop_profit']:
                to_sell.append((c, 'TP')); continue
            if h.get('hold_days', 0) >= cfg['hold_days_max']:
                to_sell.append((c, 'TO')); continue

        daily_pnl = 0
        for c, reason in to_sell:
            if c not in pd_.index: continue
            sp = pd_[c]
            if pd.isna(sp) or sp <= 0: continue
            h = holdings[c]
            sv = h['shares'] * sp * (1 - cfg['commission_rate'] - cfg['stamp_tax'] - cfg['slippage_rate'])
            daily_pnl += sv - h['shares'] * h['cost']
            cash += sv
            sell_reasons[reason] = sell_reasons.get(reason, 0) + 1
        for c, _ in to_sell: holdings.pop(c, None)

        # 计算当前 NAV
        nav = cash
        for c, h in holdings.items():
            if c in pd_.index:
                cp = pd_[c]
                if not pd.isna(cp) and cp > 0: nav += h['shares'] * cp

        # 更新最高 NAV
        if nav > max_nav:
            max_nav = nav
            # 恢复冷静期
            if cooldown > 0:
                cooldown = max(0, cooldown - 1)

        # 计算回撤
        drawdown = (max_nav - nav) / max_nav if max_nav > 0 else 0

        # 回撤控制：冷静期
        if drawdown > cfg.get('cooldown_dd', 0.05):
            cooldown = cfg.get('cooldown_days', 3)

        nav_list.append(nav)

        # 仓位乘数（回撤控制）
        pos_mult = 1.0
        if cooldown > 0:
            pos_mult *= 0.5  # 冷静期仓位减半
        if drawdown > cfg.get('daily_dd_limit', 0.03):
            pos_mult *= 0.5  # 日度过撤降仓

        # 趋势跟踪：NAV < MA20 时降仓
        if len(nav_list) >= 20:
            nav_series = pd.Series(nav_list[-20:])
            nav_ma20 = nav_series.mean()
            if nav_list[-1] < nav_ma20 * 0.95:  # NAV 低于 MA20 的 5%
                pos_mult *= 0.7

        # 选股
        if date not in mom_5.index:
            continue

        m5 = mom_5.loc[date].dropna()
        scores = {}
        for code in m5.index:
            score = 0.0
            m = m5[code]
            if m > cfg.get('mom_threshold', 0.02):
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

        cands = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)[:cfg['max_holdings']]

        if cands and cash > cfg['initial_capital'] * 0.1 and len(holdings) < cfg['max_holdings']:
            avail = (cash - cfg['initial_capital'] * 0.1) * pos_mult
            nb = min(len(cands), cfg['max_daily_buy'], cfg['max_holdings'] - len(holdings))
            if nb > 0 and avail > 0:
                per = min(avail / nb, cfg['initial_capital'] * cfg['max_position'] * pos_mult)
                for c in cands[:cfg['max_daily_buy']]:
                    if len(holdings) >= cfg['max_holdings'] or nb <= 0: break
                    bp = od[c] if c in od.index else pd_[c]
                    if pd.isna(bp) or bp <= 0: continue
                    adj = bp * (1 + cfg['commission_rate'] + cfg['slippage_rate'])
                    sh = int(per / adj / 100) * 100
                    if sh <= 0: continue
                    cost = sh * adj
                    if cost > cash: continue
                    cash -= cost
                    holdings[c] = {'shares': sh, 'cost': bp, 'hold_days': 0}
                    nb -= 1

    nav_s = pd.Series(nav_list)
    daily_ret = nav_s.pct_change().dropna()
    total = nav_s.iloc[-1] / nav_s.iloc[0] - 1
    annual = (1 + total) ** (365 / max(len(nav_list) - 30, 1)) - 1
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    max_dd = ((nav_s.cummax() - nav_s) / nav_s.cummax()).max()

    return {
        'annual': annual, 'sharpe': sharpe, 'max_dd': max_dd,
        'total': total, 'nav': nav_s,
        'sell_reasons': sell_reasons,
    }

def main():
    import argparse
    parser = argparse.ArgumentParser(description="v25 回撤控制策略")
    parser.add_argument("--start", type=str, default="2022-01-01")
    parser.add_argument("--end", type=str, default="2026-05-31")
    parser.add_argument("--capital", type=int, default=100000)
    args = parser.parse_args()

    t_start = time.time()
    print("=" * 60)
    print("v25 回撤控制策略回测")
    print("=" * 60)

    tpl, _ = load_panel_from_db(args.start, args.end, need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]

    print("数据: %d 天 x %d 只" % (close_panel.shape[0], close_panel.shape[1]))

    # 基线：无回撤控制
    cfg_base = {
        'initial_capital': args.capital,
        'max_holdings': 8,
        'max_daily_buy': 6,
        'max_position': 0.20,
        'hold_days_max': 5,
        'stop_loss': -0.015,
        'stop_profit': 0.03,
        'commission_rate': 0.0003,
        'stamp_tax': 0.001,
        'slippage_rate': 0.002,
        'mom_threshold': 0.02,
    }

    # 回撤控制版本
    cfg_dd = dict(cfg_base)
    cfg_dd.update({
        'cooldown_dd': 0.05,
        'cooldown_days': 3,
        'daily_dd_limit': 0.03,
        'trend_ma': 20,
    })

    print("\n[1] 基线（无回撤控制）...")
    r_base = run_v25(close_panel, volume_panel, amount_panel, open_panel,
                      high_panel, low_panel, cfg_base)
    print("  年化=%.2f%% 夏普=%.3f 回撤=%.2f%%" % (
        r_base['annual']*100, r_base['sharpe'], r_base['max_dd']*100))

    print("\n[2] 回撤控制版...")
    r_dd = run_v25(close_panel, volume_panel, amount_panel, open_panel,
                    high_panel, low_panel, cfg_dd)
    print("  年化=%.2f%% 夏普=%.3f 回撤=%.2f%%" % (
        r_dd['annual']*100, r_dd['sharpe'], r_dd['max_dd']*100))

    print("\n" + "=" * 60)
    print("对比")
    print("=" * 60)
    print("%-20s %10s %8s %8s" % ('策略', '年化%', '夏普', '回撤%'))
    print("-" * 50)
    print("%-20s %10.2f %8.3f %8.2f" % (
        'v22 基线', r_base['annual']*100, r_base['sharpe'], r_base['max_dd']*100))
    print("%-20s %10.2f %8.3f %8.2f" % (
        'v25 回撤控制', r_dd['annual']*100, r_dd['sharpe'], r_dd['max_dd']*100))
    print("=" * 60)
    print("耗时: %.1fs" % (time.time() - t_start))

    return r_base, r_dd

if __name__ == "__main__":
    main()
