#!/usr/bin/env python3
"""
v22_regime_test — 动量因子分段回测
========================================
按市场环境分段验证动量因子有效性

市场分段定义（基于沪深300指数）：
- 牛市：价格 > MA20 且 MA20 上升
- 熊市：价格 < MA20 且 MA20 下降
- 震荡市：其他

分段方式：用全市场涨跌比（ADR）+ 沪深300 趋势双维度
"""

import sys, os
import time
import numpy as np
import pandas as pd

from core.db import load_panel_from_db

def detect_regimes(close_panel, adr_lookback=20, ma_lookback=60):
    """
    市场分段检测
    
    使用两个维度：
    1. 全市场涨跌比 ADR = 上涨数 / 总数（rolling mean）
    2. 市场趋势 = 全市场平均 close 的 MA60 斜率
    
    Returns: Series of regime labels ('bull'/'bear'/'range')
    """
    # 用截面平均 close 代表市场
    market = close_panel.mean(axis=1)
    
    # MA60 及其斜率
    ma60 = market.rolling(ma_lookback).mean()
    ma_slope = ma60.pct_change(20)  # 20日斜率

    # ADR（截面涨跌比）
    daily_ret = close_panel.pct_change()
    up = (daily_ret > 0).sum(axis=1)
    down = (daily_ret < 0).sum(axis=1)
    total = up + down
    adr = up / total.replace(0, np.nan)
    adr_ma = adr.rolling(adr_lookback).mean()
    
    adr_norm = adr_ma.rolling(252).rank(pct=True)  # 历史分位

    regimes = pd.Series('range', index=close_panel.index, dtype='object')
    
    # 牛市：ADR高位 + MA斜率正
    bull_mask = (adr_norm > 0.6) & (ma_slope > 0.02)
    regimes[bull_mask] = 'bull'
    
    # 熊市：ADR低位 + MA斜率负
    bear_mask = (adr_norm < 0.4) & (ma_slope < -0.02)
    regimes[bear_mask] = 'bear'
    
    # 震荡：ADR中间 + MA斜率接近0
    range_mask = (adr_norm >= 0.4) & (adr_norm <= 0.6) & (ma_slope.abs() < 0.01)
    regimes[range_mask] = 'range'
    
    return regimes, adr_norm, ma_slope

def run_v22_on_dates(close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel, dates, cfg):
    """在指定日期子集上跑 v22"""
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

    valid_dates = [d for d in close_panel.index if d in dates]

    for i, date in enumerate(close_panel.index):
        if i < 30 or date not in valid_dates:
            nav_list.append(nav_list[-1] if nav_list else cash)
            continue

        pd_ = close_panel.loc[date]
        od = open_panel.loc[date]

        for c in holdings:
            holdings[c]['hold_days'] = holdings[c].get('hold_days', 0) + 1

        to_sell = []
        for c, h in holdings.items():
            if c not in pd_.index: continue
            cp = pd_[c]
            if pd.isna(cp) or cp <= 0: continue
            pnl = (cp - h['cost']) / h['cost']
            if pnl <= cfg['stop_loss']: to_sell.append(c); continue
            if pnl >= cfg['stop_profit']: to_sell.append(c); continue
            if h.get('hold_days', 0) >= cfg['hold_days_max']: to_sell.append(c)

        sold = set()
        for c in to_sell:
            if c not in pd_.index: continue
            sp = pd_[c]
            if pd.isna(sp) or sp <= 0: continue
            h = holdings[c]
            sv = h['shares'] * sp * (1 - cfg['commission_rate'] - cfg['stamp_tax'] - cfg['slippage_rate'])
            cash += sv; sold.add(c)
        for c in sold: holdings.pop(c, None)

        if date not in mom_5.index:
            nav_list.append(nav_list[-1]); continue

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
            avail = cash - cfg['initial_capital'] * 0.1
            nb = min(len(cands), cfg['max_daily_buy'], cfg['max_holdings'] - len(holdings))
            if nb > 0:
                per = min(avail / nb, cfg['initial_capital'] * cfg['max_position'])
                for c in cands[:cfg['max_daily_buy']]:
                    if len(holdings) >= cfg['max_holdings'] or nb <= 0: break
                    bp2 = od[c] if c in od.index else pd_[c]
                    if pd.isna(bp2) or bp2 <= 0: continue
                    adj = bp2 * (1 + cfg['commission_rate'] + cfg['slippage_rate'])
                    sh = int(per / adj / 100) * 100
                    if sh <= 0: continue
                    cost = sh * adj
                    if cost > cash: continue
                    cash -= cost
                    holdings[c] = {'shares': sh, 'cost': bp2, 'hold_days': 0}
                    nb -= 1

        nav = cash
        for c, h in holdings.items():
            if c in pd_.index:
                cp = pd_[c]
                if not pd.isna(cp) and cp > 0: nav += h['shares'] * cp
        nav_list.append(nav)

    nav_s = pd.Series(nav_list, index=close_panel.index)
    return nav_s

def main():
    import argparse
    parser = argparse.ArgumentParser(description="v22 分段回测")
    parser.add_argument("--start", type=str, default="2021-01-01")
    parser.add_argument("--end", type=str, default="2026-05-31")
    parser.add_argument("--capital", type=int, default=100000)
    args = parser.parse_args()

    print("=" * 60)
    print("v22 动量因子分段回测")
    print("=" * 60)

    tpl, _ = load_panel_from_db(args.start, args.end, need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]

    print("数据: %d 天 x %d 只 | %s ~ %s" % (
        close_panel.shape[0], close_panel.shape[1],
        close_panel.index[0].strftime('%Y-%m-%d'),
        close_panel.index[-1].strftime('%Y-%m-%d')))

    # 市场分段
    regimes, adr_norm, ma_slope = detect_regimes(close_panel)
    
    regime_stats = regimes.value_counts()
    print("\n市场分段分布:")
    for r in ['bull', 'range', 'bear']:
        cnt = regime_stats.get(r, 0)
        pct = cnt / len(regimes) * 100
        print("  %s: %d 天 (%.1f%%)" % (r, cnt, pct))

    cfg = {
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

    # 全量回测
    t0 = time.time()
    nav_full = run_v22_on_dates(close_panel, volume_panel, amount_panel,
                                 high_panel, low_panel, open_panel,
                                 set(close_panel.index), cfg)
    elapsed = time.time() - t0

    # 分段统计
    print("\n" + "=" * 60)
    print("分段回测结果")
    print("=" * 60)

    results = {}
    for regime in ['bull', 'range', 'bear', 'all']:
        if regime == 'all':
            mask = pd.Series(True, index=close_panel.index)
        else:
            mask = (regimes == regime)
        
        regime_dates = mask[mask].index
        if len(regime_dates) == 0:
            continue

        # 计算该分段内的 NAV 变化
        nav_regime = nav_full[regime_dates]
        if len(nav_regime) < 2:
            continue

        # 分段内收益（用该分段第一天的 NAV 作为起点）
        start_nav = nav_regime.iloc[0]
        end_nav = nav_regime.iloc[-1]
        total_ret = end_nav / start_nav - 1 if start_nav > 0 else 0

        # 分段内日收益
        daily_ret = nav_regime.pct_change().dropna()
        sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
        max_dd = ((nav_regime.cummax() - nav_regime) / nav_regime.cummax()).max()

        n_days = len(regime_dates)
        annual = (1 + total_ret) ** (365 / max(n_days, 1)) - 1

        results[regime] = {
            'days': n_days, 'total_ret': total_ret,
            'annual': annual, 'sharpe': sharpe, 'max_dd': max_dd
        }

        label = {'bull': '牛市', 'range': '震荡', 'bear': '熊市', 'all': '全量'}[regime]
        print("\n  [%s] %d 天" % (label, n_days))
        print("    区间收益: %.2f%%" % (total_ret * 100))
        print("    年化收益: %.2f%%" % (annual * 100))
        print("    夏普比率: %.3f" % sharpe)
        print("    最大回撤: %.2f%%" % (max_dd * 100))

    print("\n  全量回测耗时: %.1fs" % elapsed)

    # 月度收益分析
    print("\n" + "=" * 60)
    print("月度收益分析（全量）")
    print("=" * 60)
    monthly = nav_full.resample('ME').last().pct_change().dropna()
    print("  正收益月份: %d/%d (%.0f%%)" % (
        (monthly > 0).sum(), len(monthly), (monthly > 0).mean() * 100))
    print("  平均月收益: %.2f%%" % (monthly.mean() * 100))
    print("  最大月收益: %.2f%%" % (monthly.max() * 100))
    print("  最大月亏损: %.2f%%" % (monthly.min() * 100))

    # 年度收益
    print("\n" + "=" * 60)
    print("年度收益")
    print("=" * 60)
    yearly = nav_full.resample('YE').last().pct_change().dropna()
    for year, ret in yearly.items():
        print("  %d: %.2f%%" % (year.year, ret * 100))

    return results

if __name__ == "__main__":
    main()
