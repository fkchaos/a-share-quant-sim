#!/usr/bin/env python3
"""
v28_dripping_stone — 滴水穿石因子（成交量周期动量）
=====================================================

核心思想：
  主力建仓不是一次性完成的，而是通过持续的、有节奏的买入。
  这种"滴水穿石"模式在日K线上表现为：
  - 成交量呈现周期性放大-缩小（节奏性）
  - 价格稳步上涨（或横盘蓄势）
  - 量增时价格涨，量缩时价格不跌（价量配合）

因子定义（基于日K线代理）：
  1. vol_regime_score: 成交量状态得分
     - 近10日成交量序列的自相关性（lag=1）
     - 正自相关 = 放量后继续放量（趋势性资金）
     - 负自相关 = 放量-缩量交替（节奏性资金 = 滴水穿石）
     - 适度的负自相关（-0.3 ~ -0.1）= 最佳滴水穿石模式
  
  2. price_inertia: 价格惯性
     - 近10日收益率的均值 / 标准差（类似 Sharpe）
     - 高值 = 持续稳定上涨（滴水穿石的结果）
  
  3. vol_price_coupling: 量价耦合度
     - 近10日量比序列和价格变化序列的协方差
     - 正值 = 放量涨、缩量跌（健康）
     - 但"滴水穿石"更关注：缩量时不跌（抗跌性）
  
  4. accumulation_score: 蓄势得分（核心）
     - 近5日涨幅 / 近5日量比变化
     - 涨幅为正但量比下降 = 缩量上涨 = 筹码锁定 = 蓄势
     - 涨幅为正且量比上升 = 放量上涨 = 加速（可能接近尾声）

选股逻辑：
  基础：mom_5 > 2%（保持 v22 动量框架）
  加分：
    vol_regime_score 在 [-0.3, -0.1] 区间: +0.8（节奏性资金）
    price_inertia > 0.5: +0.5（稳定上涨）
    accumulation_score > 0: +0.6（缩量蓄势）
"""

import sys, os
import time
import numpy as np
import pandas as pd

from core.db import load_panel_from_db

class V28Config:
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

def calc_v28_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None):
    """计算 v28 滴水穿石因子"""
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

    # ── 滴水穿石核心因子 ──

    # 1. vol_regime_score: 成交量状态（滚动自相关）
    # 近10日量比的 lag-1 自相关
    vol_5 = volume_panel.rolling(5).mean()
    vol_20 = volume_panel.rolling(20).mean()
    vr = vol_5 / (vol_20 + eps)  # 量比

    # 量比的滚动自相关（lag=1, window=10）
    vr_lag = vr.shift(1)
    vr_mean_10 = vr.rolling(10).mean()
    vr_lag_mean_10 = vr_lag.rolling(10).mean()
    cov_vr = ((vr - vr_mean_10) * (vr_lag - vr_lag_mean_10)).rolling(10).mean()
    var_vr = vr.rolling(10).std() * vr_lag.rolling(10).std()
    factors['vol_regime_score'] = cov_vr / (var_vr + eps)

    # 2. price_inertia: 价格惯性 = 均值 / 标准差
    ret_mean_10 = returns.rolling(10).mean()
    ret_std_10 = returns.rolling(10).std()
    factors['price_inertia'] = ret_mean_10 / (ret_std_10 + eps)

    # 3. vol_price_coupling: 量价耦合度
    ret_mean_5 = returns.rolling(5).mean()
    vr_mean_5 = vr.rolling(5).mean()
    cov_vp = ((returns - ret_mean_5) * (vr - vr_mean_5)).rolling(5).mean()
    factors['vol_price_coupling'] = cov_vp / (returns.rolling(5).std() * vr.rolling(5).std() + eps)

    # 4. accumulation_score: 蓄势得分
    # 涨幅 / 量比变化率
    mom_5 = close_panel.pct_change(5)
    vr_change = vr.pct_change(5)
    factors['accumulation_score'] = mom_5 / (vr_change + eps + 0.5)  # +0.5 防止除零和极端值

    # ── 退市风险 ──
    price_level = close_panel.rolling(20).mean()
    price_trend = close_panel.pct_change(20)
    vol_current = returns.rolling(5).std()
    vol_hist = returns.rolling(60).std()
    vol_abnormal = vol_current / (vol_hist + eps)

    def _zscore(df):
        m = df.mean(axis=1)
        s = df.std(axis=1)
        return (df.sub(m, axis=0)).div(s + eps, axis=0)

    factors['delist_risk'] = (
        -_zscore(price_level) + -_zscore(price_trend) +
        -_zscore(vol_5 / (vol_20 + eps)) + _zscore(vol_abnormal)
    ) / 4.0

    return factors

def select_stocks_v28(factors, date, close_panel, volume_panel, amount_panel, current_holdings, cfg):
    """v28 选股：动量 + 滴水穿石"""
    if date not in factors['mom_5'].index:
        return []

    mom_5 = factors['mom_5'].loc[date].dropna()
    scores = {}

    for code in mom_5.index:
        score = 0.0
        m = mom_5[code]

        if m > cfg.mom_threshold:
            score += m * 100

            # 加分1：成交量节奏（滴水穿石核心）
            if 'vol_regime_score' in factors and date in factors['vol_regime_score'].index:
                vrs = factors['vol_regime_score'].loc[date, code] if code in factors['vol_regime_score'].columns else np.nan
                if not pd.isna(vrs) and -0.35 < vrs < -0.05:
                    score += 0.8  # 节奏性资金

            # 加分2：价格惯性
            if 'price_inertia' in factors and date in factors['price_inertia'].index:
                pi = factors['price_inertia'].loc[date, code] if code in factors['price_inertia'].columns else np.nan
                if not pd.isna(pi) and pi > 0.5:
                    score += 0.5

            # 加分3：蓄势得分
            if 'accumulation_score' in factors and date in factors['accumulation_score'].index:
                acc = factors['accumulation_score'].loc[date, code] if code in factors['accumulation_score'].columns else np.nan
                if not pd.isna(acc) and acc > 0:
                    score += 0.6

            # 加分4：量价耦合
            if 'vol_price_coupling' in factors and date in factors['vol_price_coupling'].index:
                vpc = factors['vol_price_coupling'].loc[date, code] if code in factors['vol_price_coupling'].columns else np.nan
                if not pd.isna(vpc) and vpc > 0.2:
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

def run_v28_backtest(start_date='2022-01-01', end_date='2026-05-31'):
    print("=" * 60)
    print("v28_dripping_stone — 滴水穿石因子回测")
    print("=" * 60)
    t0 = time.time()

    print(f"\n[1/4] 加载数据 ({start_date} ~ {end_date})...")
    tpl, codes = load_panel_from_db(start_date, end_date, need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]
    print(f"  Panel: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只")

    print("\n[2/4] 计算因子...")
    t1 = time.time()
    factors = calc_v28_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel)
    print(f"  因子: {list(factors.keys())}")
    print(f"  因子计算耗时: {time.time()-t1:.1f}s")

    print("\n[3/4] 运行回测...")
    cfg = V28Config()
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
        cands = select_stocks_v28(factors, date, close_panel, volume_panel, amount_panel, holdings, cfg)

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
    print(f"v28 回测结果 ({start_date} ~ {end_date})")
    print(f"{'='*60}")
    print(f"  年化收益: {annual_return*100:.2f}%")
    print(f"  夏普比率: {sharpe:.3f}")
    print(f"  最大回撤: {max_dd*100:.2f}%")
    print(f"  总交易: {total_buys} 买 / {total_sells} 卖")
    print(f"  选股率: {select_days}/{len(dates)-30} 天 ({select_days/max(1,len(dates)-30)*100:.1f}%)")
    print(f"  卖出原因: TP={tp_pct:.1f}% SL={sl_pct:.1f}% TO={to_pct:.1f}%")

    print(f"\n  对比 v22: 252%/5.54/-7.1%")
    print(f"  对比 v27: 252%/5.72/-6.7%")
    print(f"  v28: {annual_return*100:.1f}%/{sharpe:.2f}/{-max_dd*100:.1f}%")

    return {
        'annual_return': annual_return, 'sharpe': sharpe, 'max_dd': max_dd,
        'total_buys': total_buys, 'total_sells': total_sells,
        'nav': nav_df, 'select_days': select_days,
    }

if __name__ == "__main__":
    run_v28_backtest()
