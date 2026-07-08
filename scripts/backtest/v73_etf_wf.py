#!/usr/bin/env python3
"""
v73: ETF动量轮动 — 独立WF回测脚本（overlay模式）
===============================================
逻辑：每天计算所有ETF的25日动量得分，选得分最高的1只买入
      指数>MA20才开仓（趋势确认）
      空仓时持有现金

与v61b overlay类似的独立脚本，不依赖标准stock面板框架。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import sqlite3
import numpy as np
import pandas as pd

# ── 默认参数 ──
DEFAULT_PARAMS = {
    'STOP_LOSS': -0.05,
    'TAKE_PROFIT': 0.15,
    'HOLD_DAYS_MAX': 10,
    'MAX_HOLDINGS': 1,
    'MAX_DAILY_BUY': 1,
    'MAX_POSITION': 1.0,
    'MOM_WINDOW': 25,        # 动量计算窗口（天）
    'MOM_MIN_SLOPE': 0.0,    # 最低动量得分（过滤下跌趋势）
    'INDEX_MA_ENABLED': True,
    'INDEX_MA_PERIOD': 20,
}


def load_etf_data():
    """加载ETF收盘价 + 上证指数"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(script_dir))
    db_path = os.path.join(project_root, 'data', 'quant_stocks.db')
    conn = sqlite3.connect(db_path, timeout=30)

    # ETF数据
    etf_df = pd.read_sql(
        "SELECT code, date, close FROM index_kline "
        "WHERE code LIKE 'sh5%' OR code LIKE 'sz15%' OR code LIKE 'sz51%' "
        "ORDER BY code, date",
        conn
    )
    etf_df['date'] = pd.to_datetime(etf_df['date'])

    # 只保留有足够数据的ETF（>100天）
    etf_counts = etf_df.groupby('code').size()
    valid_etfs = etf_counts[etf_counts > 100].index.tolist()
    etf_df = etf_df[etf_df['code'].isin(valid_etfs)]

    # pivot成面板
    etf_close = etf_df.pivot(index='date', columns='code', values='close')
    etf_close = etf_close.dropna(how='all')

    # 上证指数（在daily_kline表中）
    idx_df = pd.read_sql(
        "SELECT date, close FROM daily_kline WHERE code='sh000001' ORDER BY date",
        conn
    )
    conn.close()

    idx_df['date'] = pd.to_datetime(idx_df['date'])
    idx_df = idx_df.set_index('date')
    index_close = idx_df['close']

    return etf_close, index_close


def calc_momentum(etf_close, window=25):
    """计算每只ETF的动量得分（简单收益率）"""
    return etf_close.pct_change(window)


def run_fold(etf_close, index_close, test_start, test_end, params):
    """
    在指定测试区间内模拟交易

    Returns:
        dict: {total_ret, sharpe, max_dd, nav_series} or None
    """
    p = {**DEFAULT_PARAMS, **params}

    # 动量面板
    mom = calc_momentum(etf_close, p['MOM_WINDOW'])

    # 指数MA
    index_ma = index_close.rolling(p['INDEX_MA_PERIOD']).mean()
    index_above = (index_close > index_ma).astype(float)

    # 测试日期范围
    all_dates = sorted(etf_close.index)
    test_dates = [d for d in all_dates if test_start <= d <= test_end]
    if len(test_dates) < 10:
        return None

    # 模拟交易
    cash = 100000.0
    holdings = {}  # {code: {shares, cost, buy_date, hold_days}}
    nav_series = []

    for date in test_dates:
        # 更新hold_days
        for code in holdings:
            holdings[code]['hold_days'] = holdings[code].get('hold_days', 0) + 1

        # 当日价格
        if date not in etf_close.index:
            nav = cash + sum(h['shares'] * h.get('last_price', h['cost']) for h in holdings.values())
            nav_series.append((date, nav))
            continue

        prices = etf_close.loc[date].dropna()

        # ── 风控：止损/止盈/超时 ──
        to_sell = []
        for code, h in list(holdings.items()):
            if code not in prices.index:
                continue
            p_now = prices[code]
            h['last_price'] = p_now
            pnl = (p_now - h['cost']) / h['cost']

            reason = None
            if pnl <= p['STOP_LOSS']:
                reason = 'stop_loss'
            elif pnl >= p['TAKE_PROFIT']:
                reason = 'take_profit'
            elif h['hold_days'] >= p['HOLD_DAYS_MAX']:
                reason = 'hold_days_max'

            if reason:
                cash += h['shares'] * p_now
                to_sell.append(code)

        for code in to_sell:
            del holdings[code]

        # ── 买入逻辑 ──
        # 指数>MA20才开仓
        if p['INDEX_MA_ENABLED'] and date in index_above.index:
            if index_above.loc[date] == 0:
                # 指数在MA20下方，清仓
                for code in list(holdings.keys()):
                    if code in prices.index:
                        cash += holdings[code]['shares'] * prices[code]
                holdings.clear()
                nav = cash
                nav_series.append((date, nav))
                continue

        # 选得分最高的ETF
        if date in mom.index:
            scores = mom.loc[date].dropna()
            scores = scores[scores > p['MOM_MIN_SLOPE']]

            if len(scores) > 0:
                best = scores.sort_values(ascending=False).index[0]

                # 如果已持有最好的，不操作
                if best not in holdings:
                    # 清仓旧的（只持1只）
                    for code in list(holdings.keys()):
                        if code in prices.index:
                            cash += holdings[code]['shares'] * prices[code]
                    holdings.clear()

                    # 买入
                    if cash > 0 and best in prices.index:
                        price = prices[best]
                        shares = int(cash / price / 100) * 100  # ETF按100份整手
                        if shares > 0:
                            cost = shares * price
                            cash -= cost
                            holdings[best] = {
                                'shares': shares,
                                'cost': price,
                                'buy_date': date,
                                'hold_days': 0,
                                'last_price': price,
                            }

        # 计算NAV
        nav = cash + sum(h['shares'] * h.get('last_price', h['cost']) for h in holdings.values())
        nav_series.append((date, nav))

    if len(nav_series) < 10:
        return None

    nav = pd.Series([n[1] for n in nav_series], index=[n[0] for n in nav_series])
    total_ret = (nav.iloc[-1] / nav.iloc[0] - 1) * 100

    # 夏普
    daily_ret = nav.pct_change().dropna()
    if daily_ret.std() > 0:
        sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252)
    else:
        sharpe = 0

    # 最大回撤
    max_dd = ((nav / nav.cummax()) - 1).min() * 100

    return {
        'total': total_ret,
        'sharpe': sharpe,
        'dd': max_dd,
        'nav': nav,
    }


def run_wf_overlay(train_days=252, test_days=126, step_days=63,
                   start_date='2021-01-01', end_date='2026-06-24',
                   params=None, full=False):
    """
    标准WF回测接口，供wf_runner调用

    full=False (默认): WF切分回测
    full=True: 全量连续回测

    返回标准结果格式（DataFrame）
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    print(f"[v73 overlay] 加载ETF数据...")
    etf_close, index_close = load_etf_data()
    print(f"  ETF: {etf_close.shape[1]}只, {etf_close.shape[0]}天")
    print(f"  指数: {len(index_close)}天")

    all_dates = sorted(etf_close.index)
    start_idx = next((i for i, d in enumerate(all_dates) if d >= pd.Timestamp(start_date)), 0)

    if full:
        # ── 全量连续回测 ──
        print(f"[v73 overlay] 全量回测模式, {start_date} ~ {end_date}")
        test_s = all_dates[start_idx]
        test_e = all_dates[-1]
        r = run_fold(etf_close, index_close, test_s, test_e, p)
        if r is None:
            return pd.DataFrame({'test_ret': [0], 'test_sharpe': [0], 'test_dd': [0]})

        # 分年统计
        nav = r['nav']
        print(f"\n--- 分年统计 ---")
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
            print(f"  {year}: 收益={yr:+.1f}%, 夏普={ys:+.3f}, 回撤={ydd:.1f}%")

        return pd.DataFrame({
            'test_ret': [r['total']],
            'test_sharpe': [r['sharpe']],
            'test_dd': [r['dd']],
            'test_days': [len(nav)],
        })

    # ── WF 切分回测 ──
    fold_results = []
    i = start_idx
    fold_num = 0
    while i + train_days + test_days <= len(all_dates):
        test_s = all_dates[i + train_days]
        test_e = all_dates[min(i + train_days + test_days - 1, len(all_dates) - 1)]
        r = run_fold(etf_close, index_close, test_s, test_e, p)
        if r:
            fold_results.append({
                'fold': fold_num,
                'test_ret': r['total'],
                'test_sharpe': r['sharpe'],
                'test_dd': r['dd'],
                'test_days': test_days,
            })
            print(f"  Fold {fold_num}: ret={r['total']:+.2f}%, sharpe={r['sharpe']:+.3f}, dd={r['dd']:.1f}%")
        else:
            print(f"  Fold {fold_num}: [ skipped ]")
        fold_num += 1
        i += step_days

    if not fold_results:
        return pd.DataFrame({'test_ret': [], 'test_sharpe': [], 'test_dd': []})

    df = pd.DataFrame(fold_results)
    avg_sharpe = df['test_sharpe'].mean()
    avg_ret = df['test_ret'].mean()
    pos = (df['test_sharpe'] > 0).sum()
    total = len(df)
    print(f"\n  平均: ret={avg_ret:+.2f}%, sharpe={avg_sharpe:+.3f}, 正fold {pos}/{total} ({pos/total*100:.0f}%)")

    return df


def run_signal(account_id, date, params, state, panels):
    """
    v73 信号生成函数（供account_runner overlay调用）

    由于v73交易ETF而非个股，panels参数被忽略，
    直接从DB加载ETF数据。
    """
    p = {**DEFAULT_PARAMS, **(params or {})}

    # 加载数据
    etf_close, index_close = load_etf_data()

    # 指数MA过滤
    if p['INDEX_MA_ENABLED']:
        index_ma = index_close.rolling(p['INDEX_MA_PERIOD']).mean()
        if date in index_close.index and date in index_ma.index:
            if index_close.loc[date] <= index_ma.loc[date]:
                return {'sell_plan': [], 'buy_plan': [], 'hold_plan': []}

    # 计算动量
    mom = calc_momentum(etf_close, p['MOM_WINDOW'])
    if date not in mom.index:
        return {'sell_plan': [], 'buy_plan': [], 'hold_plan': []}

    scores = mom.loc[date].dropna()
    scores = scores[scores > p['MOM_MIN_SLOPE']]
    if scores.empty:
        return {'sell_plan': [], 'buy_plan': [], 'hold_plan': []}

    best = scores.sort_values(ascending=False).index[0]
    best_score = scores.iloc[0]

    # 返回格式与account_runner一致
    return {
        'sell_plan': [],
        'buy_plan': [{'code': best, 'score': best_score, 'shares': 0, 'price': 0}],
        'hold_plan': [],
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='v73 ETF动量轮动 WF回测')
    parser.add_argument('--full', action='store_true', help='全量回测')
    parser.add_argument('--start', default='2021-01-01', help='起始日期')
    parser.add_argument('--end', default='2026-06-24', help='结束日期')
    parser.add_argument('--train', type=int, default=252, help='训练窗口')
    parser.add_argument('--test', type=int, default=126, help='测试窗口')
    parser.add_argument('--step', type=int, default=63, help='步进')
    args = parser.parse_args()

    result = run_wf_overlay(
        train_days=args.train,
        test_days=args.test,
        step_days=args.step,
        start_date=args.start,
        end_date=args.end,
        full=args.full,
    )
    print(f"\n结果:\n{result}")
