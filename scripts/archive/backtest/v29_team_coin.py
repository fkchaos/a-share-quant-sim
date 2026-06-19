#!/usr/bin/env python3
"""
v29_team_coin — 球队硬币因子（市场状态自适应动量）
=====================================================

核心思想（不同于 v19 的简单多因子等权）：
  不同股票在不同市场状态下表现不同 —— 就像球队有主场/客场之分。
  "硬币"指的是市场状态会随机切换，策略需要自适应。

市场状态定义（基于波动率 + 趋势）：
  1. 低波动牛市 (low_vol_bull): vol_20 < 中位数 & MA60 斜率 > 0
     → 动量因子最有效（强者恒强）
  2. 高波动牛市 (high_vol_bull): vol_20 > 中位数 & MA60 斜率 > 0
     → 反转因子有效（波动大 → 超涨回调）
  3. 低波动熊市 (low_vol_bear): vol_20 < 中位数 & MA60 斜率 < 0
     → 防御因子（低波+质量）
  4. 高波动熊市 (high_vol_bear): vol_20 > 中位数 & MA60 斜率 < 0
     → 强反转（恐慌后反弹）

因子定义：
  1. market_regime: 市场状态得分 (-1=熊市, 0=震荡, 1=牛市)
     = MA60斜率排名 + 全市场涨跌比排名 的综合
  2. adaptive_mom: 自适应动量
     = mom_5 × (1 + market_regime)  [牛市加强动量]
     + rev_5 × (1 - market_regime)  [熊市加强反转]
  3. regime_vol_score: 状态-波动率得分
     = vol_of_vol × (-market_regime)  [牛市时低vol_of_vol好，熊市时高vol_of_vol好]
  4. coin_flip_signal: 硬币翻转信号
     = 前一天的 market_regime 与当天的差值
     = 市场状态切换时产生信号（翻转 = 机会）

选股逻辑：
  基础：mom_5 > 2%（保持动量框架）
  加分：
    market_regime > 0.3: +0.5（牛市状态，动量加强）
    adaptive_mom 排名前30%: +0.8
    coin_flip_signal > 0.5: +0.6（市场状态翻转 → 新机会）
  减分/排除：
    market_regime < -0.5 且 mom_5 < 5%: 熊市弱动量排除
"""

import sys, os
import time
import numpy as np
import pandas as pd

from core.db import load_panel_from_db

class V29Config:
    mom_threshold = 0.02
    max_holdings = 8
    max_daily_buy = 6
    max_position = 0.20
    hold_days_max = 5
    hold_days_min = 2
    stop_loss = -0.015
    stop_profit = 0.03
    commission_rate = 0.0003
    stamp_tax = 0.001
    slippage_rate = 0.002
    initial_capital = 200000

def calc_v29_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None):
    """计算 v29 球队硬币因子"""
    returns = close_panel.pct_change()
    eps = 1e-10
    factors = {}

    # ── 基础因子 ──
    factors['mom_5'] = close_panel.pct_change(5)
    factors['mom_10'] = close_panel.pct_change(10)

    # ── 跳空因子 ──
    prev_close = close_panel.shift(1)
    factors['gap_ratio'] = (open_panel - prev_close) / (prev_close + eps) if open_panel is not None else returns * 0

    # ── 非流动性因子 ──
    avg_amount = amount_panel.rolling(20).mean()
    factors['illiquidity'] = 1.0 / (avg_amount / 1e8 + eps)

    # ── 布林带宽 ──
    ma20 = close_panel.rolling(20).mean()
    std20 = close_panel.rolling(20).std()
    factors['boll_width_20'] = (4 * std20) / (ma20 + eps)

    # ── 球队硬币核心因子 ──

    # 1. market_regime: 市场状态（截面指标，必须是 Series）
    # 市场趋势：全市场收益率的中位数 MA60 斜率
    market_ret = returns.median(axis=1)
    market_ma60 = market_ret.rolling(60).mean()
    market_slope = market_ma60.pct_change(20)
    market_slope_norm = (market_slope - market_slope.rolling(60).mean()) / (market_slope.rolling(60).std() + eps)

    # 全市场涨跌比
    up_ratio = (returns > 0).sum(axis=1) / returns.shape[1]
    up_ratio_norm = (up_ratio - up_ratio.rolling(60).mean()) / (up_ratio.rolling(60).std() + eps)

    # 市场波动率（截面中位数）
    vol_median = returns.rolling(20).std().median(axis=1)
    vol_norm = (vol_median - vol_median.rolling(60).mean()) / (vol_median.rolling(60).std() + eps)

    # 市场状态得分（Series）
    market_regime_raw = market_slope_norm * 0.4 + up_ratio_norm * 0.4 - vol_norm * 0.2
    market_regime = (market_regime_raw - 0.5) * 2  # [-1, 1]
    market_regime = market_regime.reindex(close_panel.index).ffill().fillna(0)
    factors['market_regime'] = market_regime

    # 2. adaptive_mom: 自适应动量（个股 × 市场状态）
    mom_5 = close_panel.pct_change(5)
    regime_np = market_regime.values[:, np.newaxis]  # (days, 1) 广播
    mom_5_np = mom_5.values
    adaptive_mom_np = mom_5_np * (1 + regime_np) + (-mom_5_np) * (1 - regime_np)
    factors['adaptive_mom'] = pd.DataFrame(adaptive_mom_np, index=close_panel.index, columns=close_panel.columns)

    # 3. regime_vol_score: 状态-波动率得分
    vol_20_returns = returns.rolling(20).std()
    vol_of_vol = vol_20_returns.rolling(20).std()
    vov_np = vol_of_vol.values
    regime_vol_score_np = vov_np * (-regime_np)
    factors['regime_vol_score'] = pd.DataFrame(regime_vol_score_np, index=close_panel.index, columns=close_panel.columns)

    # 4. coin_flip_signal: 硬币翻转信号（市场状态切换）
    coin_flip = market_regime.diff(3)
    factors['coin_flip_signal'] = coin_flip

    # ── 退市风险 ──
    price_level = close_panel.rolling(20).mean()
    price_trend = close_panel.pct_change(20)
    vol_shrink = volume_panel.rolling(5).mean() / (volume_panel.rolling(20).mean() + eps)
    vol_current = returns.rolling(5).std()
    vol_hist = returns.rolling(60).std()
    vol_abnormal = vol_current / (vol_hist + eps)

    def _zscore(df):
        m = df.mean(axis=1)
        s = df.std(axis=1)
        return (df.sub(m, axis=0)).div(s + eps, axis=0)

    factors['delist_risk'] = (
        -_zscore(price_level) + -_zscore(price_trend) +
        -_zscore(vol_shrink) + _zscore(vol_abnormal)
    ) / 4.0

    return factors

def select_stocks_v29(factors, date, close_panel, volume_panel, amount_panel, current_holdings, cfg):
    """v29 选股：市场状态自适应动量"""
    if date not in factors['mom_5'].index:
        return []

    mom_5 = factors['mom_5'].loc[date].dropna()
    scores = {}

    # 市场状态
    regime = factors['market_regime'].loc[date] if date in factors['market_regime'].index else 0

    for code in mom_5.index:
        score = 0.0
        m = mom_5[code]

        if m > cfg.mom_threshold:
            score += m * 100

            # 加分1：牛市状态加强
            if regime > 0.3:
                score += 0.5

            # 加分2：自适应动量排名
            if 'adaptive_mom' in factors and date in factors['adaptive_mom'].index:
                am = factors['adaptive_mom'].loc[date, code] if code in factors['adaptive_mom'].columns else np.nan
                if not pd.isna(am) and am > 0:
                    score += min(am * 50, 2.0)  # 封顶

            # 加分3：硬币翻转信号
            if 'coin_flip_signal' in factors and date in factors['coin_flip_signal'].index:
                cf = factors['coin_flip_signal'].loc[date] if isinstance(factors['coin_flip_signal'].loc[date], (int, float)) else 0
                if cf > 0.3:
                    score += 0.6

            # 加分4：状态-波动率
            if 'regime_vol_score' in factors and date in factors['regime_vol_score'].index:
                rvs = factors['regime_vol_score'].loc[date, code] if code in factors['regime_vol_score'].columns else np.nan
                if not pd.isna(rvs):
                    if regime > 0 and rvs < 0:  # 牛市低波动好
                        score += 0.3
                    elif regime < 0 and rvs > 0:  # 熊市高波动好
                        score += 0.3

            # 跳空加分
            if 'gap_ratio' in factors and date in factors['gap_ratio'].index:
                gr = factors['gap_ratio'].loc[date, code] if code in factors['gap_ratio'].columns else 0
                if not pd.isna(gr) and gr > 0.02:
                    score += 0.5

            # 非流动性加分
            if 'illiquidity' in factors and date in factors['illiquidity'].index:
                il = factors['illiquidity'].loc[date, code] if code in factors['illiquidity'].columns else 0
                if not pd.isna(il) and il > 0:
                    score += 0.8

            # 布林带宽加分
            if 'boll_width_20' in factors and date in factors['boll_width_20'].index:
                bw = factors['boll_width_20'].loc[date, code] if code in factors['boll_width_20'].columns else 0
                if not pd.isna(bw) and bw > 1.2:
                    score += 0.3

            # 排除：熊市弱动量
            if regime < -0.5 and m < 0.05:
                score = 0

        if score > 0:
            scores[code] = score

    # 排除退市风险
    if 'delist_risk' in factors and date in factors['delist_risk'].index:
        dr = factors['delist_risk'].loc[date]
        dr_threshold = dr.quantile(0.9)
        scores = {c: s for c, s in scores.items()
                  if c not in dr.index or dr[c] <= dr_threshold}

    if current_holdings:
        scores = {c: s for c, s in scores.items() if c not in current_holdings}

    candidates = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)
    return candidates[:cfg.max_holdings]

def run_v29_backtest(start_date='2022-01-01', end_date='2026-05-31'):
    print("=" * 60)
    print("v29_team_coin — 球队硬币因子回测")
    print("=" * 60)
    t0 = time.time()

    print(f"\n[1/4] 加载数据 ({start_date} ~ {end_date})...")
    tpl, codes = load_panel_from_db(start_date, end_date, need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]
    print(f"  Panel: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只")

    print("\n[2/4] 计算因子...")
    t1 = time.time()
    factors = calc_v29_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel)
    print(f"  因子: {list(factors.keys())}")
    print(f"  因子计算耗时: {time.time()-t1:.1f}s")

    print("\n[3/4] 运行回测...")
    cfg = V29Config()
    cash = cfg.initial_capital
    holdings = {}
    nav_list = []
    select_days = 0
    total_buys = 0
    total_sells = 0
    sell_reasons = {}

    dates = close_panel.index[close_panel.index >= pd.Timestamp(start_date)]

    for i, date in enumerate(dates):
        if i < 30:
            nav_list.append((date, cash))
            continue
        if date not in close_panel.index:
            nav_list.append((nav_list[-1][1] if nav_list else cash))
            continue

        price_data = close_panel.loc[date]
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
            if i > 0:
                pc = close_panel.iloc[i-1].get(c)
                if pc and not pd.isna(pc) and pc > 0 and sp <= pc * 0.90 * 1.01:
                    holdings[c]['hold_days'] = max(0, holdings[c].get('hold_days', 0) - 1)
                    continue
            h = holdings[c]
            sv = h['shares'] * sp * (1 - cfg.commission_rate - cfg.stamp_tax - cfg.slippage_rate)
            cash += sv; sold_codes.add(c)
            total_sells += 1
            sell_reasons[reason] = sell_reasons.get(reason, 0) + 1
        for c in sold_codes: holdings.pop(c, None)

        # 选股 + 买入
        cands = select_stocks_v29(factors, date, close_panel, volume_panel, amount_panel, holdings, cfg)

        if cands and cash > cfg.initial_capital * 0.1 and len(holdings) < cfg.max_holdings:
            available = cash - cfg.initial_capital * 0.1
            n_buy = min(len(cands), cfg.max_daily_buy, cfg.max_holdings - len(holdings))
            per_stock = min(available / n_buy, cfg.initial_capital * cfg.max_position)
            bought = 0
            for c in cands[:cfg.max_daily_buy]:
                if bought >= n_buy: break
                bp = open_data[c] if c in open_data.index else price_data[c]
                if pd.isna(bp) or bp <= 0: continue
                if i > 0:
                    pc = close_panel.iloc[i-1].get(c)
                    if pc and not pd.isna(pc) and pc > 0 and bp >= pc * 1.10 * 0.99:
                        continue
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

    nav_df = pd.DataFrame(nav_list, columns=['date', 'nav']).set_index('date')
    nav_df['ret'] = nav_df['nav'].pct_change()
    total_return = (nav_df['nav'].iloc[-1] / cfg.initial_capital) - 1
    days_count = (nav_df.index[-1] - nav_df.index[0]).days
    annual_return = (1 + total_return) ** (365 / max(days_count, 1)) - 1
    sharpe = nav_df['ret'].mean() / nav_df['ret'].std() * np.sqrt(252) if nav_df['ret'].std() > 0 else 0
    max_dd = ((nav_df['nav'].cummax() - nav_df['nav']) / nav_df['nav'].cummax()).max()

    tp_pct = sell_reasons.get('TP', 0) / max(total_sells, 1) * 100
    sl_pct = sell_reasons.get('SL', 0) / max(total_sells, 1) * 100
    to_pct = sell_reasons.get('TO', 0) / max(total_sells, 1) * 100

    print(f"\n[4/4] 回测完成 ({elapsed:.1f}s)")
    print(f"\n{'='*60}")
    print(f"v29 回测结果 ({start_date} ~ {end_date})")
    print(f"{'='*60}")
    print(f"  年化收益: {annual_return*100:.2f}%")
    print(f"  夏普比率: {sharpe:.3f}")
    print(f"  最大回撤: {max_dd*100:.2f}%")
    print(f"  总交易: {total_buys} 买 / {total_sells} 卖")
    print(f"  选股率: {select_days}/{len(dates)-30} 天 ({select_days/max(1,len(dates)-30)*100:.1f}%)")
    print(f"  卖出原因: TP={tp_pct:.1f}% SL={sl_pct:.1f}% TO={to_pct:.1f}%")

    print(f"\n  对比 v22: 252%/5.54/-7.1%")
    print(f"  对比 v27: 251%/5.72/-6.7%")
    print(f"  v29: {annual_return*100:.1f}%/{sharpe:.2f}/{-max_dd*100:.1f}%")

    return {
        'annual_return': annual_return, 'sharpe': sharpe, 'max_dd': max_dd,
        'total_buys': total_buys, 'total_sells': total_sells,
        'nav': nav_df, 'select_days': select_days,
    }

if __name__ == "__main__":
    run_v29_backtest()
