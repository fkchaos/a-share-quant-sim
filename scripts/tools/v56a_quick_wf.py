#!/usr/bin/env python3
"""
Quick single-factor WF test for mom_5 / reversal_score / quality_score
Lightweight: no full framework, just test if single factor generates positive WF
"""
import sys, os, time
sys.path.insert(0, '/root/a-share-quant-sim')
import numpy as np
import pandas as pd
from core.db import load_panel_from_db
from core.account import PortfolioState, buy, sell, portfolio_value
from core.config import TradingCosts
from scripts.strategies.v56a_multialpha import calc_factors, DEFAULT_PARAMS

print("[1] Loading panels...")
t = time.time()
tpl, _ = load_panel_from_db('2021-06-01', '2026-06-01', need_open=True, need_hl=True, pool='zz1800')
cp, vp, ap, op, hp, lp = tpl[0], tpl[1], tpl[2], tpl[3], tpl[4], tpl[5]
# exclude STAR
cols = [c for c in cp.columns if not c.startswith(('688','689'))]
cp, vp, ap = cp[cols], vp[cols], ap[cols]
op, hp, lp = op[cols], hp[cols], lp[cols]
print(f"  {cp.shape[0]}d x {cp.shape[1]} stocks ({time.time()-t:.1f}s)")

print("[2] Computing factors...")
t = time.time()
factors = calc_factors(cp, vp, ap, hp, lp, op, DEFAULT_PARAMS)
print(f"  {len(factors)} factors ({time.time()-t:.1f}s)")

# Build forward returns
fwd_5d = cp.pct_change(5).shift(-5)

# Simple WF function
def run_single_factor_wf(factor_name, reverse=False, sl=-0.015, tp=0.03, hold=5):
    """Lightweight single-factor WF"""
    train, test, step = 252, 126, 63
    total = cp.shape[0]
    nfolds = (total - train) // step
    results = []
    
    for fold in range(nfolds):
        sidx = fold * step
        tend = min(sidx + train + test, total)
        test_close = cp.iloc[sidx+train:tend]
        if len(test_close) < 30:
            continue
        
        state = PortfolioState(cash=200000, initial_capital=200000)
        nav = []
        
        for i in range(len(test_close)):
            d = test_close.index[i]
            price_row = test_close.iloc[i]
            
            # Risk management
            to_sell = []
            for code in list(state.holdings.keys()):
                if code not in price_row.index:
                    continue
                price = price_row[code]
                if pd.isna(price) or price <= 0:
                    continue
                h = state.holdings[code]
                cost = h.get('cost_price', 0)
                pnl = (price - cost) / cost if cost > 0 else 0
                entry = pd.Timestamp(h.get('entry_date', str(d)))
                hd = (pd.Timestamp(d) - entry).days
                
                if pnl < sl:
                    to_sell.append((code, 'sl'))
                elif pnl >= tp:
                    to_sell.append((code, 'tp'))
                elif hd > hold:
                    to_sell.append((code, 'to'))
            
            for code, reason in to_sell:
                if code in state.holdings and code in price_row.index:
                    state = sell(state, code, price_row[code], d, reason=reason)
            
            # Select
            if d in factors[factor_name].index and len(state.holdings) < 8:
                fseries = factors[factor_name].loc[d].dropna()
                if reverse:
                    fseries = fseries.sort_values(ascending=True)
                else:
                    fseries = fseries.sort_values(ascending=False)
                
                to_buy = [c for c in fseries.index[:3] if c not in state.holdings]
                if to_buy and state.cash > 1000:
                    n = min(len(to_buy), 3, 8 - len(state.holdings))
                    per = min((state.cash - 1000) / max(n, 1), 200000 * 0.20)
                    for code in to_buy[:n]:
                        if code not in price_row.index:
                            continue
                        bp = price_row[code]
                        if pd.isna(bp) or bp <= 0:
                            continue
                        shares = int(per / (bp * 1.001) / 100) * 100
                        if shares > 0:
                            state = buy(state, code, bp, d, shares=shares)
            
            nav.append(portfolio_value(state, d, price_row))
        
        if nav:
            ns = pd.Series(nav)
            ret = ns.iloc[-1] / ns.iloc[0] - 1
            dd = ((ns.cummax() - ns) / ns.cummax()).max()
            dr = ns.pct_change().dropna()
            sharpe = dr.mean() / dr.std() * np.sqrt(252) if dr.std() > 0 else 0
            results.append({'fold': fold, 'ret': ret, 'dd': dd, 'sharpe': sharpe, 'days': len(nav)})
    
    if not results:
        return None
    df = pd.DataFrame(results)
    return {
        'ret': df['ret'].mean() * 100,
        'sharpe': df['sharpe'].mean(),
        'dd': df['dd'].mean() * 100,
        'pos_folds': (df['ret'] > 0).sum(),
        'total': len(df),
    }


# Run configs
configs = [
    # (factor, reverse, sl, tp, hold)
    ('mom_5', False, -0.05, 0.10, 5),
    ('mom_5', False, -0.015, 0.03, 5),
    ('quality_score', False, -0.05, 0.10, 5),
    ('quality_score', False, -0.015, 0.03, 5),
    ('reversal_score', True, -0.05, 0.10, 5),
    ('reversal_score', True, -0.015, 0.03, 5),
    ('reversal_score', False, -0.05, 0.10, 5),
    ('reversal_score', False, -0.015, 0.03, 5),
]

results = []
for i, (fname, reverse, sl, tp, hold) in enumerate(configs):
    direction = "ASC" if reverse else "DESC"
    print(f"\n[{i+1}/8] {fname} ({direction}, SL={sl}, TP={tp}, HOLD={hold})...", flush=True)
    t1 = time.time()
    r = run_single_factor_wf(fname, reverse=reverse, sl=sl, tp=tp, hold=hold)
    if r:
        r['factor'] = fname
        r['reverse'] = reverse
        r['config'] = f"SL={sl},TP={tp},HOLD={hold}"
        results.append(r)
        mark = "PASS" if r['sharpe'] > 0.5 and r['pos_folds'] >= 0.6 * r['total'] else "FAIL"
        print(f"  {mark} Sharpe={r['sharpe']:.3f}, Ret={r['ret']:.2f}%, DD={r['dd']:.1f}%, Folds={r['pos_folds']}/{r['total']} ({time.time()-t1:.0f}s)")
    else:
        print(f"  NODATA ({time.time()-t1:.0f}s)")

if results:
    results.sort(key=lambda x: x['sharpe'], reverse=True)
    print(f"\n{'='*80}")
    print(f"{'排名':4s} {'因子':20s} {'方向':4s} {'夏普':>6s} {'收益':>8s} {'回撤':>6s} {'Fold':>6s} {'状态':4s} 风控")
    print(f"{'='*80}")
    for i, r in enumerate(results, 1):
        mark = "PASS" if r['sharpe'] > 0.5 and r['pos_folds'] >= 0.6 * r['total'] else "FAIL"
        direction = "ASC" if r['reverse'] else "DESC"
        print(f"{i:4d} {r['factor']:20s} {direction:4s} {r['sharpe']:6.3f} {r['ret']:7.2f}% {r['dd']:5.1f}% {r['pos_folds']:3d}/{r['total']:<3d} {mark:4s} {r['config']}")

    # Save
    out_path = '/root/a-share-quant-sim/docs/strategy/v56a_single_factor_wf.txt'
    with open(out_path, 'w') as f:
        f.write("v56a 单因子 WF 验证结果\n")
        f.write(f"时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"条件: train=252, test=126, step=63, 2021-06-2026, zz1800\n\n")
        for i, r in enumerate(results, 1):
            mark = "PASS" if r['sharpe'] > 0.5 and r['pos_folds'] >= 0.6 * r['total'] else "FAIL"
            direction = "ASC" if r['reverse'] else "DESC"
            f.write(f"{i:4d} {r['factor']:20s} {direction:4s} Sharpe={r['sharpe']:.3f} Ret={r['ret']:.2f}% DD={r['dd']:.1f}% Folds={r['pos_folds']}/{r['total']} {mark} | {r['config']}\n")
    print(f"\n结果已保存: {out_path}")

print("\nDONE")
