#!/usr/bin/env python3
"""v61b 风控参数精调 v2 — 卖出后立刻买入"""
import sys, os, json
sys.path.insert(0, '/root/a-share-quant-sim')
import sqlite3, numpy as np, pandas as pd

RESULT_FILE = '/root/a-share-quant-sim/scripts/backtest/v61b_risk_results.json'

# 标准WF参数（供wf_runner调用）
DEFAULT_PARAMS = {
    'REBALANCE_DAYS': 5,
    'TOP_N': 5,
    'STOP_LOSS': -0.08,
    'TAKE_PROFIT': 0.25,
    'HOLD_DAYS_MAX': 5,
}

def load_data():
    return load_data_with_range('2020-06-01', '2026-06-29')

def load_data_with_range(start_date='2020-06-01', end_date='2026-06-29'):
    """加载数据，支持指定日期范围"""
    print(f"[1] 加载数据 ({start_date} ~ {end_date})...")
    conn = sqlite3.connect('data/quant_stocks.db', timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    codes_df = pd.read_sql_query('SELECT code, float_shares FROM stock_pool_zz1800', conn)
    codes = codes_df['code'].tolist()
    fs_map = dict(zip(codes_df['code'], codes_df['float_shares']))

    placeholders = ','.join(['?']*len(codes))
    sql = f"""SELECT code, date, open, high, low, close, volume
              FROM daily_kline WHERE code IN ({placeholders})
              AND date >= '{start_date}' AND date <= '{end_date}'
              ORDER BY code, date"""
    df = pd.read_sql_query(sql, conn, params=codes)
    conn.close()

    df['date'] = pd.to_datetime(df['date'])
    df['float_shares'] = df['code'].map(fs_map)
    df['turnover'] = df['volume'] * 100 / df['float_shares']
    df['market_cap'] = df['close'] * df['float_shares']

    close = df.pivot(index='date', columns='code', values='close')
    turnover = df.pivot(index='date', columns='code', values='turnover')
    mcap = df.pivot(index='date', columns='code', values='market_cap')
    turn_5 = turnover.rolling(5, min_periods=3).mean()

    print(f"    {close.shape[0]} days, {close.shape[1]} stocks")
    return {'close': close, 'turnover': turnover, 'mcap': mcap, 'turn_5': turn_5}

def calc_scores(date, data):
    """计算选股分数"""
    close = data['close']
    turnover = data['turnover']
    turn_5 = data['turn_5']
    mcap = data['mcap']
    
    t5 = turn_5.loc[date]
    sz = mcap.loc[date]
    
    scores = pd.Series(0.0, index=close.columns)
    for f in (-t5, -sz):
        valid = f.dropna()
        if len(valid) >= 50:
            ranked = valid.rank(ascending=True, pct=True)
            scores[ranked.index] += ranked
    
    valid_codes = [c for c in scores.dropna().index
                  if close.at[date, c] > 0 and turnover.at[date, c] > 0]
    return scores[valid_codes].sort_values(ascending=False)

def run_fold(data, test_start, test_end, rebal, top_n, sl, tp, hold_max):
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

    def sell(code, date):
        nonlocal cash
        if code in close.columns:
            p = close.at[date, code]
            if not np.isnan(p):
                cash += holdings[code]['shares'] * p * 0.9987
        del holdings[code]

    def buy_new(date):
        """买入新股票直到满仓"""
        nonlocal cash
        scores = calc_scores(date, data)
        target = scores.head(top_n).index.tolist()
        
        # 先卖不在目标中的
        for code in list(holdings.keys()):
            if code not in target:
                sell(code, date)
        
        # 再买新的
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

        # 执行卖出
        for code in to_sell:
            sell(code, date)

        nav_list.append({'date': date, 'nav': val})
        days_since += 1

        # 调仓日或有卖出时，都重新计算买入
        if days_since >= rebal or len(to_sell) > 0:
            days_since = 0 if days_since >= rebal else days_since
            buy_new(date)

    if not nav_list:
        return None

    nav = pd.Series([n['nav'] for n in nav_list], index=[n['date'] for n in nav_list])
    total = (nav.iloc[-1] / nav.iloc[0] - 1) * 100
    daily_ret = nav.pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    dd = (nav / nav.cummax() - 1).min() * 100
    return {'total': total, 'sharpe': sharpe, 'dd': dd}

def run_wf_overlay(train_days=252, test_days=126, step_days=63,
                   start_date='2021-01-01', end_date='2026-05-31',
                   params=None):
    """
    标准WF回测接口，供wf_runner调用
    
    返回标准结果格式：
    {
        "total": 总收益率(%),
        "sharpe": 夏普比率,
        "dd": 最大回撤(%),
        "pos_rate": 正收益fold比例(%),
        "n_folds": fold数量,
        "fold_results": [...]
    }
    """
    p = {**DEFAULT_PARAMS, **(params or {})}
    
    # 加载数据
    data = load_data_with_range('2020-06-01', end_date)
    dates = sorted(data['close'].index)
    
    # 找到起始位置
    start_idx = next((i for i, d in enumerate(dates) if d >= pd.Timestamp(start_date)), 0)
    
    # 提取参数
    rebal = p['REBALANCE_DAYS']
    top_n = p['TOP_N']
    sl = p['STOP_LOSS']
    tp = p['TAKE_PROFIT']
    hold_max = p['HOLD_DAYS_MAX']
    
    # 运行WF
    fold_results = []
    i = start_idx
    while i + train_days + test_days <= len(dates):
        test_s = dates[i + train_days]
        test_e = dates[min(i + train_days + test_days - 1, len(dates) - 1)]
        r = run_fold(data, test_s, test_e, rebal, top_n, sl, tp, hold_max)
        if r:
            fold_results.append(r)
        i += step_days
    
    if not fold_results:
        return {"total": 0, "sharpe": 0, "dd": 0, "pos_rate": 0, "n_folds": 0, "fold_results": []}
    
    # 计算汇总指标
    avg_ret = np.mean([f['total'] for f in fold_results])
    avg_sharpe = np.mean([f['sharpe'] for f in fold_results])
    avg_dd = np.mean([f['dd'] for f in fold_results])
    pos = sum(1 for f in fold_results if f['sharpe'] > 0)
    nf = len(fold_results)
    
    return {
        "total": round(avg_ret, 2),
        "sharpe": round(avg_sharpe, 3),
        "dd": round(avg_dd, 1),
        "pos_rate": round(pos / nf * 100, 1),
        "n_folds": nf,
        "fold_results": fold_results,
    }

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--group', type=int, default=0, help='组号')
    parser.add_argument('--list', action='store_true', help='列出所有组')
    args = parser.parse_args()

    data = load_data()
    dates = sorted(data['close'].index)
    start_idx = next(i for i, d in enumerate(dates) if d >= pd.Timestamp('2021-01-01'))

    TRAIN = 252
    TEST = 126
    STEP = 63

    HOLD_DAYS = [3, 5, 7, 10]
    STOP_LOSS = [-0.08, -0.10, -0.12, -0.15]
    TAKE_PROFIT = [0.15, 0.20, 0.25, 0.30]

    if args.list:
        for i, h in enumerate(HOLD_DAYS):
            print(f"  组{i}: HOLD_DAYS_MAX={h}")
        return

    if args.group >= len(HOLD_DAYS):
        print(f"组号超出范围 (0-{len(HOLD_DAYS)-1})")
        return

    hold_max = HOLD_DAYS[args.group]

    results = {}
    if os.path.exists(RESULT_FILE):
        with open(RESULT_FILE, 'r') as f:
            results = json.load(f)

    print(f"\n[2] 组{args.group}: HOLD_DAYS_MAX={hold_max}")
    print(f"{'='*60}")

    for sl in STOP_LOSS:
        for tp in TAKE_PROFIT:
            rebal = 5
            top_n = 5
            label = f"SL={sl:.0%}/TP={tp:.0%}/HD={hold_max}"
            key = f"{sl}_{tp}_{hold_max}"

            if key in results:
                print(f"  {label:<25} [已跳过] 夏普={results[key]['sharpe']:.3f}")
                continue

            fold_results = []
            i = start_idx
            while i + TRAIN + TEST <= len(dates):
                test_s = dates[i + TRAIN]
                test_e = dates[min(i + TRAIN + TEST - 1, len(dates) - 1)]
                r = run_fold(data, test_s, test_e, rebal, top_n, sl, tp, hold_max)
                if r:
                    fold_results.append(r)
                i += STEP

            if fold_results:
                avg_ret = np.mean([f['total'] for f in fold_results])
                avg_sharpe = np.mean([f['sharpe'] for f in fold_results])
                avg_dd = np.mean([f['dd'] for f in fold_results])
                pos = sum(1 for f in fold_results if f['sharpe'] > 0)
                nf = len(fold_results)

                results[key] = {
                    'stop_loss': sl, 'take_profit': tp, 'hold_days': hold_max,
                    'ret': round(avg_ret, 2), 'sharpe': round(avg_sharpe, 3),
                    'dd': round(avg_dd, 1), 'pos_rate': round(pos/nf*100, 1),
                    'n_folds': nf,
                }
                with open(RESULT_FILE, 'w') as f:
                    json.dump(results, f, indent=2)

                marker = "✅" if avg_sharpe > 2.0 and pos/nf >= 0.9 else "  "
                print(f"  {label:<25} ret={avg_ret:>+7.2f}%  sharpe={avg_sharpe:+.3f}  "
                      f"dd={avg_dd:+.1f}%  pos={pos}/{nf} {marker}")
            else:
                print(f"  {label:<25} [无结果]")

    print(f"\n{'='*60}")
    print(f"结果已保存: {RESULT_FILE}")

if __name__ == '__main__':
    main()
