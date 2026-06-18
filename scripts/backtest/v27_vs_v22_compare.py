#!/usr/bin/env python3
"""v22 vs v27 公平对比 - 同风控参数"""
import sys, os
import numpy as np
import pandas as pd

from core.db import load_panel_from_db

start, end = '2022-01-01', '2026-05-31'
tpl, _ = load_panel_from_db(start, end, need_open=True, need_hl=True)
close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]

eps = 1e-10
returns = close_panel.pct_change()

# 计算所有因子
factors = {}
factors['mom_5'] = close_panel.pct_change(5)
factors['mom_10'] = close_panel.pct_change(10)
factors['mom_20'] = close_panel.pct_change(20)

prev_close = close_panel.shift(1)
factors['gap_ratio'] = (open_panel - prev_close) / (prev_close + eps)

avg_amount = amount_panel.rolling(20).mean()
factors['illiquidity'] = 1.0 / (avg_amount / 1e8 + eps)

ma20 = close_panel.rolling(20).mean()
std20 = close_panel.rolling(20).std()
factors['boll_width_20'] = (4 * std20) / (ma20 + eps)

# v27 因子
vol_5 = volume_panel.rolling(5).mean()
vol_20 = volume_panel.rolling(20).mean()
vr = vol_5 / (vol_20 + eps)
daily_ret = close_panel.pct_change()

def _fast_rolling_corr_panel(ret_df, vol_df, window):
    ret_std = ret_df.rolling(window).std()
    vol_std = vol_df.rolling(window).std()
    ret_mean = ret_df.rolling(window).mean()
    vol_mean = vol_df.rolling(window).mean()
    xy_mean = (ret_df * vol_df).rolling(window).mean()
    cov = xy_mean - ret_mean * vol_mean
    return cov / (ret_std * vol_std + eps)

factors['pv_corr_10'] = _fast_rolling_corr_panel(daily_ret, vr, 10)
factors['pv_corr_20'] = _fast_rolling_corr_panel(daily_ret, vr, 20)

mom_rank = close_panel.pct_change(5).rank(axis=1, pct=True)
vol_rank = vr.rank(axis=1, pct=True)
factors['vol_price_divergence'] = mom_rank - vol_rank

# 退市风险
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

# 回测参数
INITIAL_CAPITAL = 200000
MAX_HOLDINGS = 8
MAX_DAILY_BUY = 6
MAX_POSITION = 0.20
HOLD_MAX = 5
STOP_LOSS = -0.015
STOP_PROFIT = 0.03
COMM_RATE = 0.0003
STAMP_TAX = 0.001
SLIPPAGE = 0.002
MOM_THRESHOLD = 0.02

def run_strategy(name, select_fn):
    cash = INITIAL_CAPITAL
    holdings = {}
    nav_list = []
    select_days = 0
    total_buys = 0
    total_sells = 0
    sell_reasons = {}

    dates = close_panel.index[close_panel.index >= pd.Timestamp(start)]

    for i, date in enumerate(dates):
        if i < 30:
            nav_list.append(cash); continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else cash); continue

        pd_ = close_panel.loc[date]
        od = open_panel.loc[date]

        for c in holdings:
            holdings[c]['hold_days'] = holdings[c].get('hold_days', 0) + 1

        # 风控
        to_sell = []
        for c, h in holdings.items():
            if c not in pd_.index: continue
            cp = pd_[c]
            if pd.isna(cp) or cp <= 0: continue
            pnl = (cp - h['cost']) / h['cost']
            hd = h.get('hold_days', 0)
            if pnl <= STOP_LOSS: to_sell.append((c, 'SL')); continue
            if pnl >= STOP_PROFIT: to_sell.append((c, 'TP')); continue
            if hd >= HOLD_MAX: to_sell.append((c, 'TO'))

        sold = set()
        for c, reason in to_sell:
            if c not in pd_.index: continue
            sp = pd_[c]
            if pd.isna(sp) or sp <= 0: continue
            if i > 0:
                pc = close_panel.iloc[i-1].get(c)
                if pc and not pd.isna(pc) and pc > 0 and sp <= pc * 0.90 * 1.01:
                    holdings[c]['hold_days'] = max(0, holdings[c].get('hold_days', 0) - 1)
                    continue
            h = holdings[c]
            sv = h['shares'] * sp * (1 - COMM_RATE - STAMP_TAX - SLIPPAGE)
            cash += sv; sold.add(c)
            total_sells += 1
            sell_reasons[reason] = sell_reasons.get(reason, 0) + 1
        for c in sold: holdings.pop(c, None)

        # 选股
        cands = select_fn(factors, date, pd_, od, holdings)

        if cands and cash > INITIAL_CAPITAL * 0.1 and len(holdings) < MAX_HOLDINGS:
            avail = cash - INITIAL_CAPITAL * 0.1
            nb = min(len(cands), MAX_DAILY_BUY, MAX_HOLDINGS - len(holdings))
            per = min(avail / nb, INITIAL_CAPITAL * MAX_POSITION)
            bought = 0
            for c in cands[:MAX_DAILY_BUY]:
                if bought >= nb: break
                bp = od[c] if c in od.index else pd_[c]
                if pd.isna(bp) or bp <= 0: continue
                if i > 0:
                    pc = close_panel.iloc[i-1].get(c)
                    if pc and not pd.isna(pc) and pc > 0 and bp >= pc * 1.10 * 0.99:
                        continue
                adj = bp * (1 + COMM_RATE + SLIPPAGE)
                sh = int(per / adj / 100) * 100
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
            if c in pd_.index:
                cp = pd_[c]
                if not pd.isna(cp) and cp > 0: nav += h['shares'] * cp
        nav_list.append(nav)

    nav_s = pd.Series(nav_list)
    ret = nav_s.pct_change().dropna()
    total = nav_s.iloc[-1] / INITIAL_CAPITAL - 1
    days = len(nav_list) - 30
    ar = (1 + total) ** (365 / max(days, 1)) - 1
    sh = ret.mean() / ret.std() * np.sqrt(252) if ret.std() > 0 else 0
    mdd = ((nav_s.cummax() - nav_s) / nav_s.cummax()).max()

    tp_pct = sell_reasons.get('TP', 0) / max(total_sells, 1) * 100
    sl_pct = sell_reasons.get('SL', 0) / max(total_sells, 1) * 100
    to_pct = sell_reasons.get('TO', 0) / max(total_sells, 1) * 100

    print(f"\n{'='*60}")
    print(f"{name} 回测结果 ({start} ~ {end})")
    print(f"{'='*60}")
    print(f"  年化收益: {ar*100:.2f}%")
    print(f"  夏普比率: {sh:.3f}")
    print(f"  最大回撤: {mdd*100:.2f}%")
    print(f"  总交易: {total_buys} 买 / {total_sells} 卖")
    print(f"  选股率: {select_days}/{len(dates)-30} 天 ({select_days/max(1,len(dates)-30)*100:.1f}%)")
    print(f"  卖出原因: TP={tp_pct:.1f}% SL={sl_pct:.1f}% TO={to_pct:.1f}%")
    return ar, sh, mdd, nav_s

# v22 选股逻辑
def v22_select(factors, date, pd_, od, holdings):
    if date not in factors['mom_5'].index:
        return []
    m5 = factors['mom_5'].loc[date].dropna()
    cands = []
    for code in m5.index:
        m = m5[code]
        if m > MOM_THRESHOLD:
            score = m * 100
            # gap
            if 'gap_ratio' in factors and date in factors['gap_ratio'].index:
                gr = factors['gap_ratio'].loc[date, code] if code in factors['gap_ratio'].columns else 0
                if not pd.isna(gr) and gr > 0.02: score += 0.5
            # illiq
            if 'illiquidity' in factors and date in factors['illiquidity'].index:
                il = factors['illiquidity'].loc[date, code] if code in factors['illiquidity'].columns else 0
                if not pd.isna(il) and il > 0: score += 0.8
            # boll
            if 'boll_width_20' in factors and date in factors['boll_width_20'].index:
                bw = factors['boll_width_20'].loc[date, code] if code in factors['boll_width_20'].columns else 0
                if not pd.isna(bw) and bw > 1.2: score += 0.3
            cands.append((code, score))
    cands.sort(key=lambda x: x[1], reverse=True)
    result = [c for c, s in cands[:MAX_HOLDINGS]]
    if holdings:
        result = [c for c in result if c not in holdings]
    return result

# v27 选股逻辑
def v27_select(factors, date, pd_, od, holdings):
    if date not in factors['mom_5'].index:
        return []
    m5 = factors['mom_5'].loc[date].dropna()
    scores = {}
    for code in m5.index:
        score = 0.0
        m = m5[code]
        if m > MOM_THRESHOLD:
            score += m * 100
            # pv_corr_10
            if 'pv_corr_10' in factors and date in factors['pv_corr_10'].index:
                pv10 = factors['pv_corr_10'].loc[date, code] if code in factors['pv_corr_10'].columns else np.nan
                if not pd.isna(pv10) and pv10 > 0.3: score += 0.5
            # vol_price_divergence
            if 'vol_price_divergence' in factors and date in factors['vol_price_divergence'].index:
                vpd = factors['vol_price_divergence'].loc[date, code] if code in factors['vol_price_divergence'].columns else np.nan
                if not pd.isna(vpd) and vpd < -0.3: score += 0.8
            # pv_corr_20
            if 'pv_corr_20' in factors and date in factors['pv_corr_20'].index:
                pv20 = factors['pv_corr_20'].loc[date, code] if code in factors['pv_corr_20'].columns else np.nan
                if not pd.isna(pv20) and pv20 > 0: score += 0.3
            # gap
            if 'gap_ratio' in factors and date in factors['gap_ratio'].index:
                gr = factors['gap_ratio'].loc[date, code] if code in factors['gap_ratio'].columns else 0
                if not pd.isna(gr) and gr > 0.02: score += 0.5
            # illiq
            if 'illiquidity' in factors and date in factors['illiquidity'].index:
                il = factors['illiquidity'].loc[date, code] if code in factors['illiquidity'].columns else 0
                if not pd.isna(il) and il > 0: score += 0.8
            # boll
            if 'boll_width_20' in factors and date in factors['boll_width_20'].index:
                bw = factors['boll_width_20'].loc[date, code] if code in factors['boll_width_20'].columns else 0
                if not pd.isna(bw) and bw > 1.2: score += 0.3
            # 排除量价严重背离
            if 'pv_corr_10' in factors and date in factors['pv_corr_10'].index:
                pv10 = factors['pv_corr_10'].loc[date, code] if code in factors['pv_corr_10'].columns else 0
                if not pd.isna(pv10) and pv10 < -0.5:
                    score = 0
        if score > 0:
            scores[code] = score

    # 排除退市风险
    if 'delist_risk' in factors and date in factors['delist_risk'].index:
        dr = factors['delist_risk'].loc[date]
        dr_threshold = dr.quantile(0.9)
        scores = {c: s for c, s in scores.items()
                  if c not in dr.index or dr[c] <= dr_threshold}

    result = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)[:MAX_HOLDINGS]
    if holdings:
        result = [c for c in result if c not in holdings]
    return result

if __name__ == "__main__":
    print("=" * 60)
    print("v22 vs v27 公平对比 (2022-01 ~ 2026-05)")
    print("=" * 60)

    v22_result = run_strategy("v22 (纯动量基线)", v22_select)
    v27_result = run_strategy("v27 (价量共振)", v27_select)

    print(f"\n{'='*60}")
    print("对比总结")
    print(f"{'='*60}")
    print(f"{'策略':12} | {'年化':>8} | {'夏普':>6} | {'回撤':>8}")
    print(f"{'v22':12} | {v22_result[0]*100:>7.1f}% | {v22_result[1]:>6.2f} | {v22_result[2]*100:>7.1f}%")
    print(f"{'v27':12} | {v27_result[0]*100:>7.1f}% | {v27_result[1]:>6.2f} | {v27_result[2]*100:>7.1f}%")
