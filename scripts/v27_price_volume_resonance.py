#!/usr/bin/env python3
"""
v27_price_volume_resonance — 价量共振因子
============================================

核心思想（不同于 v17 的 price_volume_tension）：
- v17: 价格偏离度 × 量能变化率（两个独立信号的乘积）
- v27: 价格趋势与量能趋势的滚动相关系数（共振度）

因子定义：
1. pv_corr_10: 近10日收益率与量比的滚动相关系数
   - 正相关 = 放量上涨/缩量下跌（健康趋势）
   - 负相关 = 缩量上涨/放量下跌（趋势衰竭信号）
2. pv_corr_20: 近20日收益率与量比的滚动相关系数（中期）
3. vol_price_divergence: 量价背离度 = 价格动量排名 - 量能动量排名
   - 正值 = 价格跑赢量能（缩量上涨，警惕）
   - 负值 = 量能跑赢价格（放量待涨，机会）

选股逻辑（基于 v22 动量框架扩展）：
  基础分：mom_5 × 100（必须 > 2%）
  共振加分：
    pv_corr_10 排名前30%: +0.5（量价同步的健康趋势）
    vol_price_divergence < -0.3: +0.8（量能蓄势，即将爆发）
  风控加分：
    pv_corr_20 > 0: +0.3（中期趋势健康）
  排除：
    pv_corr_10 < -0.5: 量价严重背离的不选
    delist_risk 前10%: 排除退市风险股

对比 v17 的改进：
  - v17 IC_IR=0.032（极弱）
  - 预期 v27 的 IC_IR > 0.08（因捕捉的是趋势健康度，非简单量价乘积）
"""

import sys, os
import time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from core.db import load_panel_from_db


class V27Config:
    # 选股参数
    mom_threshold = 0.02       # 涨幅阈值
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

    # 价量共振阈值
    pv_corr_percentile = 0.30  # pv_corr_10 前30%加分
    vp_div_threshold = -0.3    # 量价背离度阈值
    pv_corr_20_min = 0         # 中期趋势健康度


def calc_v27_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel=None):
    """计算 v27 价量共振因子"""
    returns = close_panel.pct_change()
    eps = 1e-10
    factors = {}

    # ── 基础因子 ──
    factors['mom_5'] = close_panel.pct_change(5)
    factors['mom_10'] = close_panel.pct_change(10)
    factors['mom_20'] = close_panel.pct_change(20)

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

    # ── 价量共振因子（核心新因子）──
    # 量比序列
    vol_5 = volume_panel.rolling(5).mean()
    vol_20 = volume_panel.rolling(20).mean()
    vr = vol_5 / (vol_20 + eps)  # 量比

    # 日收益率序列
    daily_ret = close_panel.pct_change()

    # pv_corr_10: 近10日 daily_ret 与 vr 的滚动相关系数
    # 对每个股票，逐日计算过去10天的 ret-vr 相关系数
    def _rolling_corr(x, y, window):
        """计算两个面板的滚动相关系数"""
        corr_df = pd.DataFrame(index=x.index, columns=x.columns)
        for i in range(window, len(x)):
            x_win = x.iloc[i-window:i].values.T  # (stocks, days)
            y_win = y.iloc[i-window:i].values.T
            for j in range(x_win.shape[0]):
                xv = x_win[j]
                yv = y_win[j]
                mask = ~(np.isnan(xv) | np.isnan(yv))
                if mask.sum() >= 5:
                    xv, yv = xv[mask], yv[mask]
                    x_std = xv.std()
                    y_std = yv.std()
                    if x_std > eps and y_std > eps:
                        corr_df.iloc[i, j] = np.corrcoef(xv, yv)[0, 1]
        return corr_df

    # 简化版：用 pandas rolling corr（更快但逐列处理）
    def _fast_rolling_corr_panel(ret_df, vol_df, window):
        """面板级别的滚动相关系数（向量化实现）"""
        # 标准化
        ret_std = ret_df.rolling(window).std()
        vol_std = vol_df.rolling(window).std()
        ret_mean = ret_df.rolling(window).mean()
        vol_mean = vol_df.rolling(window).mean()

        # E[XY] - E[X]E[Y]
        xy_mean = (ret_df * vol_df).rolling(window).mean()
        cov = xy_mean - ret_mean * vol_mean
        corr = cov / (ret_std * vol_std + eps)
        return corr

    factors['pv_corr_10'] = _fast_rolling_corr_panel(daily_ret, vr, 10)
    factors['pv_corr_20'] = _fast_rolling_corr_panel(daily_ret, vr, 20)

    # ── 量价背离度 ──
    # 价格动量排名（截面百分位）vs 量能动量排名
    mom_rank = close_panel.pct_change(5).rank(axis=1, pct=True)
    vol_rank = vr.rank(axis=1, pct=True)
    factors['vol_price_divergence'] = mom_rank - vol_rank

    # ── 退市风险 ──
    price_level = close_panel.rolling(20).mean()
    price_trend = close_panel.pct_change(20)
    vol_shrink = vol_5 / (vol_20 + eps)
    vol_current = daily_ret.rolling(5).std()
    vol_hist = daily_ret.rolling(60).std()
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


def select_stocks_v27(factors, date, close_panel, volume_panel, amount_panel, current_holdings, cfg):
    """v27 选股：动量 + 价量共振"""
    if date not in factors['mom_5'].index:
        return []

    mom_5 = factors['mom_5'].loc[date].dropna()

    scores = {}
    for code in mom_5.index:
        score = 0.0
        m = mom_5[code]

        # 基础分：涨幅 > 阈值
        if m > cfg.mom_threshold:
            score += m * 100

            # 共振加分1：短期量价相关（健康趋势）
            if 'pv_corr_10' in factors and date in factors['pv_corr_10'].index:
                pv10 = factors['pv_corr_10'].loc[date, code] if code in factors['pv_corr_10'].columns else np.nan
                if not pd.isna(pv10) and pv10 > 0.3:
                    score += 0.5

            # 共振加分2：量价背离度（量能蓄势）
            if 'vol_price_divergence' in factors and date in factors['vol_price_divergence'].index:
                vpd = factors['vol_price_divergence'].loc[date, code] if code in factors['vol_price_divergence'].columns else np.nan
                if not pd.isna(vpd) and vpd < cfg.vp_div_threshold:
                    score += 0.8

            # 共振加分3：中期趋势健康
            if 'pv_corr_20' in factors and date in factors['pv_corr_20'].index:
                pv20 = factors['pv_corr_20'].loc[date, code] if code in factors['pv_corr_20'].columns else np.nan
                if not pd.isna(pv20) and pv20 > cfg.pv_corr_20_min:
                    score += 0.3

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

            # 排除：量价严重背离
            if 'pv_corr_10' in factors and date in factors['pv_corr_10'].index:
                pv10 = factors['pv_corr_10'].loc[date, code] if code in factors['pv_corr_10'].columns else 0
                if not pd.isna(pv10) and pv10 < -0.5:
                    score = 0  # 排除

        if score > 0:
            scores[code] = score

    # 排除退市风险前10%
    if 'delist_risk' in factors and date in factors['delist_risk'].index:
        dr = factors['delist_risk'].loc[date]
        dr_threshold = dr.quantile(0.9)
        scores = {c: s for c, s in scores.items()
                  if c not in dr.index or dr[c] <= dr_threshold}

    if current_holdings:
        scores = {c: s for c, s in scores.items() if c not in current_holdings}

    candidates = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)
    return candidates[:cfg.max_holdings]


def run_v27_backtest(start_date='2022-01-01', end_date='2026-05-31'):
    print("=" * 60)
    print("v27_price_volume_resonance — 价量共振策略回测")
    print("=" * 60)
    t0 = time.time()

    print(f"\n[1/4] 加载数据 ({start_date} ~ {end_date})...")
    tpl, codes = load_panel_from_db(start_date, end_date, need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]
    print(f"  Panel: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只")

    print("\n[2/4] 计算因子...")
    t1 = time.time()
    factors = calc_v27_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel)
    print(f"  因子: {list(factors.keys())}")
    print(f"  因子计算耗时: {time.time()-t1:.1f}s")

    print("\n[3/4] 运行回测...")
    cfg = V27Config()
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
            # 跌停检查
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
        cands = select_stocks_v27(factors, date, close_panel, volume_panel, amount_panel, holdings, cfg)

        if cands and cash > cfg.initial_capital * 0.1 and len(holdings) < cfg.max_holdings:
            available = cash - cfg.initial_capital * 0.1
            n_buy = min(len(cands), cfg.max_daily_buy, cfg.max_holdings - len(holdings))
            per_stock = min(available / n_buy, cfg.initial_capital * cfg.max_position)
            bought = 0
            for c in cands[:cfg.max_daily_buy]:
                if bought >= n_buy: break
                bp = open_data[c] if c in open_data.index else price_data[c]
                if pd.isna(bp) or bp <= 0: continue
                # 涨停不买
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
    print(f"v27 回测结果 ({start_date} ~ {end_date})")
    print(f"{'='*60}")
    print(f"  年化收益: {annual_return*100:.2f}%")
    print(f"  夏普比率: {sharpe:.3f}")
    print(f"  最大回撤: {max_dd*100:.2f}%")
    print(f"  总交易: {total_buys} 买 / {total_sells} 卖")
    print(f"  选股率: {select_days}/{len(dates)-30} 天 ({select_days/max(1,len(dates)-30)*100:.1f}%)")
    print(f"  卖出原因: {sell_reasons}")

    # 对比 v22 基线
    print(f"\n  对比 v22 (mom_5>2% 同参数):")
    print(f"  v22: 252%/5.33/-11.8% (2022-2026)")
    print(f"  v27: {annual_return*100:.1f}%/{sharpe:.2f}/{-max_dd*100:.1f}%")

    return {
        'annual_return': annual_return, 'sharpe': sharpe, 'max_dd': max_dd,
        'total_buys': total_buys, 'total_sells': total_sells,
        'nav': nav_df, 'select_days': select_days,
    }


if __name__ == "__main__":
    run_v27_backtest()
