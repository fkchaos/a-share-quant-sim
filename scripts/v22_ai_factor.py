#!/usr/bin/env python3
"""
v22_ai_factor — 基于 AI 因子分析优化的策略
==============================================

改进点（基于 IC 分析结果）：
1. rev_5(反转) → mom_5(动量): IR 从 -0.019 → +0.019
2. 加入 gap_ratio(跳空因子): IR=0.162
3. 加入 illiquidity(非流动性): IR=0.275 (最强因子)
4. 加入 boll_width_20(布林带宽): IR=0.132
5. 剔除冗余因子 (vol_10/vol_60/rsi_6/rsi_28/boll_pos_10/rev_3/rev_10/mom_10/mom_20/mom_60)

评分逻辑：
  基础分：mom_5 × 100（涨幅越大分越高）
  辅助加分：
    gap_ratio > 0.02: +0.5
    illiquidity > 中位数: +0.8
    boll_width_20 > 1.2: +0.3
    vol_20 > 1.5: +0.2
  风控：SL=1.5% TP=3% hold=5
"""

import sys, os
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from core.db import load_panel_from_db


class V22Config:
    # 选股参数
    mom_threshold = 0.02       # 涨幅阈值（取代 rev_threshold）
    max_holdings = 8
    max_daily_buy = 6
    max_position = 0.20
    hold_days_max = 5
    hold_days_min = 2
    stop_loss = -0.015
    stop_profit = 0.03

    # 交易成本
    commission_rate = 0.0003
    stamp_tax = 0.001
    slippage_rate = 0.002
    initial_capital = 200000


def calc_v22_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None):
    """计算 v22 因子"""
    returns = close_panel.pct_change()
    eps = 1e-10

    factors = {}

    # 1. 动量因子（取代反转）
    factors['mom_5'] = close_panel.pct_change(5)

    # 2. 跳空因子
    prev_close = close_panel.shift(1)
    factors['gap_ratio'] = (open_panel - prev_close) / (prev_close + eps) if open_panel is not None else returns * 0

    # 3. 非流动性因子（小市值代理）
    # 用 amount 的倒数近似：成交额越低 = 流动性越差 = 小市值
    avg_amount = amount_panel.rolling(20).mean()
    factors['illiquidity'] = 1.0 / (avg_amount / 1e8 + eps)  # 标准化

    # 4. 布林带宽
    ma20 = close_panel.rolling(20).mean()
    std20 = close_panel.rolling(20).std()
    factors['boll_width_20'] = (4 * std20) / (ma20 + eps)

    # 5. 波动率
    factors['vol_20'] = returns.rolling(20).std()

    # 6. 振幅
    factors['amplitude'] = (high_panel - low_panel) / (close_panel + eps)

    return factors


def select_stocks_v22(factors, date, close_panel, volume_panel, amount_panel, current_holdings, cfg):
    """v22 选股：动量+辅助因子"""
    if date not in factors['mom_5'].index:
        return []

    mom_5 = factors['mom_5'].loc[date].dropna()

    scores = {}
    for code in mom_5.index:
        score = 0.0
        m = mom_5[code]

        # 基础分：涨幅越大分越高（取代反转因子的跌幅逻辑）
        if m > cfg.mom_threshold:
            score += m * 100  # 涨幅的百分数作为基础分

            # 跳空加分
            if 'gap_ratio' in factors and date in factors['gap_ratio'].index:
                gr = factors['gap_ratio'].loc[date, code] if code in factors['gap_ratio'].columns else 0
                if not pd.isna(gr) and gr > 0.02:
                    score += 0.5

            # 非流动性加分
            if 'illiquidity' in factors and date in factors['illiquidity'].index:
                illiq = factors['illiquidity'].loc[date, code] if code in factors['illiquidity'].columns else 0
                if not pd.isna(illiq) and illiq > 0:
                    score += 0.8

            # 布林带宽加分
            if 'boll_width_20' in factors and date in factors['boll_width_20'].index:
                bw = factors['boll_width_20'].loc[date, code] if code in factors['boll_width_20'].columns else 0
                if not pd.isna(bw) and bw > 1.2:
                    score += 0.3

        if score > 0:
            scores[code] = score

    if current_holdings:
        scores = {c: s for c, s in scores.items() if c not in current_holdings}

    candidates = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)
    return candidates[:cfg.max_holdings]


def run_v22_backtest(start_date='2022-01-01', end_date='2026-05-31'):
    print("=" * 60)
    print("v22_ai_factor — AI 因子优化策略回测")
    print("=" * 60)
    t0 = time.time()

    # 加载数据
    print("\n[1/4] 加载数据...")
    tpl, codes = load_panel_from_db(start_date, end_date, need_open=True, need_hl=True)
    close_panel = tpl[0]
    volume_panel = tpl[1]
    amount_panel = tpl[2]
    open_panel = tpl[3]
    high_panel = tpl[4]
    low_panel = tpl[5]
    print(f"  Panel: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只")

    # 计算因子
    print("\n[2/4] 计算因子...")
    factors = calc_v22_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel)
    print(f"  因子: {list(factors.keys())}")

    # 回测
    print("\n[3/4] 运行回测...")
    cfg = V22Config()
    cash = cfg.initial_capital
    holdings = {}
    nav_list = []
    trade_log = []
    select_days = 0
    total_buys = 0
    total_sells = 0
    sell_reasons = {}

    dates = close_panel.index[close_panel.index >= pd.Timestamp(start_date)]

    for i, date in enumerate(dates):
        if i < 30:
            nav_list.append((date, cash))
            continue

        price_data = close_panel.loc[date] if date in close_panel.index else None
        if price_data is None:
            nav_list.append((date, cash))
            continue

        open_data = open_panel.loc[date] if open_panel is not None else price_data

        for c in holdings:
            holdings[c]['hold_days'] = holdings[c].get('hold_days', 0) + 1

        # 风控
        to_sell = []
        for c, h in holdings.items():
            if c not in price_data.index: continue
            cp = price_data[c]
            if pd.isna(cp) or cp <= 0: continue
            pnl = (cp - h['cost']) / h['cost']
            hd = h.get('hold_days', 0)
            if pnl <= cfg.stop_loss: to_sell.append((c, 'SL', pnl)); continue
            if pnl >= cfg.stop_profit: to_sell.append((c, 'TP', pnl)); continue
            if hd >= cfg.hold_days_max: to_sell.append((c, 'TO', pnl))

        sold_codes = set()
        for c, reason, pnl in to_sell:
            if c not in price_data.index: continue
            sp = price_data[c]
            if pd.isna(sp) or sp <= 0: continue
            h = holdings[c]
            sv = h['shares'] * sp * (1 - cfg.commission_rate - cfg.stamp_tax - cfg.slippage_rate)
            cash += sv; sold_codes.add(c)
            total_sells += 1
            sell_reasons[reason] = sell_reasons.get(reason, 0) + 1
        for c in sold_codes: holdings.pop(c, None)

        # 选股 + 买入
        cands = select_stocks_v22(factors, date, close_panel, volume_panel, amount_panel, holdings, cfg)

        if cands and cash > cfg.initial_capital * 0.1 and len(holdings) < cfg.max_holdings:
            available = cash - cfg.initial_capital * 0.1
            n_buy = min(len(cands), cfg.max_daily_buy, cfg.max_holdings - len(holdings))
            per_stock = min(available / n_buy, cfg.initial_capital * cfg.max_position)
            bought = 0
            for c in cands[:cfg.max_daily_buy]:
                if bought >= n_buy: break
                bp = open_data[c] if c in open_data.index else price_data[c]
                if pd.isna(bp) or bp <= 0: continue
                adj = bp * (1 + cfg.commission_rate + cfg.slippage_rate)
                sh = int(per_stock / adj / 100) * 100
                if sh <= 0: continue
                cost = sh * adj
                if cost > cash: continue
                cash -= cost
                holdings[c] = {'shares': sh, 'cost': bp, 'hold_days': 0}
                bought += 1
                total_buys += 1

        if cands:
            select_days += 1

        nav = cash
        for c, h in holdings.items():
            if c in price_data.index:
                cp = price_data[c]
                if not pd.isna(cp) and cp > 0:
                    nav += h['shares'] * cp
        nav_list.append((date, nav))

    elapsed = time.time() - t0

    # 统计
    nav_df = pd.DataFrame(nav_list, columns=['date', 'nav']).set_index('date')
    nav_df['ret'] = nav_df['nav'].pct_change()
    total_return = (nav_df['nav'].iloc[-1] / cfg.initial_capital) - 1
    days_count = (nav_df.index[-1] - nav_df.index[0]).days
    annual_return = (1 + total_return) ** (365 / max(days_count, 1)) - 1
    sharpe = nav_df['ret'].mean() / nav_df['ret'].std() * np.sqrt(252) if nav_df['ret'].std() > 0 else 0
    max_dd = ((nav_df['nav'].cummax() - nav_df['nav']) / nav_df['nav'].cummax()).max()

    print(f"\n[4/4] 回测完成 ({elapsed:.1f}s)")
    print(f"\n{'='*60}")
    print(f"v22 回测结果 ({start_date} ~ {end_date})")
    print(f"{'='*60}")
    print(f"  年化收益: {annual_return*100:.2f}%")
    print(f"  夏普比率: {sharpe:.3f}")
    print(f"  最大回撤: {max_dd*100:.2f}%")
    print(f"  总交易: {total_buys} 买 / {total_sells} 卖")
    print(f"  选股率: {select_days}/{len(dates)-30} 天 ({select_days/max(1,len(dates)-30)*100:.1f}%)")
    print(f"  卖出原因: {sell_reasons}")
    print(f"\n  对比 v13 (同参数): 待对比")

    return {
        'annual_return': annual_return, 'sharpe': sharpe, 'max_dd': max_dd,
        'total_buys': total_buys, 'total_sells': total_sells,
        'nav': nav_df,
    }


if __name__ == "__main__":
    run_v22_backtest()
