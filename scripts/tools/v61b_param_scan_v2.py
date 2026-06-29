#!/usr/bin/env python3
"""v61b 参数扫描 — 支持分组+断点续传"""
import sys, os, json, hashlib
sys.path.insert(0, '/root/a-share-quant-sim')
import sqlite3, numpy as np, pandas as pd
from datetime import datetime

RESULT_FILE = '/root/a-share-quant-sim/scripts/tools/v61b_results.json'

def load_data():
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
    df['turnover'] = df['volume'] * 100 / df['float_shares']
    df['market_cap'] = df['close'] * df['float_shares']

    close = df.pivot(index='date', columns='code', values='close')
    turnover = df.pivot(index='date', columns='code', values='turnover')
    ret = df.pivot(index='date', columns='code', values='ret')
    mcap = df.pivot(index='date', columns='code', values='market_cap')

    # 预计算
    turn_3 = turnover.rolling(3, min_periods=2).mean()
    turn_5 = turnover.rolling(5, min_periods=3).mean()
    turn_10 = turnover.rolling(10, min_periods=5).mean()
    turn_20 = turnover.rolling(20, min_periods=10).mean()
    mom_3 = close / close.shift(3) - 1
    mom_10 = close / close.shift(10) - 1
    vol_5 = ret.rolling(5, min_periods=3).std()
    vol_10 = ret.rolling(10, min_periods=5).std()

    print(f"    {close.shape[0]} days, {close.shape[1]} stocks")
    return {
        'close': close, 'turnover': turnover, 'ret': ret, 'mcap': mcap,
        'turn_3': turn_3, 'turn_5': turn_5, 'turn_10': turn_10, 'turn_20': turn_20,
        'mom_3': mom_3, 'mom_10': mom_10, 'vol_5': vol_5, 'vol_10': vol_10,
    }

def run_fold(data, test_start, test_end, combo_fn, rebal, top_n, sl, tp, hold_max):
    close = data['close']
    turnover = data['turnover']
    dates = sorted(close.index)
    test_dates = [d for d in dates if test_start <= d <= test_end]
    if len(test_dates) < 10:
        return None

    INIT_CASH = 200000
    cash = INIT_CASH
    holdings = {}
    nav_list = []
    days_since = rebal

    for date in test_dates:
        val = cash
        to_sell = []
        for code, pos in holdings.items():
            if code in close.columns:
                p = close.at[date, code]
                if not np.isnan(p):
                    val += pos['shares'] * p
                    pnl = (p - pos['cost']) / pos['cost']
                    if pnl <= sl or pnl >= tp:
                        to_sell.append(code)
                        continue
                    pos['days'] = pos.get('days', 0) + 1
                    if pos['days'] >= hold_max:
                        to_sell.append(code)
        for code in to_sell:
            p = close.at[date, code]
            if not np.isnan(p):
                cash += holdings[code]['shares'] * p * 0.9987
            del holdings[code]

        nav_list.append({'date': date, 'nav': val})
        days_since += 1

        if days_since >= rebal:
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
            target = scores.head(top_n).index.tolist()

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
                                    holdings[code] = {'shares': shares, 'cost': p, 'days': 0}

    if not nav_list:
        return None

    nav = pd.Series([n['nav'] for n in nav_list], index=[n['date'] for n in nav_list])
    total = (nav.iloc[-1] / nav.iloc[0] - 1) * 100
    daily_ret = nav.pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    dd = (nav / nav.cummax() - 1).min() * 100
    return {'total': total, 'sharpe': sharpe, 'dd': dd, 'n_days': len(nav)}

def load_results():
    if os.path.exists(RESULT_FILE):
        with open(RESULT_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_results(results):
    with open(RESULT_FILE, 'w') as f:
        json.dump(results, f, indent=2)

def config_hash(combo_name, param_label):
    return hashlib.md5(f"{combo_name}|{param_label}".encode()).hexdigest()[:8]

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--group', type=int, default=0, help='组号 (0-11)')
    parser.add_argument('--list', action='store_true', help='列出所有组')
    args = parser.parse_args()

    data = load_data()
    dates = sorted(data['close'].index)
    start_idx = next(i for i, d in enumerate(dates) if d >= pd.Timestamp('2021-01-01'))

    # 因子组合定义
    COMBOS = {
        'A1: 换手60/市值40': lambda d: (-data['turn_5'].loc[d]*0.6, -data['mcap'].loc[d]*0.4),
        'A2: 换手70/市值30': lambda d: (-data['turn_5'].loc[d]*0.7, -data['mcap'].loc[d]*0.3),
        'A3: 换手40/市值60': lambda d: (-data['turn_5'].loc[d]*0.4, -data['mcap'].loc[d]*0.6),
        'A4: 换手30/市值70': lambda d: (-data['turn_5'].loc[d]*0.3, -data['mcap'].loc[d]*0.7),
        'B1: 换手3日+市值': lambda d: (-data['turn_3'].loc[d], -data['mcap'].loc[d]),
        'B2: 换手10日+市值': lambda d: (-data['turn_10'].loc[d], -data['mcap'].loc[d]),
        'B3: 换手20日+市值': lambda d: (-data['turn_20'].loc[d], -data['mcap'].loc[d]),
        'C1: 换手+市值+动量3': lambda d: (-data['turn_5'].loc[d], -data['mcap'].loc[d], data['mom_3'].loc[d]),
        'C2: 换手+市值+动量10': lambda d: (-data['turn_5'].loc[d], -data['mcap'].loc[d], data['mom_10'].loc[d]),
        'D1: 换手+市值+低波5': lambda d: (-data['turn_5'].loc[d], -data['mcap'].loc[d], -data['vol_5'].loc[d]),
        'D2: 换手+市值+低波10': lambda d: (-data['turn_5'].loc[d], -data['mcap'].loc[d], -data['vol_10'].loc[d]),
        'BASE: v61原始': lambda d: (-data['turn_5'].loc[d], -data['mcap'].loc[d]),
    }

    PARAM_SETS = [
        (5, 5, -0.10, 0.20, 5, 'v61原始'),
        (3, 5, -0.10, 0.20, 3, '调仓3天'),
        (7, 5, -0.10, 0.20, 7, '调仓7天'),
        (5, 3, -0.10, 0.20, 5, '持仓3只'),
        (5, 8, -0.10, 0.20, 5, '持仓8只'),
        (5, 5, -0.08, 0.15, 5, '止损-8%/止盈15%'),
        (5, 5, -0.12, 0.25, 5, '止损-12%/止盈25%'),
    ]

    # 分组：按因子组合分12组
    combo_names = list(COMBOS.keys())
    if args.list:
        for i, name in enumerate(combo_names):
            print(f"  组{i:2d}: {name}")
        return

    if args.group >= len(combo_names):
        print(f"组号超出范围 (0-{len(combo_names)-1})")
        return

    combo_name = combo_names[args.group]
    combo_fn = COMBOS[combo_name]

    results = load_results()
    TRAIN = 252
    TEST = 126
    STEP = 63

    print(f"\n[2] 组{args.group}: {combo_name}")
    print(f"{'='*60}")

    for params in PARAM_SETS:
        rebal, top_n, sl, tp, hold_max, param_label = params
        h = config_hash(combo_name, param_label)

        # 断点续传：跳过已完成的
        if h in results:
            print(f"  {param_label:<20} [已跳过] 夏普={results[h]['sharpe']:.3f}")
            continue

        fold_results = []
        i = start_idx
        while i + TRAIN + TEST <= len(dates):
            test_s = dates[i + TRAIN]
            test_e = dates[min(i + TRAIN + TEST - 1, len(dates) - 1)]
            r = run_fold(data, test_s, test_e, combo_fn, rebal, top_n, sl, tp, hold_max)
            if r:
                fold_results.append(r)
            i += STEP

        if fold_results:
            avg_ret = np.mean([f['total'] for f in fold_results])
            avg_sharpe = np.mean([f['sharpe'] for f in fold_results])
            avg_dd = np.mean([f['dd'] for f in fold_results])
            pos = sum(1 for f in fold_results if f['sharpe'] > 0)
            nf = len(fold_results)

            results[h] = {
                'combo': combo_name, 'params': param_label,
                'ret': round(avg_ret, 2), 'sharpe': round(avg_sharpe, 3),
                'dd': round(avg_dd, 1), 'pos_rate': round(pos/nf*100, 1),
                'n_folds': nf, 'time': datetime.now().isoformat(),
            }
            save_results(results)

            marker = "✅" if avg_sharpe > 2.0 and pos/nf >= 0.9 else "  "
            print(f"  {param_label:<20} ret={avg_ret:>+7.2f}%  sharpe={avg_sharpe:+.3f}  "
                  f"dd={avg_dd:+.1f}%  pos={pos}/{nf} {marker}")
        else:
            print(f"  {param_label:<20} [无结果]")

    print(f"\n{'='*60}")
    print(f"结果已保存: {RESULT_FILE}")

if __name__ == '__main__':
    main()
