#!/usr/bin/env python3
"""v13 bonus 因子单因子扫描

逐个扫描每个 bonus 因子的 score 和阈值，其他参数固定为默认值。
基线：v13 无 bonus = 49.87%/2.484/-13.46%

用法：python scripts/v13_bonus_scan.py
"""
import sys, os, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

from scripts.v13_small_mid_short import (
    V13Config,
    load_small_cap_panel,
    calc_small_cap_factors,
    select_stocks,
)

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")

# 默认 bonus 因子定义
DEFAULT_BONUS = [
    {'factor': 'pvt',
     'calc': lambda c, v, a, h, l: (
         (c - c.rolling(20).mean()) / (c.rolling(20).std() + 1e-10)
         * (v / (v.rolling(5).mean() + 1e-10)) / (v / (v.rolling(20).mean() + 1e-10) + 1e-10)
     ),
     'condition': lambda v: v > 0.5,
     'score': 0.3},
    {'factor': 'vov_inv',
     'calc': lambda c, v, a, h, l: (
         c.pct_change().rolling(20).std().rolling(20).std()
     ),
     'condition': lambda v: v < 0.02,
     'score': 0.2},
    {'factor': 'mom_agree',
     'calc': lambda c, v, a, h, l: (
         (c.pct_change(5) > 0).astype(float) + (c.pct_change(10) > 0).astype(float)
     ),
     'condition': lambda v: v >= 2,
     'score': 0.2},
]


def run_v13_with_bonus(bonus_factors, close_panel, volume_panel, amount_panel,
                       high_panel, low_panel, open_panel):
    """用指定的 bonus 配置跑回测（显式传参，不修改类属性）"""
    factors = calc_small_cap_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel,
                                     bonus_factors=bonus_factors)

    cfg = V13Config()
    initial_capital = cfg.initial_capital
    cash = initial_capital
    holdings = {}
    nav_list = []
    trade_log = []
    dates = close_panel.index

    for i, date in enumerate(dates):
        if i < 20:
            nav_list.append(initial_capital)
            continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else initial_capital)
            continue

        price_data = close_panel.loc[date]
        open_data = open_panel.loc[date] if open_panel is not None else price_data

        for code in holdings:
            holdings[code]['hold_days'] += 1

        to_sell = []
        for code, h in list(holdings.items()):
            if code not in price_data.index:
                continue
            cp = price_data[code]
            if pd.isna(cp) or cp <= 0:
                continue
            pnl_pct = (cp - h['cost']) / h['cost']
            if pnl_pct <= cfg.stop_loss:
                to_sell.append((code, 'stop_loss', pnl_pct))
                continue
            if pnl_pct >= cfg.stop_profit:
                to_sell.append((code, 'stop_profit', pnl_pct))
                continue
            if h['hold_days'] >= cfg.hold_days_max:
                to_sell.append((code, 'timeout', pnl_pct))
                continue

        sold_codes = set()
        for code, reason, pnl_pct in to_sell:
            if code in price_data.index:
                sell_price = price_data[code]
                if pd.isna(sell_price) or sell_price <= 0:
                    continue
                if i > 0:
                    prev_close = close_panel.iloc[i-1].get(code) if code in close_panel.columns else None
                    if prev_close and not pd.isna(prev_close) and prev_close > 0:
                        if sell_price <= prev_close * 0.90 * 1.01:
                            holdings[code]['hold_days'] = max(0, holdings[code]['hold_days'] - 1)
                            continue
                h = holdings[code]
                sv = h['shares'] * sell_price * (1 - cfg.commission_rate - cfg.stamp_tax - cfg.slippage_rate)
                cash += sv
                trade_log.append({'date': str(date.date()), 'code': code, 'action': 'sell',
                                  'reason': reason, 'pnl_pct': round(pnl_pct * 100, 2)})
                sold_codes.add(code)
        for code in sold_codes:
            holdings.pop(code, None)

        candidates = select_stocks(factors, date, close_panel, volume_panel, amount_panel, holdings,
                                   bonus_factors=bonus_factors)

        if candidates and cash > initial_capital * 0.1 and len(holdings) < cfg.max_holdings:
            available_cash = cash - initial_capital * 0.1
            per_stock = min(available_cash / min(len(candidates), cfg.max_daily_buy),
                            initial_capital * cfg.max_position)
            for code in candidates[:cfg.max_daily_buy]:
                if code not in price_data.index:
                    continue
                bp = open_data[code] if code in open_data.index else price_data[code]
                if pd.isna(bp) or bp <= 0:
                    continue
                if i > 0:
                    prev_close = close_panel.iloc[i-1].get(code) if code in close_panel.columns else None
                    if prev_close and not pd.isna(prev_close) and prev_close > 0:
                        if bp >= prev_close * 1.10 * 0.99:
                            continue
                adj = bp * (1 + cfg.commission_rate + cfg.slippage_rate)
                shares = int(per_stock / adj / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * adj
                if cost > cash:
                    continue
                cash -= cost
                holdings[code] = {'shares': shares, 'cost': bp, 'hold_days': 0}
                trade_log.append({'date': str(date.date()), 'code': code, 'action': 'buy',
                                  'price': round(bp, 2), 'shares': shares})

        pv = cash
        for code, h in holdings.items():
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    pv += h['shares'] * p
        nav_list.append(pv)

    nav = pd.Series(nav_list, index=dates[:len(nav_list)])
    rets = nav.pct_change().dropna()
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    years = max((nav.index[-1] - nav.index[0]).days / 365, 0.01)
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = rets.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    peak = nav.cummax()
    max_dd = ((nav - peak) / peak).min()
    sells = [t for t in trade_log if t['action'] == 'sell']
    wins = [t for t in sells if t.get('pnl_pct', 0) > 0]
    win_rate = len(wins) / len(sells) * 100 if sells else 0

    return {'annual_return': ann_ret, 'sharpe': sharpe, 'max_drawdown': max_dd,
            'win_rate': win_rate, 'total_trades': len(trade_log)}


def main():
    print("=" * 70)
    print("v13 Bonus 因子单因子扫描")
    print("=" * 70)

    print("\n加载数据...")
    t0 = time.time()
    close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel = load_small_cap_panel()
    print(f"  Panel: {close_panel.shape}, 耗时 {time.time()-t0:.1f}s")

    # 基线
    print("\n跑基线（无 bonus）...")
    baseline = run_v13_with_bonus([], close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel)
    print(f"  基线: {baseline['annual_return']:.2%}/{baseline['sharpe']:.3f}/{baseline['max_drawdown']:.2%}/{baseline['win_rate']:.1f}%", flush=True)

    results = []

    # 扫描 B1: pvt
    print("\n扫描 B1 (pvt)...")
    for score in [0.1, 0.2, 0.3, 0.5, 0.8, 1.0]:
        for thresh in [0.0, 0.3, 0.5, 0.8, 1.0, 1.5]:
            bonus = [{'factor': 'pvt',
                      'calc': lambda c, v, a, h, l: (
                          (c - c.rolling(20).mean()) / (c.rolling(20).std() + 1e-10)
                          * (v / (v.rolling(5).mean() + 1e-10)) / (v / (v.rolling(20).mean() + 1e-10) + 1e-10)
                      ),
                      'condition': lambda v, t=thresh: v > t,
                      'score': score}]
            m = run_v13_with_bonus(bonus, close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel)
            results.append({'factor': 'pvt', 'score': score, 'thresh': thresh, **m})
            print(f"  pvt s={score} t={thresh}: {m['annual_return']:.2%}/{m['sharpe']:.3f}/{m['max_drawdown']:.2%}", flush=True)

    # 扫描 B2: vov_inv
    print("\n扫描 B2 (vov_inv)...")
    for score in [0.1, 0.2, 0.3, 0.5, 0.8, 1.0]:
        for thresh in [0.005, 0.01, 0.015, 0.02, 0.03, 0.05]:
            bonus = [{'factor': 'vov_inv',
                      'calc': lambda c, v, a, h, l: c.pct_change().rolling(20).std().rolling(20).std(),
                      'condition': lambda v, t=thresh: v < t,
                      'score': score}]
            m = run_v13_with_bonus(bonus, close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel)
            results.append({'factor': 'vov_inv', 'score': score, 'thresh': thresh, **m})
            print(f"  vov s={score} t={thresh}: {m['annual_return']:.2%}/{m['sharpe']:.3f}/{m['max_drawdown']:.2%}", flush=True)

    # 扫描 B3: mom_agree
    print("\n扫描 B3 (mom_agree)...")
    for score in [0.1, 0.2, 0.3, 0.5, 0.8, 1.0]:
        for thresh in [0.5, 1.0, 1.5, 2.0]:
            bonus = [{'factor': 'mom_agree',
                      'calc': lambda c, v, a, h, l: (
                          (c.pct_change(5) > 0).astype(float) + (c.pct_change(10) > 0).astype(float)
                      ),
                      'condition': lambda v, t=thresh: v >= t,
                      'score': score}]
            m = run_v13_with_bonus(bonus, close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel)
            results.append({'factor': 'mom_agree', 'score': score, 'thresh': thresh, **m})
            print(f"  mom s={score} t={thresh}: {m['annual_return']:.2%}/{m['sharpe']:.3f}/{m['max_drawdown']:.2%}", flush=True)

    # 汇总
    df = pd.DataFrame(results)
    df['vs_baseline'] = df['annual_return'] - baseline['annual_return']

    print(f"\n{'='*70}")
    print("各因子最优参数（vs 基线）")
    print(f"{'='*70}")

    for factor in ['pvt', 'vov_inv', 'mom_agree']:
        sub = df[df['factor'] == factor].sort_values('sharpe', ascending=False)
        best = sub.iloc[0]
        print(f"\n{factor}:")
        print(f"  最优: score={best['score']}, thresh={best['thresh']}")
        print(f"  结果: {best['annual_return']:.2%}/{best['sharpe']:.3f}/{best['max_drawdown']:.2%} (vs 基线 {best['vs_baseline']:+.2%})")
        print(f"  Top 3:")
        for _, r in sub.head(3).iterrows():
            print(f"    s={r['score']:.1f} t={r['thresh']:.3f}: {r['annual_return']:.2%}/{r['sharpe']:.3f}/{r['max_drawdown']:.2%}")

    # 保存
    out_path = os.path.join(DATA_DIR, 'backtest_results', 'v13_bonus_scan.csv')
    df.to_csv(out_path, index=False)
    print(f"\n结果已保存: {out_path}")


if __name__ == '__main__':
    main()
