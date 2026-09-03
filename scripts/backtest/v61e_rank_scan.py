#!/usr/bin/env python3
"""v61e: v61c排名掉出Top15 + 涨跌幅确认启动信号

核心思路：
- v61c选低换手+小市值的Top15
- 如果一只股票5天前在Top15，现在掉出去了
- 排名下降 = 换手率升高/市值变大 = 被市场关注 = 可能要启动
- 加涨跌幅判断确认：3日涨幅>1%、5日涨幅>2%才买

基于v61c_risk_scan.py改造
"""

import sys, os, json, sqlite3
import numpy as np, pandas as pd

RESULT_FILE = '/root/a-share-quant-sim/scripts/backtest/v61e_rank_results.json'

DEFAULT_PARAMS = {
    'REBALANCE_DAYS': 5,
    'TOP_N': 5,
    'STOP_LOSS': -0.08,
    'TAKE_PROFIT': 0.25,
    'HOLD_DAYS_MAX': 5,
    'SELL_OUT_OF': 15,
    'MAX_POSITION': 0.20,
    'RANK_TODAY_N': 15,      # 今日Top N范围
    'LOOKBACK_BARS': 5,       # 回看5天前的排名
    'MIN_PCT_3D': 0.01,       # 3日最小涨幅（1%确认启动）
    'MIN_PCT_5D': 0.02,       # 5日最小涨幅（2%确认启动）
}

# 缓存交易日历
_TRADING_DAYS_CACHE = None

def _get_trading_days(start='2020-01-01', end='2026-12-31'):
    global _TRADING_DAYS_CACHE
    if _TRADING_DAYS_CACHE is None:
        conn = sqlite3.connect(os.path.join(os.path.dirname(__file__), '../../data/quant_stocks.db'))
        rows = conn.execute(
            "SELECT DISTINCT date FROM daily_kline WHERE code='sh000001' ORDER BY date"
        ).fetchall()
        conn.close()
        _TRADING_DAYS_CACHE = set(r[0] for r in rows)
    return _TRADING_DAYS_CACHE

def count_trading_days(entry_date_str, current_date_str):
    days = _get_trading_days()
    entry = str(entry_date_str)[:10]
    current = str(current_date_str)[:10]
    count = 0
    for d in sorted(days):
        if d > entry and d <= current:
            count += 1
    return count

def load_data_with_range(start_date='2020-06-01', end_date='2026-06-29'):
    print(f"[1] Loading data ({start_date} ~ {end_date})...")
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

    daily_ret = close.pct_change()
    is_limit = ((daily_ret >= 0.095) & (daily_ret <= 0.105)).astype(float).fillna(0)
    two_day_limit = (is_limit.shift(1).fillna(0) == 1) & (is_limit == 1)
    daily_limit_count = two_day_limit.astype(float).sum(axis=1)

    print(f"    {close.shape[0]} days, {close.shape[1]} stocks")
    return {'close': close, 'turnover': turnover, 'mcap': mcap, 'turn_5': turn_5,
            'daily_limit_count': daily_limit_count}

def calc_scores(date, data):
    """计算v61c因子得分"""
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

def select_v61e(date, data, rank_history, params):
    """v61e选股逻辑：排名掉出Top15 + 涨幅确认"""
    p = params
    close = data['close']
    rank_n = p.get('RANK_TODAY_N', 15)
    lookback = p.get('LOOKBACK_BARS', 5)
    min_pct_3d = p.get('MIN_PCT_3D', 0.01)
    min_pct_5d = p.get('MIN_PCT_5D', 0.02)

    # 计算今日得分
    scores = calc_scores(date, data)

    # 保存今日排名到历史
    rank_history[date] = scores.copy()

    # 清理旧数据 (只保留最近30天)
    if len(rank_history) > 30:
        sorted_dates = sorted(rank_history.keys())
        for old_date in sorted_dates[:-30]:
            del rank_history[old_date]

    # 找出今日Top N
    sorted_today = scores.sort_values(ascending=False)
    today_top_n = set(sorted_today.head(rank_n).index)

    # 找出历史Top N（lookback天前）
    hist_scores = None
    hist_date = None
    for d in sorted(rank_history.keys(), reverse=True):
        if d < date:
            hist_scores = rank_history[d]
            hist_date = d
            break

    if hist_scores is None:
        return []

    sorted_hist = hist_scores.sort_values(ascending=False)
    hist_top_n = set(sorted_hist.head(rank_n).index)

    # 找出"历史在Top N，现在不在Top N"的股票
    dropped = [c for c in hist_top_n if c not in today_top_n]
    # 找出"历史在Top N，现在不在Top N"的股票
    dropped = [c for c in hist_top_n if c not in today_top_n]
    if not dropped:
        return []
    # 加涨跌幅条件：排名下降同时股价上涨
    candidates = []
    for code in dropped:
        if code in close.columns:
            # 计算3日和5日涨幅
            try:
                idx = close.index.get_loc(date)
                if idx >= 5:
                    p3 = close.iloc[idx-3][code]
                    p5 = close.iloc[idx-5][code]
                    p_now = close.iloc[idx][code]
                    if p3 > 0 and p5 > 0 and p_now > 0:
                        r3 = (p_now / p3 - 1)
                        r5 = (p_now / p5 - 1)
                        # 排名下降同时股价上涨：5日涨幅>2%确认启动
                        if r5 > min_pct_5d:
                            candidates.append((code, round(scores.get(code, 0), 4), round(r3, 4), round(r5, 4)))
            except:
                pass

    # 按5日涨跌幅降序排序
    candidates.sort(key=lambda x: x[3], reverse=True)

    return [(code, score) for code, score, r3, r5 in candidates]

def run_fold(data, test_start, test_end, params):
    """运行一个fold的回测"""
    close = data['close']
    turnover = data['turnover']
    daily_limit_count = data.get('daily_limit_count')
    dates = sorted(close.index)
    test_dates = [d for d in dates if test_start <= d <= test_end]
    if len(test_dates) < 10:
        return None

    p = params
    rebal = p['REBALANCE_DAYS']
    top_n = p['TOP_N']
    sl = p['STOP_LOSS']
    tp = p['TAKE_PROFIT']
    hold_max = p['HOLD_DAYS_MAX']
    sell_out_of = p.get('SELL_OUT_OF', 15)

    INIT_CASH = 200000
    cash = INIT_CASH
    holdings = {}
    nav_list = []
    rank_history = {}
    first_day = True
    days_since_rebal = 0

    def sell(code, date):
        nonlocal cash
        if code in close.columns:
            p_val = close.at[date, code]
            if not np.isnan(p_val):
                cash += holdings[code]['shares'] * p_val * 0.9987
        del holdings[code]

    def buy_new(date):
        nonlocal cash
        candidates = select_v61e(date, data, rank_history, p)
        if not candidates:
            return

        # 排除已持仓
        candidates = [(c, s) for c, s in candidates if c not in holdings]

        avail = cash - INIT_CASH * 0.03
        if avail <= 0:
            return

        nb = min(len(candidates), top_n - len(holdings))
        if nb <= 0:
            return

        per_stock = min(avail / nb, INIT_CASH * p.get('MAX_POSITION', 0.20))

        for code, score in candidates[:nb]:
            if code in close.columns:
                price = close.at[date, code]
                if not np.isnan(price) and price > 0:
                    shares = int(per_stock / price / 100) * 100
                    if shares > 0:
                        cost = shares * price * 1.0013
                        if cost <= cash:
                            cash -= cost
                            holdings[code] = {
                                'shares': shares,
                                'entry_price': price,
                                'entry_date': str(date)[:10],
                                'score': score,
                                'hold_days': 0,
                            }

    for date in test_dates:
        # 更新持仓天数
        for code in holdings:
            holdings[code]['hold_days'] = holdings[code].get('hold_days', 0) + 1

        # 检查止损止盈
        to_sell = []
        for code, info in holdings.items():
            if code in close.columns:
                price = close.at[date, code]
                if np.isnan(price):
                    continue
                pnl = (price / info['entry_price'] - 1)
                if pnl <= sl or pnl >= tp:
                    to_sell.append((code, 'stop_loss' if pnl <= 0 else 'take_profit'))
                elif info['hold_days'] >= hold_max:
                    # 到期检查：是否还在sell_out_of范围内
                    scores = calc_scores(date, data)
                    rank_pos = (scores.rank(ascending=False)).get(code, 999)
                    if rank_pos > sell_out_of:
                        to_sell.append((code, 'timeout'))
                    else:
                        # 续持：重置持有天数
                        info['hold_days'] = 0

        for code, reason in to_sell:
            if code in holdings:
                sell(code, date)

        # 每rebal天调仓
        days_since_rebal += 1
        if days_since_rebal >= rebal:
            days_since_rebal = 0
            buy_new(date)

        # 计算净值
        total = cash
        for code, info in holdings.items():
            if code in close.columns:
                price = close.at[date, code]
                if not np.isnan(price):
                    total += info['shares'] * price
        nav_list.append(total)

        first_day = False

    if not nav_list:
        return None

    nav = pd.Series(nav_list, index=test_dates[:len(nav_list)])
    total_ret = (nav.iloc[-1] / INIT_CASH - 1) * 100
    daily_ret = nav.pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    max_dd = (nav / nav.cummax() - 1).min() * 100

    return {
        'total': total_ret,
        'sharpe': sharpe,
        'dd': max_dd,
        'nav': nav,
    }

def run_wf_overlay(train_days=252, test_days=126, step_days=63,
                   start_date='2021-01-01', end_date='2026-05-31', params=None, full=False):
    """标准WF回测接口"""
    p = {**DEFAULT_PARAMS, **(params or {})}

    data = load_data_with_range('2020-06-01', end_date)
    dates = sorted(data['close'].index)
    start_idx = next((i for i, d in enumerate(dates) if d >= pd.Timestamp(start_date)), 0)

    print(f"\n[v61e overlay] Params: {p}")

    if full:
        print(f"[v61e overlay] Full backtest mode, {start_date} ~ {end_date}")
        test_s = dates[start_idx]
        test_e = dates[-1]
        r = run_fold(data, test_s, test_e, p)
        if r is None:
            return {"total": 0, "sharpe": 0, "dd": 0, "pos_rate": 0, "n_folds": 0}

        nav = r['nav']
        print(f"\n--- Yearly Stats ---")
        for year in range(2021, 2027):
            ym = nav.index.year == year
            if ym.sum() == 0:
                continue
            yn = nav[ym]
            if len(yn) < 2:
                continue
            yr = (yn.iloc[-1] / yn.iloc[0] - 1) * 100
            yd = yn.pct_change().dropna()
            ys = yd.mean() / yd.std() * np.sqrt(252) if yd.std() > 0 else 0
            ydd = (yn / yn.cummax() - 1).min() * 100
            print(f"  {year}: Return={yr:+.1f}%, Sharpe={ys:+.3f}, DD={ydd:.1f}%")

        return {
            "total": round(r['total'], 2),
            "sharpe": round(r['sharpe'], 3),
            "dd": round(r['dd'], 1),
            "pos_rate": 100.0 if r['sharpe'] > 0 else 0,
            "n_folds": 1,
        }

    # WF 切分回测
    print(f"\n[v61e overlay] WF mode: train={train_days}, test={test_days}, step={step_days}")
    fold_results = []
    i = start_idx
    fold_num = 0
    while i + train_days + test_days <= len(dates):
        test_s = dates[i + train_days]
        test_e = dates[min(i + train_days + test_days - 1, len(dates) - 1)]
        fold_num += 1

        print(f"\n  Fold {fold_num}: {str(test_s)[:10]} ~ {str(test_e)[:10]}")
        r = run_fold(data, test_s, test_e, p)
        if r is not None:
            print(f"    Return: {r['total']:+.2f}%, Sharpe: {r['sharpe']:+.3f}, DD: {r['dd']:.1f}%")
            fold_results.append({
                'start': str(test_s)[:10],
                'end': str(test_e)[:10],
                'total': r['total'],
                'sharpe': r['sharpe'],
                'dd': r['dd'],
            })
        else:
            print(f"    Skipped (too few data)")
        i += step_days

    if not fold_results:
        return {"total": 0, "sharpe": 0, "dd": 0, "pos_rate": 0, "n_folds": 0}

    avg_sharpe = np.mean([f['sharpe'] for f in fold_results])
    avg_total = np.mean([f['total'] for f in fold_results])
    avg_dd = np.mean([f['dd'] for f in fold_results])
    pos_rate = sum(1 for f in fold_results if f['sharpe'] > 0) / len(fold_results) * 100

    return {
        "total": round(avg_total, 2),
        "sharpe": round(avg_sharpe, 3),
        "dd": round(avg_dd, 1),
        "pos_rate": round(pos_rate, 1),
        "n_folds": len(fold_results),
        "folds": fold_results,
    }


if __name__ == "__main__":
    result = run_wf_overlay()
    print(f"\n{'='*60}")
    print(f"v61e WF Summary")
    print(f"{'='*60}")
    print(f"  Total Return: {result['total']:+.2f}%")
    print(f"  Sharpe Ratio: {result['sharpe']:+.3f}")
    print(f"  Max Drawdown: {result['dd']:.1f}%")
    print(f"  Positive Folds: {result['pos_rate']:.1f}% ({result['n_folds']} folds)")
    print(f"{'='*60}")

    with open(RESULT_FILE, 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\nResults saved to {RESULT_FILE}")
