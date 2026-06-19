#!/usr/bin/env python3
"""
v24_momentum_variants — 动量因子变体回测
==============================================

测试以下动量因子变体：
1. mom_vw: 成交量加权动量 = mom_5 × (1 + vol_change)
2. mom_adj: 调整后动量 = mom_5 - market_mom_5
3. mom_res: 残差动量 = 个股收益对市场收益回归残差
4. mom_combined: 综合动量 = 标准化(mom_vw) + 标准化(mom_adj) + 标准化(mom_res)

对比基线：v22 原始动量因子
"""

import sys, os
import time
import numpy as np
import pandas as pd

from core.db import load_panel_from_db

def calc_momentum_variants(close_panel, volume_panel, amount_panel):
    """
    计算动量因子变体
    
    Returns: dict of DataFrames
    """
    mom_5 = close_panel.pct_change(5)
    market_mom = mom_5.mean(axis=1)  # 截面均值 = 市场动量
    
    # 1. 成交量加权动量
    vol_change = volume_panel.pct_change(5)
    mom_vw = mom_5 * (1 + vol_change.fillna(0))
    
    # 2. 调整后动量（扣除市场）
    mom_adj = mom_5.sub(market_mom, axis=0)
    
    # 3. 残差动量（用20日滚动回归）
    # 简化：用个股mom对市场mom的截面回归残差
    mom_res = pd.DataFrame(0.0, index=close_panel.index, columns=close_panel.columns)
    for i in range(20, len(close_panel)):
        date = close_panel.index[i]
        if date not in mom_5.index:
            continue
        m = mom_5.loc[date].dropna()
        if len(m) < 50:
            continue
        market_val = market_mom.get(date, 0)
        # 残差 = 个股动量 - 市场动量（简化版，不回归）
        # 更精确：用过去20天截面数据的标准差标准化
        std = m.std()
        if std > 0:
            # 标准化后减去市场标准化值
            m_std = (m - m.mean()) / std
            mk_std = (market_val - m.mean()) / std
            resid = m_std - mk_std
            for code in resid.index:
                if code in mom_res.columns:
                    mom_res.loc[date, code] = resid[code]
    
    # 4. 综合动量（标准化求和）
    # 向量化标准化
    def zscore(df):
        return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1) + 1e-10, axis=0)
    
    z_vw = zscore(mom_vw)
    z_adj = zscore(mom_adj)
    z_res = zscore(mom_res.replace(0, np.nan))
    z_raw = zscore(mom_5)
    
    mom_combined = (z_raw.fillna(0) + z_vw.fillna(0) + z_adj.fillna(0) + z_res.fillna(0)) / 4
    
    return {
        'mom_5': mom_5,
        'mom_vw': mom_vw,
        'mom_adj': mom_adj,
        'mom_res': mom_res,
        'mom_combined': mom_combined,
    }

def run_backtest(close_panel, volume_panel, amount_panel, open_panel,
                  high_panel, low_panel, factors, cfg, label=""):
    """通用回测函数，接受因子 dict"""
    eps = 1e-10
    mom_key = cfg.get('mom_key', 'mom_5')
    mom_panel = factors[mom_key]
    threshold = cfg.get('mom_threshold', 0.02)
    
    gap_ratio = (open_panel - close_panel.shift(1)) / (close_panel.shift(1) + eps)
    avg_amount = amount_panel.rolling(20).mean()
    illiq = 1.0 / (avg_amount / 1e4 + eps)
    ma20 = close_panel.rolling(20).mean()
    std20 = close_panel.rolling(20).std()
    boll_w = (4 * std20) / (ma20 + eps)

    cash = cfg['initial_capital']
    holdings = {}
    nav_list = []
    sell_reasons = {'SL': 0, 'TP': 0, 'TO': 0}

    for i, date in enumerate(close_panel.index):
        if i < 30:
            nav_list.append(cash); continue

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
            if pnl <= cfg['stop_loss']: to_sell.append((c, 'SL')); continue
            if pnl >= cfg['stop_profit']: to_sell.append((c, 'TP')); continue
            if h.get('hold_days', 0) >= cfg['hold_days_max']: to_sell.append((c, 'TO'))

        for c, reason in to_sell:
            if c not in pd_.index: continue
            sp = pd_[c]
            if pd.isna(sp) or sp <= 0: continue
            h = holdings[c]
            sv = h['shares'] * sp * (1 - cfg['commission_rate'] - cfg['stamp_tax'] - cfg['slippage_rate'])
            cash += sv
            sell_reasons[reason] = sell_reasons.get(reason, 0) + 1
        for c, _ in to_sell: holdings.pop(c, None)

        if date not in mom_panel.index:
            nav_list.append(nav_list[-1] if nav_list else cash); continue

        m = mom_panel.loc[date].dropna()
        scores = {}
        for code in m.index:
            score = 0.0
            val = m[code]
            if pd.isna(val): continue
            # 综合动量可能为负，用绝对值排序
            if mom_key == 'mom_combined':
                if val > 0:
                    score = val
                else:
                    continue
            else:
                if val <= threshold:
                    continue
                score = val * 100
            
            # 辅助因子加分
            if code in gap_ratio.columns and date in gap_ratio.index:
                gr = gap_ratio.loc[date, code]
                if not pd.isna(gr) and gr > 0.02: score += 0.5
            if code in illiq.columns and date in illiq.index:
                il = illiq.loc[date, code]
                if not pd.isna(il) and il > 0: score += 0.3
            
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

        nav = cash
        for c, h in holdings.items():
            if c in pd_.index:
                cp = pd_[c]
                if not pd.isna(cp) and cp > 0: nav += h['shares'] * cp
        nav_list.append(nav)

    nav_s = pd.Series(nav_list)
    daily_ret = nav_s.pct_change().dropna()
    total = nav_s.iloc[-1] / nav_s.iloc[0] - 1
    annual = (1 + total) ** (365 / max(len(nav_list) - 30, 1)) - 1
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    max_dd = ((nav_s.cummax() - nav_s) / nav_s.cummax()).max()
    total_sells = sum(sell_reasons.values())
    
    result = {
        'label': label, 'annual': annual, 'sharpe': sharpe,
        'max_dd': max_dd, 'total': total,
        'SL': sell_reasons.get('SL', 0),
        'TP': sell_reasons.get('TP', 0),
        'TO': sell_reasons.get('TO', 0),
        'total_sells': total_sells,
        'win_rate': sell_reasons.get('TP', 0) / max(total_sells, 1),
    }
    
    return result, nav_s

def main():
    import argparse
    parser = argparse.ArgumentParser(description="动量因子变体回测")
    parser.add_argument("--start", type=str, default="2022-01-01")
    parser.add_argument("--end", type=str, default="2026-05-31")
    parser.add_argument("--capital", type=int, default=100000)
    args = parser.parse_args()

    t_start = time.time()
    
    print("=" * 60)
    print("动量因子变体回测")
    print("=" * 60)

    tpl, _ = load_panel_from_db(args.start, args.end, need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = tpl[0], tpl[1], tpl[2]
    open_panel, high_panel, low_panel = tpl[3], tpl[4], tpl[5]

    print("数据: %d 天 x %d 只 | %s ~ %s" % (
        close_panel.shape[0], close_panel.shape[1],
        close_panel.index[0].strftime('%Y-%m-%d'),
        close_panel.index[-1].strftime('%Y-%m-%d')))

    # 计算动量变体
    print("\n计算动量因子变体...")
    t0 = time.time()
    factors = calc_momentum_variants(close_panel, volume_panel, amount_panel)
    print("  耗时: %.1fs | 因子: %s" % (time.time() - t0, list(factors.keys())))

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

    # 回测所有变体
    variants = [
        ('v22-raw', 'mom_5', '原始动量 (基线)'),
        ('mom_vw', 'mom_vw', '成交量加权动量'),
        ('mom_adj', 'mom_adj', '调整后动量(扣市场)'),
        ('mom_res', 'mom_res', '残差动量'),
        ('mom_combined', 'mom_combined', '综合动量'), ]

    results = []
    print("\n" + "=" * 60)
    print("回测结果")
    print("=" * 60)
    print("%-20s %10s %8s %8s %8s %8s" % ('策略', '年化%', '夏普', '回撤%', '胜率', 'TP/SL'))
    print("-" * 72)

    for key, mom_key, label in variants:
        run_cfg = dict(cfg)
        run_cfg['mom_key'] = mom_key
        if mom_key == 'mom_combined':
            run_cfg['mom_threshold'] = 0  # 综合动量用 0 作为阈值
        result, nav_s = run_backtest(
            close_panel, volume_panel, amount_panel, open_panel,
            high_panel, low_panel, factors, run_cfg, label)
        results.append(result)
        
        print("%-20s %10.2f %8.3f %8.2f %8.1f %d/%d" % (
            label,
            result['annual'] * 100,
            result['sharpe'],
            result['max_dd'] * 100,
            result['win_rate'] * 100,
            result['TP'],
            result['SL']))

    print("-" * 72)
    elapsed = time.time() - t_start
    print("总耗时: %.1fs" % elapsed)
    print("=" * 60)

    return results

if __name__ == "__main__":
    main()
