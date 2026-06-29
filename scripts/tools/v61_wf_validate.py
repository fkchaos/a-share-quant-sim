#!/usr/bin/env python3
"""v61 WF 验证 — 换手率因子组合"""
import sys, os
sys.path.insert(0, '/root/a-share-quant-sim')
import sqlite3, numpy as np, pandas as pd

print("[1] 加载数据...")
conn = sqlite3.connect('data/quant_stocks.db', timeout=30)
conn.execute('PRAGMA journal_mode=WAL')
codes_df = pd.read_sql_query('SELECT code, float_shares FROM stock_pool_zz1800', conn)
codes = codes_df['code'].tolist()
fs_map = dict(zip(codes_df['code'], codes_df['float_shares']))

placeholders = ','.join(['?']*len(codes))
sql = f"""SELECT code, date, open, high, low, close, volume
          FROM daily_kline WHERE code IN ({placeholders})
          AND date >= '2020-06-01' AND date <= '2026-06-29'
          ORDER BY code, date"""
df = pd.read_sql_query(sql, conn, params=codes)
conn.close()
df['date'] = pd.to_datetime(df['date'])
df['ret'] = df.groupby('code')['close'].pct_change()
df['float_shares'] = df['code'].map(fs_map)
df['turnover'] = df['volume'] * 100 / df['float_shares']  # volume是手, 需要*100
df['market_cap'] = df['close'] * df['float_shares']

close = df.pivot(index='date', columns='code', values='close')
turnover = df.pivot(index='date', columns='code', values='turnover')
ret = df.pivot(index='date', columns='code', values='ret')
mcap = df.pivot(index='date', columns='code', values='market_cap')

# 预计算
turn_5 = turnover.rolling(5, min_periods=3).mean()
turn_20 = turnover.rolling(20, min_periods=10).mean()
mom_5 = close / close.shift(5) - 1

print(f"    {close.shape[0]} days, {close.shape[1]} stocks")

# WF 参数
TRAIN = 252
TEST = 126
STEP = 63
REBAL = 5
TOP_N = 5
INIT_CASH = 200000

dates = sorted(close.index)
start = next(i for i, d in enumerate(dates) if d >= pd.Timestamp('2021-01-01'))

# 定义组合
COMBOS = {
    '换手5日':      lambda d: (-turn_5.loc[d],),
    '换手5+小市值': lambda d: (-turn_5.loc[d], -mcap.loc[d]),
    '换手5+小市值+动量5': lambda d: (-turn_5.loc[d], -mcap.loc[d], mom_5.loc[d]),
}

def run_fold(fold_idx, test_start, test_end, combo_fn):
    """跑一个 fold 的测试期"""
    test_dates = [d for d in dates if test_start <= d <= test_end]
    if len(test_dates) < 10:
        return None

    cash = INIT_CASH
    holdings = {}
    nav_list = []
    days_since = REBAL

    for date in test_dates:
        val = cash
        for code, pos in holdings.items():
            if code in close.columns:
                p = close.at[date, code]
                if not np.isnan(p):
                    val += pos['shares'] * p
        nav_list.append({'date': date, 'nav': val})

        days_since += 1
        if days_since >= REBAL:
            days_since = 0
            factors = combo_fn(date)
            if factors is None or factors[0] is None:
                continue

            scores = pd.Series(0.0, index=close.columns)
            for f in factors:
                valid = f.dropna()
                if len(valid) < 50:
                    continue
                ranked = valid.rank(ascending=True, pct=True)
                scores[ranked.index] += ranked

            valid_codes = [c for c in scores.dropna().index
                          if close.at[date, c] > 0 and turnover.at[date, c] > 0]
            scores = scores[valid_codes].sort_values(ascending=False)
            target = scores.head(TOP_N).index.tolist()

            for code in list(holdings.keys()):
                if code not in target:
                    if code in close.columns:
                        p = close.at[date, code]
                        if not np.isnan(p):
                            cash += holdings[code]['shares'] * p * 0.9987
                    del holdings[code]

            n_buy = len(target) - len(holdings)
            if n_buy > 0 and cash > 0:
                per = cash * 0.95 / n_buy
                for code in target:
                    if code not in holdings and code in close.columns:
                        p = close.at[date, code]
                        if not np.isnan(p) and p > 0:
                            shares = int(per / p / 100) * 100
                            if shares > 0:
                                cost = shares * p * 1.0003
                                if cost <= cash:
                                    cash -= cost
                                    holdings[code] = {'shares': shares, 'cost': p}

    if not nav_list:
        return None
    nav = pd.Series([n['nav'] for n in nav_list], index=[n['date'] for n in nav_list])
    total = (nav.iloc[-1] / nav.iloc[0] - 1) * 100
    daily_ret = nav.pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    dd = (nav / nav.cummax() - 1).min() * 100
    return {'total': total, 'sharpe': sharpe, 'dd': dd, 'n_days': len(nav)}

# 运行 WF
print(f"\n[2] WF 验证 (train={TRAIN}, test={TEST}, step={STEP})")
print(f"{'='*70}")

for name, combo_fn in COMBOS.items():
    print(f"\n--- {name} ---")
    fold_results = []
    fold_idx = 0
    i = start

    while i + TRAIN + TEST <= len(dates):
        test_s = dates[i + TRAIN]
        test_e = dates[min(i + TRAIN + TEST - 1, len(dates) - 1)]
        r = run_fold(fold_idx, test_s, test_e, combo_fn)
        if r:
            fold_results.append(r)
            marker = "✅" if r['sharpe'] > 0 else "❌"
            print(f"  Fold {fold_idx:>2}: {test_s.date()} ~ {test_e.date()}  "
                  f"ret={r['total']:>+7.2f}%  sharpe={r['sharpe']:+.3f}  dd={r['dd']:.1f}% {marker}")
        fold_idx += 1
        i += STEP

    if fold_results:
        avg_ret = np.mean([f['total'] for f in fold_results])
        avg_sharpe = np.mean([f['sharpe'] for f in fold_results])
        avg_dd = np.mean([f['dd'] for f in fold_results])
        pos = sum(1 for f in fold_results if f['sharpe'] > 0)
        nf = len(fold_results)
        print(f"  平均: ret={avg_ret:+.2f}%  sharpe={avg_sharpe:+.3f}  dd={avg_dd:.1f}%  正fold={pos}/{nf}")
        print(f"  {'✅ PASS' if pos/nf >= 0.6 and avg_sharpe > 0.5 else '❌ FAIL'}")

print(f"\n{'='*70}")
print(f"参考: v39g WF: sharpe=1.164, 正fold=12/16, 年化=+16.02%")
