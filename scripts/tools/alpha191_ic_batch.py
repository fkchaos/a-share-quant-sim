#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
import pandas as pd
import numpy as np
from scipy.stats import spearmanr
import warnings
warnings.filterwarnings('ignore')

def load_panel(start='2021-01-01', end='2026-06-29', db_path='data/quant_stocks.db'):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    sql = """SELECT k.code, k.date, k.open, k.high, k.low, k.close, k.volume, k.amount
             FROM daily_kline k JOIN stock_pool_zz1800 p ON k.code = p.code
             WHERE k.date >= ? AND k.date <= ? ORDER BY k.code, k.date"""
    df = pd.read_sql_query(sql, conn, params=[start, end])
    conn.close()
    df['date'] = pd.to_datetime(df['date'])
    df['ret'] = df.groupby('code')['close'].pct_change()
    df['vwap'] = np.where(df['volume'] > 0, df['amount'] / df['volume'], df['close'])
    P = {col: df.pivot(index='date', columns='code', values=col) for col in ['open','high','low','close','volume','amount','vwap','ret']}
    print(f"[info] {df['code'].nunique()} stocks, {df['date'].nunique()} days")
    return P

def precompute(P):
    o = {}
    c, h, l, op, v, a, vw, r = P['close'], P['high'], P['low'], P['open'], P['volume'], P['amount'], P['vwap'], P['ret']
    for d in [1,2,3,4,5,6,10,12,20,24,30,60]:
        o[f's{d}_c'] = c.shift(d); o[f's{d}_h'] = h.shift(d); o[f's{d}_l'] = l.shift(d)
        o[f's{d}_o'] = op.shift(d); o[f's{d}_v'] = v.shift(d)
    for d in [3,5,6,10,12,20,24,30,40,60,80]:
        o[f'm{d}_c'] = c.rolling(d, min_periods=max(2,d//2)).mean()
        o[f'm{d}_v'] = v.rolling(d, min_periods=max(2,d//2)).mean()
        o[f'm{d}_h'] = h.rolling(d, min_periods=max(2,d//2)).mean()
        o[f'm{d}_l'] = l.rolling(d, min_periods=max(2,d//2)).mean()
    for d in [5,6,10,20,24]:
        o[f'std{d}_c'] = c.rolling(d, min_periods=max(2,d//2)).std()
        o[f'std{d}_h'] = h.rolling(d, min_periods=max(2,d//2)).std()
    for d in [5,6,10,12,20,26,60]:
        o[f'sum{d}_ret'] = r.rolling(d, min_periods=max(2,d//2)).sum()
        o[f'sum{d}_v']   = v.rolling(d, min_periods=max(2,d//2)).sum()
    for d in [2,3,5,6,9,12,20]:
        o[f'mx{d}_c'] = c.rolling(d, min_periods=max(2,d//2)).max()
        o[f'mn{d}_c'] = c.rolling(d, min_periods=max(2,d//2)).min()
        o[f'mx{d}_h'] = h.rolling(d, min_periods=max(2,d//2)).max()
        o[f'mn{d}_l'] = l.rolling(d, min_periods=max(2,d//2)).min()
        o[f'mx{d}_v'] = v.rolling(d, min_periods=max(2,d//2)).max()
        o[f'mn{d}_v'] = v.rolling(d, min_periods=max(2,d//2)).min()
    print(f"[info] Precomputed {len(o)} operators")
    return o, c, h, l, op, v, a, vw, r

def compute_fwd(close, h):
    return close.shift(-h) / close - 1

def compute_ic(fp, fwd):
    common = sorted(set(fp.index) & set(fwd.index))
    ics = []
    for date in common:
        f = fp.loc[date].dropna(); r = fwd.loc[date].dropna()
        idx = f.index.intersection(r.index)
        if len(idx) < 30: continue
        fv = f[idx].values.astype(float); rv = r[idx].values.astype(float)
        if np.nanstd(fv) < 1e-10 or np.nanstd(rv) < 1e-10: continue
        ic, _ = spearmanr(fv, rv, nan_policy='omit')
        if not np.isnan(ic): ics.append(ic)
    if not ics: return {'m': np.nan, 'sd': np.nan, 'ir': np.nan, 'n': 0}
    s = pd.Series(ics); return {'m': s.mean(), 'sd': s.std(), 'ir': s.mean()/s.std() if s.std()>0 else 0, 'n': len(ics)}

def evaluate(name, panel, fwds):
    rows = []
    for h, fwd in fwds.items():
        st = compute_ic(panel, fwd)
        if st['n'] < 10: continue
        rows.append({'factor': name, 'holding': h, 'IC_mean': round(st['m'],6), 'IR': round(st['ir'],4),
                     'n_days': st['n'], 'abs_IC': round(abs(st['m']),6), 'abs_IR': round(abs(st['ir']),4),
                     'pass': 'PASS' if abs(st['m'])>0.03 and abs(st['ir'])>0.3 else 'fail'})
    return rows

# ====== Alpha191 因子定义 (每批10个,共20个 pilot) ======
# Batch 1: 均线偏离 + 动量
DEFS = {
    'alpha31': lambda c,o: (c - o['m12_c']) / o['m12_c'].replace(0, np.nan) * 100,
    'alpha34': lambda c,o: o['m12_c'] / c.replace(0, np.nan),
    'alpha65': lambda c,o: o['m6_c'] / c.replace(0, np.nan),
    'alpha71': lambda c,o: (c - o['m24_c']) / o['m24_c'].replace(0, np.nan) * 100,
    'alpha14': lambda c,o: (c - o['s5_c']) / o['s5_c'].replace(0, np.nan),
    'alpha18': lambda c,o: c / o['s5_c'].replace(0, np.nan),
    'alpha20': lambda c,o: (c - o['s6_c']) / o['s6_c'].replace(0, np.nan) * 100,
    'alpha46': lambda c,o: (o['m3_c']+o['m6_c']+o['m12_c']+o['m24_c']) / (4*c).replace(0, np.nan),
    # RSI-like
    'alpha63': lambda c,o: np.maximum(c-o['s1_c'],0).rolling(6,min_periods=3).mean() / np.abs(c-o['s1_c']).rolling(6,min_periods=3).mean()*100,
    'alpha67': lambda c,o: np.maximum(c-o['s1_c'],0).rolling(24,min_periods=12).mean() / np.abs(c-o['s1_c']).rolling(24,min_periods=12).mean()*100,
    'alpha79': lambda c,o: np.maximum(c-o['s1_c'],0).rolling(12,min_periods=6).mean() / np.abs(c-o['s1_c']).rolling(12,min_periods=6).mean()*100,
    # Williams %K / Stochastic
    'alpha47': lambda c,o,h,l: (o['mx6_h']-c)/(o['mx6_h']-o['mn6_l']).replace(0,np.nan)*100,
    'alpha57': lambda c,o,h,l: (c-o['mn9_l'])/(o['mx9_h']-o['mn9_l']).replace(0,np.nan)*100,
    'alpha82': lambda c,o,h,l: (o['mx6_h']-c)/(o['mx6_h']-o['mn6_l']).replace(0,np.nan)*100,
    # 涨跌天数占比
    'alpha53': lambda c,o: (c > o['s1_c']).rolling(12).sum()/12*100,
    'alpha58': lambda c,o: (c > o['s1_c']).rolling(20).sum()/20*100,
    # 量比变化
    'alpha80': lambda c,o,v: (v - o['s5_v']) / o['s5_v'].replace(0, np.nan)*100,
    # 典型价差结构
    'alpha2': lambda c,o,h,l: ((c-l)-(h-c))/(h-l).replace(0,np.nan).diff(1)*-1,
}

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--start', default='2021-01-01')
    parser.add_argument('--end',   default='2026-06-29')
    parser.add_argument('--db',    default='data/quant_stocks.db')
    args = parser.parse_args()

    P = load_panel(args.start, args.end, args.db)
    o, c, h, l, op, v, a, vw, r = precompute(P)
    fwds = {hh: compute_fwd(c, hh) for hh in [3, 5, 10]}

    all_rows = []
    for name, fn in DEFS.items():
        sys.stdout.write(f"\r[IC] {name:<12} ..."); sys.stdout.flush()
        try:
            # 按 lambda 参数个数传参
            import inspect
            sig = inspect.signature(fn)
            params = [p for p in sig.parameters]
            args_dict = {'c': c, 'o': o, 'h': h, 'l': l, 'op': op, 'v': v, 'a': a, 'vw': vw, 'r': r}
            panel = fn(**{p: args_dict[p] for p in params})
        except Exception as e:
            print(f"\n[warn] {name}: {e}"); continue
        all_rows.extend(evaluate(name, panel, fwds))

    print()
    out = pd.DataFrame(all_rows)
    out_path = '/root/alpha-research/reports/alpha191_pilot_ic.csv'
    out.to_csv(out_path, index=False)
    print("\n=== IC 结果 ===")
    for hh in [3,5,10]:
        sub = out[(out['holding']==hh) & (out['abs_IC']>0.02)].sort_values('abs_IC', ascending=False)
        if len(sub):
            print(f"\n--- holding {hh}d ---")
            print(sub[['factor','IC_mean','IR','n_days','pass']].to_string(index=False))
    print(f"\nPass: {(out['pass']=='PASS').sum()} / {len(out)} rows")
    print(f"[save] -> {out_path}")

if __name__ == '__main__':
    main()
