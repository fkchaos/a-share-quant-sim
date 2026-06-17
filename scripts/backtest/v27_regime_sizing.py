#!/usr/bin/env python3
"""
v27_regime_position_sizing.py — 基于大盘走势的动态仓位控制回测
=============================================================

在 v27 策略基础上，加入市场状态识别 + 动态仓位乘数。

市场状态识别（基于沪深300指数）：
- 牛市：价格 > MA20 且 MA20 上升（斜率 > 0）
- 熊市：价格 < MA20 且 MA20 下降（斜率 < 0）
- 震荡：其他

仓位乘数：
- 牛市：1.0（满仓）
- 震荡：0.6（降仓 40%）
- 熊市：0.3（降仓 70%）

对比基线：v27 原始策略（无动态仓位）
"""

import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from core.db import load_panel_from_db


# ── 市场状态识别 ──────────────────────────────────────────────────

def detect_regime(close_panel, ma_lookback=20, slope_lookback=10):
    """
    基于全市场平均价格（代理沪深300）识别市场状态
    
    Returns: Series of regime labels ('bull'/'bear'/'range')
    """
    # 用截面平均 close 代理市场指数
    market = close_panel.mean(axis=1)
    
    # MA20
    ma20 = market.rolling(ma_lookback).mean()
    
    # MA20 斜率（归一化）
    ma_slope = ma20.pct_change(slope_lookback)
    
    # 市场状态
    regimes = pd.Series('range', index=close_panel.index)
    
    # 牛市：价格 > MA20 且 MA20 上升
    bull_mask = (market > ma20) & (ma_slope > 0.005)
    regimes[bull_mask] = 'bull'
    
    # 熊市：价格 < MA20 且 MA20 下降
    bear_mask = (market < ma20) & (ma_slope < -0.005)
    regimes[bear_mask] = 'bear'
    
    return regimes, ma20, ma_slope


def get_regime_multiplier(regime, multipliers=None):
    """根据市场状态返回仓位乘数"""
    if multipliers is None:
        multipliers = {'bull': 1.0, 'range': 0.6, 'bear': 0.3}
    return multipliers.get(regime, 0.6)


# ── 回测引擎 ──────────────────────────────────────────────────────

def run_backtest_v27_regime(close_panel, volume_panel, amount_panel,
                            high_panel, low_panel, open_panel,
                            params, regime_multipliers=None, label=""):
    """
    v27 回测引擎 + 动态仓位乘数
    
    参数:
        regime_multipliers: dict, 如 {'bull': 1.0, 'range': 0.6, 'bear': 0.3}
    """
    if regime_multipliers is None:
        regime_multipliers = {'bull': 1.0, 'range': 0.6, 'bear': 0.3}
    
    # 计算市场状态
    regimes, ma20, ma_slope = detect_regime(close_panel)
    
    # 计算因子
    mom_5 = close_panel.pct_change(5)
    prev_close = close_panel.shift(1)
    gap_ratio = (open_panel - prev_close) / (prev_close + 1e-10)
    avg_amount = amount_panel.rolling(20).mean()
    illiq = 1.0 / (avg_amount / 1e4 + 1e-10)
    ma20_stock = close_panel.rolling(20).mean()
    std20 = close_panel.rolling(20).std()
    boll_w = (4 * std20) / (ma20_stock + 1e-10)
    
    cash = params['initial_capital']
    holdings = {}  # code: {shares, cost, hold_days}
    nav_list = []
    trade_log = []
    regime_log = []
    
    for i, date in enumerate(close_panel.index):
        if i < 30:
            nav_list.append(cash)
            regime_log.append('range')
            continue
        
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else cash)
            regime_log.append('range')
            continue
        
        pd_ = close_panel.loc[date]
        od = open_panel.loc[date]
        
        # 更新持有天数
        for c in holdings:
            holdings[c]['hold_days'] = holdings[c].get('hold_days', 0) + 1
        
        # ── 卖出逻辑（止损/止盈/超时）──
        to_sell = []
        for c, h in holdings.items():
            if c not in pd_.index:
                continue
            cp = pd_[c]
            if pd.isna(cp) or cp <= 0:
                continue
            pnl = (cp - h['cost']) / h['cost']
            
            if pnl <= params['stop_loss']:
                to_sell.append((c, 'SL', pnl))
                continue
            if pnl >= params['stop_profit']:
                to_sell.append((c, 'TP', pnl))
                continue
            if h.get('hold_days', 0) >= params['hold_days_max']:
                to_sell.append((c, 'TO', pnl))
                continue
        
        for c, reason, pnl in to_sell:
            if c not in pd_.index:
                continue
            sp = pd_[c]
            if pd.isna(sp) or sp <= 0:
                continue
            h = holdings[c]
            proceeds = h['shares'] * sp * (1 - params['commission_rate'] - params['stamp_tax'] - params['slippage_rate'])
            cash += proceeds
            trade_log.append({
                'date': date, 'code': c, 'action': 'sell',
                'shares': h['shares'], 'price': sp, 'reason': reason, 'pnl': pnl
            })
            holdings.pop(c, None)
        
        # ── 买入逻辑（带动态仓位乘数）──
        regime = regimes.loc[date] if date in regimes.index else 'range'
        regime_mult = get_regime_multiplier(regime, regime_multipliers)
        regime_log.append(regime)
        
        if date in mom_5.index:
            m5 = mom_5.loc[date].dropna()
            scores = {}
            for code in m5.index:
                score = 0.0
                m = m5[code]
                if m > params.get('mom_threshold', 0.02):
                    score += m * 100
                    if date in gap_ratio.index and code in gap_ratio.columns:
                        gr = gap_ratio.loc[date, code]
                        if not pd.isna(gr) and gr > 0.02:
                            score += 0.5
                    if date in illiq.index and code in illiq.columns:
                        il = illiq.loc[date, code]
                        if not pd.isna(il) and il > 0:
                            score += 0.8
                    if date in boll_w.index and code in boll_w.columns:
                        bw = boll_w.loc[date, code]
                        if not pd.isna(bw) and bw > 1.2:
                            score += 0.3
                if score > 0:
                    scores[code] = score
            
            # 排除已持仓
            if holdings:
                scores = {c: s for c, s in scores.items() if c not in holdings}
            
            cands = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)
            
            # 动态仓位乘数影响买入数量和最大持仓
            effective_max_holdings = max(1, int(params['max_holdings'] * regime_mult))
            effective_max_buy = max(1, int(params['max_daily_buy'] * regime_mult))
            
            if cands and cash > params['initial_capital'] * 0.03 and len(holdings) < effective_max_holdings:
                available = cash - params['initial_capital'] * 0.03
                nb = min(len(cands), effective_max_buy, effective_max_holdings - len(holdings))
                if nb > 0:
                    per_stock = min(available / nb, params['initial_capital'] * params['max_position'] * regime_mult)
                    bought = 0
                    for c in cands[:effective_max_buy]:
                        if len(holdings) >= effective_max_holdings or bought >= nb:
                            break
                        bp = od[c] if c in od.index else pd_[c]
                        if pd.isna(bp) or bp <= 0:
                            continue
                        adj = bp * (1 + params['commission_rate'] + params['slippage_rate'])
                        shares = int(per_stock / adj / 100) * 100
                        if shares <= 0:
                            continue
                        cost = shares * adj
                        if cost > cash:
                            continue
                        cash -= cost
                        holdings[c] = {'shares': shares, 'cost': bp, 'hold_days': 0}
                        trade_log.append({
                            'date': date, 'code': c, 'action': 'buy',
                            'shares': shares, 'price': bp, 'regime': regime
                        })
                        bought += 1
        
        # 计算 NAV
        nav = cash
        for c, h in holdings.items():
            if c in pd_.index:
                cp = pd_[c]
                if not pd.isna(cp) and cp > 0:
                    nav += h['shares'] * cp
        nav_list.append(nav)
    
    nav_series = pd.Series(nav_list, index=close_panel.index)
    regime_series = pd.Series(regime_log, index=close_panel.index)
    
    return nav_series, trade_log, regime_series


# ── 指标计算 ──────────────────────────────────────────────────────

def calc_metrics(nav_series, trade_log):
    if nav_series is None or len(nav_series) < 2:
        return {}
    
    total_return = nav_series.iloc[-1] / nav_series.iloc[0] - 1
    n_days = len(nav_series)
    ann_return = (1 + total_return) ** (252 / max(n_days, 1)) - 1
    
    daily_returns = nav_series.pct_change().dropna()
    if len(daily_returns) < 2 or daily_returns.std() < 1e-10:
        return {'ann_return': ann_return, 'total_return': total_return, 'sharpe': 0, 'max_dd': 0, 'sortino': 0, 'total_trades': 0}
    
    sharpe = daily_returns.mean() / daily_returns.std() * np.sqrt(252)
    cummax = nav_series.cummax()
    drawdown = (nav_series - cummax) / cummax
    max_dd = drawdown.min()
    
    neg_returns = daily_returns[daily_returns < 0]
    sortino = daily_returns.mean() / neg_returns.std() * np.sqrt(252) if len(neg_returns) > 0 and neg_returns.std() > 1e-10 else 0
    
    buy_trades = [t for t in trade_log if t['action'] == 'buy']
    sell_trades = [t for t in trade_log if t['action'] == 'sell']
    tp_trades = [t for t in sell_trades if t.get('reason') == 'TP']
    sl_trades = [t for t in sell_trades if t.get('reason') == 'SL']
    
    return {
        'ann_return': ann_return,
        'total_return': total_return,
        'sharpe': sharpe,
        'max_dd': max_dd,
        'sortino': sortino,
        'total_trades': len(buy_trades),
        'sell_trades': len(sell_trades),
        'tp_trades': len(tp_trades),
        'sl_trades': len(sl_trades),
        'win_rate': len(tp_trades) / max(len(sell_trades), 1),
        'final_nav': nav_series.iloc[-1],
    }


def calc_regime_metrics(nav_series, regime_series):
    """按市场状态分段统计"""
    results = {}
    for regime in ['bull', 'range', 'bear']:
        mask = (regime_series == regime)
        if mask.sum() == 0:
            continue
        regime_nav = nav_series[mask]
        if len(regime_nav) < 2:
            continue
        total_ret = regime_nav.iloc[-1] / regime_nav.iloc[0] - 1
        n_days = len(regime_nav)
        ann_ret = (1 + total_ret) ** (252 / max(n_days, 1)) - 1
        daily_ret = regime_nav.pct_change().dropna()
        sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
        max_dd = ((regime_nav.cummax() - regime_nav) / regime_nav.cummax()).max()
        
        results[regime] = {
            'days': n_days,
            'total_ret': total_ret,
            'ann_return': ann_ret,
            'sharpe': sharpe,
            'max_dd': max_dd,
            'pct_time': mask.sum() / len(regime_series) * 100,
        }
    return results


# ── 主函数 ────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="v27 动态仓位控制回测")
    parser.add_argument("--start", type=str, default="2022-01-01")
    parser.add_argument("--end", type=str, default="2026-05-31")
    parser.add_argument("--capital", type=int, default=100000)
    args = parser.parse_args()
    
    print("=" * 70)
    print("v27 动态仓位控制回测")
    print("=" * 70)
    
    # 加载数据
    print("\n📥 加载数据...")
    t0 = time.time()
    panels, codes = load_panel_from_db(args.start, args.end, need_open=True, need_hl=True)
    close_panel, volume_panel, amount_panel = panels[0], panels[1], panels[2]
    open_panel, high_panel, low_panel = panels[3], panels[4], panels[5]
    print(f"  {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只 ({time.time()-t0:.1f}s)")
    
    # 策略参数
    params = {
        'initial_capital': args.capital,
        'max_holdings': 8,
        'max_daily_buy': 4,
        'max_position': 0.20,
        'hold_days_max': 5,
        'stop_loss': -0.02,
        'stop_profit': 0.05,
        'commission_rate': 0.0003,
        'stamp_tax': 0.001,
        'slippage_rate': 0.002,
        'mom_threshold': 0.02,
    }
    
    # ── 基线：无动态仓位 ──
    print("\n" + "=" * 70)
    print("基线：v27 原始策略（无动态仓位）")
    print("=" * 70)
    t1 = time.time()
    nav_base, trades_base, regime_base = run_backtest_v27_regime(
        close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel,
        params, regime_multipliers={'bull': 1.0, 'range': 1.0, 'bear': 1.0},
        label="baseline"
    )
    metrics_base = calc_metrics(nav_base, trades_base)
    regime_base_stats = calc_regime_metrics(nav_base, regime_base)
    
    print(f"  回测耗时: {time.time()-t1:.1f}s")
    print(f"  年化收益: {metrics_base['ann_return']:.1%}")
    print(f"  夏普比率: {metrics_base['sharpe']:.2f}")
    print(f"  最大回撤: {metrics_base['max_dd']:.1%}")
    print(f"  总交易数: {metrics_base['total_trades']}")
    print(f"  胜率: {metrics_base['win_rate']:.1%}")
    
    print("\n  分段统计（无动态仓位）：")
    for regime, stats in regime_base_stats.items():
        label = {'bull': '牛市', 'range': '震荡', 'bear': '熊市'}[regime]
        print(f"    {label}: {stats['days']}天 ({stats['pct_time']:.0f}%) | "
              f"年化{stats['ann_return']:.1%} | 夏普{stats['sharpe']:.2f} | 回撤{stats['max_dd']:.1%}")
    
    # ── 方案 A：保守（牛市1.0/震荡0.6/熊市0.3）──
    print("\n" + "=" * 70)
    print("方案A：保守（牛市1.0 / 震荡0.6 / 熊市0.3）")
    print("=" * 70)
    t2 = time.time()
    nav_a, trades_a, regime_a = run_backtest_v27_regime(
        close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel,
        params, regime_multipliers={'bull': 1.0, 'range': 0.6, 'bear': 0.3},
        label="conservative"
    )
    metrics_a = calc_metrics(nav_a, trades_a)
    regime_a_stats = calc_regime_metrics(nav_a, regime_a)
    
    print(f"  回测耗时: {time.time()-t2:.1f}s")
    print(f"  年化收益: {metrics_a['ann_return']:.1%}")
    print(f"  夏普比率: {metrics_a['sharpe']:.2f}")
    print(f"  最大回撤: {metrics_a['max_dd']:.1%}")
    print(f"  总交易数: {metrics_a['total_trades']}")
    print(f"  胜率: {metrics_a['win_rate']:.1%}")
    
    print("\n  分段统计：")
    for regime, stats in regime_a_stats.items():
        label = {'bull': '牛市', 'range': '震荡', 'bear': '熊市'}[regime]
        print(f"    {label}: {stats['days']}天 ({stats['pct_time']:.0f}%) | "
              f"年化{stats['ann_return']:.1%} | 夏普{stats['sharpe']:.2f} | 回撤{stats['max_dd']:.1%}")
    
    # ── 方案 B：激进（牛市1.2/震荡0.5/熊市0.2）──
    print("\n" + "=" * 70)
    print("方案B：激进（牛市1.2 / 震荡0.5 / 熊市0.2）")
    print("=" * 70)
    t3 = time.time()
    nav_b, trades_b, regime_b = run_backtest_v27_regime(
        close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel,
        params, regime_multipliers={'bull': 1.2, 'range': 0.5, 'bear': 0.2},
        label="aggressive"
    )
    metrics_b = calc_metrics(nav_b, trades_b)
    regime_b_stats = calc_regime_metrics(nav_b, regime_b)
    
    print(f"  回测耗时: {time.time()-t3:.1f}s")
    print(f"  年化收益: {metrics_b['ann_return']:.1%}")
    print(f"  夏普比率: {metrics_b['sharpe']:.2f}")
    print(f"  最大回撤: {metrics_b['max_dd']:.1%}")
    print(f"  总交易数: {metrics_b['total_trades']}")
    print(f"  胜率: {metrics_b['win_rate']:.1%}")
    
    print("\n  分段统计：")
    for regime, stats in regime_b_stats.items():
        label = {'bull': '牛市', 'range': '震荡', 'bear': '熊市'}[regime]
        print(f"    {label}: {stats['days']}天 ({stats['pct_time']:.0f}%) | "
              f"年化{stats['ann_return']:.1%} | 夏普{stats['sharpe']:.2f} | 回撤{stats['max_dd']:.1%}")
    
    # ── 方案 C：只降熊市（牛市1.0/震荡0.8/熊市0.3）──
    print("\n" + "=" * 70)
    print("方案C：只降熊市（牛市1.0 / 震荡0.8 / 熊市0.3）")
    print("=" * 70)
    t4 = time.time()
    nav_c, trades_c, regime_c = run_backtest_v27_regime(
        close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel,
        params, regime_multipliers={'bull': 1.0, 'range': 0.8, 'bear': 0.3},
        label="bear_only"
    )
    metrics_c = calc_metrics(nav_c, trades_c)
    regime_c_stats = calc_regime_metrics(nav_c, regime_c)
    
    print(f"  回测耗时: {time.time()-t4:.1f}s")
    print(f"  年化收益: {metrics_c['ann_return']:.1%}")
    print(f"  夏普比率: {metrics_c['sharpe']:.2f}")
    print(f"  最大回撤: {metrics_c['max_dd']:.1%}")
    print(f"  总交易数: {metrics_c['total_trades']}")
    print(f"  胜率: {metrics_c['win_rate']:.1%}")
    
    print("\n  分段统计：")
    for regime, stats in regime_c_stats.items():
        label = {'bull': '牛市', 'range': '震荡', 'bear': '熊市'}[regime]
        print(f"    {label}: {stats['days']}天 ({stats['pct_time']:.0f}%) | "
              f"年化{stats['ann_return']:.1%} | 夏普{stats['sharpe']:.2f} | 回撤{stats['max_dd']:.1%}")
    
    # ── 汇总对比 ──
    print("\n" + "=" * 70)
    print("汇总对比")
    print("=" * 70)
    print(f"{'方案':25s} | {'年化':>8s} | {'夏普':>6s} | {'回撤':>8s} | {'交易':>6s} | {'胜率':>6s}")
    print(f"{'-'*25}-+-{'-'*8}-+-{'-'*6}-+-{'-'*8}-+-{'-'*6}-+-{'-'*6}")
    
    all_metrics = [
        ("基线（无动态仓位）", metrics_base),
        ("方案A 保守", metrics_a),
        ("方案B 激进", metrics_b),
        ("方案C 只降熊市", metrics_c),
    ]
    
    for name, m in all_metrics:
        print(f"{name:25s} | {m['ann_return']:8.1%} | {m['sharpe']:6.2f} | {m['max_dd']:8.1%} | {m['total_trades']:6d} | {m['win_rate']:6.1%}")
    
    # 保存结果
    DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
    REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")
    os.makedirs(REPORT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = os.path.join(REPORT_DIR, f"v27_regime_sizing_{ts}.json")
    
    results = {
        'params': params,
        'regime_multipliers': {
            'baseline': {'bull': 1.0, 'range': 1.0, 'bear': 1.0},
            'conservative': {'bull': 1.0, 'range': 0.6, 'bear': 0.3},
            'aggressive': {'bull': 1.2, 'range': 0.5, 'bear': 0.2},
            'bear_only': {'bull': 1.0, 'range': 0.8, 'bear': 0.3},
        },
        'metrics': {
            'baseline': metrics_base,
            'conservative': metrics_a,
            'aggressive': metrics_b,
            'bear_only': metrics_c,
        },
        'regime_stats': {
            'baseline': regime_base_stats,
            'conservative': regime_a_stats,
            'aggressive': regime_b_stats,
            'bear_only': regime_c_stats,
        }
    }
    
    with open(out_file, "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n✅ 结果已保存 → {out_file}")


if __name__ == "__main__":
    main()
