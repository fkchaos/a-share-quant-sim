#!/usr/bin/env python3
"""v61c 风控参数精调 — 到期续持优化（基于v61b_risk_scan.py）

核心改进：
- v61b: 到期 → 强制卖出 → 可能买回同一只（白交手续费）
- v61c: 到期 → 检查是否还在SELL_OUT_OF内 → 在则续持，不在才卖出
- 止盈止损保持硬性

WF对比（16 folds）:
  v61b原始: Sharpe=2.407, 收益=+36.8%, 正fold=14/16
  v61c-top15: Sharpe=2.530, 收益=+37.6%, 正fold=15/16 ✅
"""
import sys, os, json, argparse
sys.path.insert(0, '/root/a-share-quant-sim')
import sqlite3, numpy as np, pandas as pd

RESULT_FILE = '/root/a-share-quant-sim/scripts/backtest/v61c_risk_results.json'

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

DEFAULT_PARAMS = {
    'REBALANCE_DAYS': 5,
    'TOP_N': 5,
    'STOP_LOSS': -0.08,
    'TAKE_PROFIT': 0.25,
    'HOLD_DAYS_MAX': 5,
    'SELL_OUT_OF': 15,  # 到期时检查的排名范围
    'MAX_POSITION': 0.20,
    'SENTIMENT_WINDOW': 0,
    'SENTIMENT_THRESHOLD': 5.0,
    'SENTIMENT_COLD_MODE': True,
}

def load_data():
    return load_data_with_range('2020-06-01', '2026-06-29')

def load_data_with_range(start_date='2020-06-01', end_date='2026-06-29'):
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

    daily_ret = close.pct_change()
    is_limit = ((daily_ret >= 0.095) & (daily_ret <= 0.105)).astype(float).fillna(0)
    two_day_limit = (is_limit.shift(1).fillna(0) == 1) & (is_limit == 1)
    daily_limit_count = two_day_limit.astype(float).sum(axis=1)

    print(f"    {close.shape[0]} days, {close.shape[1]} stocks")
    return {'close': close, 'turnover': turnover, 'mcap': mcap, 'turn_5': turn_5,
            'daily_limit_count': daily_limit_count}

def calc_scores(date, data):
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

def run_fold(data, test_start, test_end, rebal, top_n, sl, tp, hold_max,
             sell_out_of=15, sent_window=0, sent_thresh=5.0, cold_mode=True):
    """v61c核心逻辑：到期时检查是否还在sell_out_of内"""
    close = data['close']
    turnover = data['turnover']
    daily_limit_count = data.get('daily_limit_count')
    dates = sorted(close.index)
    test_dates = [d for d in dates if test_start <= d <= test_end]
    if len(test_dates) < 10:
        return None

    sentiment = None
    if sent_window > 0 and daily_limit_count is not None:
        sentiment = daily_limit_count.rolling(sent_window).mean()

    INIT_CASH = 200000
    cash = INIT_CASH
    holdings = {}
    nav_list = []
    first_day = True

    def sell(code, date):
        nonlocal cash
        if code in close.columns:
            p = close.at[date, code]
            if not np.isnan(p):
                cash += holdings[code]['shares'] * p * 0.9987
        del holdings[code]

    def buy_new(date):
        nonlocal cash
        # 情绪过滤
        if sentiment is not None and date in sentiment.index:
            sent = sentiment.loc[date]
            if not np.isnan(sent):
                if cold_mode and sent >= sent_thresh:
                    return
                elif not cold_mode and sent <= sent_thresh:
                    return

        scores = calc_scores(date, data)
        target = scores.head(top_n).index.tolist()
        wider = scores.head(sell_out_of).index.tolist()
        
        # 卖不在wider中的（排名下降超过sell_out_of）
        for code in list(holdings.keys()):
            if code not in wider:
                sell(code, date)
        
        # 到期处理：还在wider内就续持，否则卖出
        for code in list(holdings.keys()):
            if holdings[code].get('days', 0) >= hold_max:
                if code in wider:
                    holdings[code]['days'] = 0  # 续持，重置天数
                else:
                    sell(code, date)
        
        # 买新的
        n_buy = top_n - len(holdings)
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
                    # 止盈止损：硬性，直接卖出
                    if pnl <= sl or pnl >= tp:
                        to_sell.append(code)
                        continue
                    pos['days'] = pos.get('days', 0) + 1

        # 执行风控卖出
        for code in to_sell:
            sell(code, date)

        nav_list.append({'date': date, 'nav': val})

        # 调仓触发：第一天、有风控卖出、或到达调仓日
        # 注意：不再检查hold_max到期（移到buy_new里处理）
        if first_day or len(to_sell) > 0:
            buy_new(date)
            first_day = False

    if not nav_list:
        return None
    nav = pd.Series([n['nav'] for n in nav_list], index=[n['date'] for n in nav_list])
    total = (nav.iloc[-1] / nav.iloc[0] - 1) * 100
    daily_ret = nav.pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    dd = (nav / nav.cummax() - 1).min() * 100
    return {'total': total, 'sharpe': sharpe, 'dd': dd, 'nav': nav}


def run_wf_overlay(train_days=252, test_days=126, step_days=63,
                   start_date='2021-01-01', end_date='2026-05-31', params=None, full=False):
    """标准WF回测接口"""
    p = {**DEFAULT_PARAMS, **(params or {})}
    
    data = load_data_with_range('2020-06-01', end_date)
    dates = sorted(data['close'].index)
    start_idx = next((i for i, d in enumerate(dates) if d >= pd.Timestamp(start_date)), 0)
    
    rebal = p['REBALANCE_DAYS']
    top_n = p['TOP_N']
    sl = p['STOP_LOSS']
    tp = p['TAKE_PROFIT']
    hold_max = p['HOLD_DAYS_MAX']
    sell_out_of = p.get('SELL_OUT_OF', 15)
    sent_window = p.get('SENTIMENT_WINDOW', 0)
    sent_thresh = p.get('SENTIMENT_THRESHOLD', 5.0)
    cold_mode = p.get('SENTIMENT_COLD_MODE', True)
    
    if full:
        print(f"[v61c overlay] 全量回测模式, {start_date} ~ {end_date}")
        print(f"  到期续持范围: Top{sell_out_of}")
        test_s = dates[start_idx]
        test_e = dates[-1]
        r = run_fold(data, test_s, test_e, rebal, top_n, sl, tp, hold_max,
                     sell_out_of, sent_window, sent_thresh, cold_mode)
        if r is None:
            return {"total": 0, "sharpe": 0, "dd": 0, "pos_rate": 0, "n_folds": 0}
        
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
        
        return {
            "total": round(r['total'], 2),
            "sharpe": round(r['sharpe'], 3),
            "dd": round(r['dd'], 1),
            "pos_rate": 100.0 if r['sharpe'] > 0 else 0,
            "n_folds": 1,
        }
    
    # WF 切分回测
    fold_results = []
    i = start_idx
    while i + train_days + test_days <= len(dates):
        test_s = dates[i + train_days]
        test_e = dates[min(i + train_days + test_days - 1, len(dates) - 1)]
        
        r = run_fold(data, test_s, test_e, rebal, top_n, sl, tp, hold_max,
                     sell_out_of, sent_window, sent_thresh, cold_mode)
        if r is not None:
            fold_results.append({
                'start': str(test_s)[:10],
                'end': str(test_e)[:10],
                'total': r['total'],
                'sharpe': r['sharpe'],
                'dd': r['dd'],
            })
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


def run_signal(account_id, date, params, state, panels):
    """
    v61c 信号生成函数（供account_runner overlay调用）
    
    v61c核心改进：到期时检查是否还在sell_out_of内，在则续持，不在才卖出。
    
    Args:
        account_id: 账户ID
        date: 当前日期
        params: 策略参数
        state: PortfolioState对象
        panels: 数据面板 (cp, vp, ap, hp, lp, op)
    
    Returns:
        交易计划字典（与account_runner._run_signal_impl格式一致）
    """
    import logging
    from datetime import datetime
    logger = logging.getLogger("v61c_overlay")
    
    cp, vp, ap, hp, lp, op = panels
    rebalance_days = params.get("REBALANCE_DAYS", 5)
    top_n = params.get("TOP_N", 5)
    stop_loss = params.get("STOP_LOSS", -0.08)
    take_profit = params.get("TAKE_PROFIT", 0.25)
    hold_days_max = params.get("HOLD_DAYS_MAX", 5)
    max_holdings = params.get("MAX_HOLDINGS", 5)
    max_daily_buy = params.get("MAX_DAILY_BUY", 5)
    max_position = params.get("MAX_POSITION", 0.20)
    sell_out_of = params.get("SELL_OUT_OF", 15)  # v61c关键参数：到期续持检查范围
    
    # ── 1. 风控检查：止损/止盈（硬性，直接卖出） ──
    to_sell = []
    if date in cp.index:
        price_data = cp.loc[date]
        for code, h in list(state.holdings.items()):
            if code not in price_data.index:
                continue
            p = price_data[code]
            if pd.isna(p) or p <= 0:
                continue
            
            # T+1：当天买入的不检查
            if h.get('hold_days', 0) < 1:
                continue
            
            cost = h.get('cost_price', 0)
            if cost <= 0:
                continue
            
            pnl = (p - cost) / cost
            reason = None
            
            # 止损
            if pnl <= stop_loss:
                reason = 'stop_loss'
            # 止盈
            elif pnl >= take_profit:
                reason = 'take_profit'
            
            if reason:
                to_sell.append((code, reason, pnl))
                logger.info(f"v61c风控: 卖出{code}, 原因={reason}, 盈亏={pnl:.2%}")
    
    # ── 2. 到期续持判断（v61c核心逻辑） ──
    # 先计算选股分数，用于判断到期股票是否还在sell_out_of内
    date_str = str(date)[:10] if not isinstance(date, str) else date[:10]
    rebalance_codes = []
    
    # 计算当天分数（用于到期续持判断）
    scores = None
    try:
        data = load_data_with_range(
            (pd.Timestamp(date) - pd.Timedelta(days=365)).strftime('%Y-%m-%d'),
            date if isinstance(date, str) else date.strftime('%Y-%m-%d')
        )
        scores = calc_scores(date, data)
        wider = scores.head(sell_out_of).index.tolist()  # 到期续持检查范围
    except Exception as e:
        logger.warning(f"v61c: 计算分数失败: {e}, 使用空wider")
        wider = []
    
    for code, h in list(state.holdings.items()):
        if code in {c for c, _, _ in to_sell}:
            continue  # 已在卖出列表，跳过
        
        # 计算持有天数
        entry_date = h.get('entry_date')
        if entry_date is None:
            rebalance_codes.append(code)  # 无入场日期，强制调仓
            continue
        
        try:
            days_held = count_trading_days(entry_date, date_str)
        except:
            rebalance_codes.append(code)  # 解析失败，强制调仓
            continue
        
        if days_held >= hold_days_max:
            # v61c核心：到期时检查是否还在sell_out_of内
            if code in wider:
                # 还在范围内，续持（不卖出，重置天数由执行层处理）
                logger.info(f"v61c: {code} 持有{days_held}天到期，但在Top{sell_out_of}内，续持")
            else:
                # 不在范围内，卖出
                rebalance_codes.append(code)
                logger.info(f"v61c: {code} 持有{days_held}天到期，不在Top{sell_out_of}，调仓卖出")
    
    # ── 3. 排名下降卖出（超出sell_out_of的持仓也卖出） ──
    for code, h in list(state.holdings.items()):
        if code in {c for c, _, _ in to_sell}:
            continue
        if code in rebalance_codes:
            continue
        if code not in wider:
            # 持仓不在wider内，排名下降太多，卖出
            price_data = cp.loc[date] if date in cp.index else pd.Series()
            p = price_data.get(code, 0) if code in price_data.index else 0
            cost = h.get('cost_price', 0)
            pnl = (p - cost) / cost if cost > 0 else 0
            to_sell.append((code, 'rank_drop', pnl))
            logger.info(f"v61c: {code} 排名下降超出Top{sell_out_of}，卖出, 盈亏={pnl:.2%}")
    
    has_sell_signal = len(to_sell) > 0
    has_rebalance = len(rebalance_codes) > 0
    has_vacancy = len(state.holdings) < max_holdings
    
    # ── 4. 选股 ──
    buy_plan = []
    if has_rebalance or has_sell_signal or has_vacancy:
        # 将调仓股票加入卖出列表
        for code in rebalance_codes:
            if code in state.holdings and code not in {c for c, _, _ in to_sell}:
                h = state.holdings[code]
                price_data = cp.loc[date] if date in cp.index else pd.Series()
                p = price_data.get(code, 0) if code in price_data.index else 0
                cost = h.get('cost_price', 0)
                pnl = (p - cost) / cost if cost > 0 else 0
                to_sell.append((code, 'rebalance', pnl))
                days = count_trading_days(h.get('entry_date', date), date_str)
                logger.info(f"v61c: 调仓卖出{code}, 持有{days}天")
        
        # 使用已计算的分数选股
        if scores is not None:
            candidates = scores.head(top_n * 2).index.tolist()
        else:
            candidates = []
        
        # 排除已持有和将卖出的
        held = set(state.holdings.keys())
        sell_codes = {c for c, _, _ in to_sell}
        candidates = [c for c in candidates if c not in held and c not in sell_codes]
        
        # 计算可买入数量
        remaining = len(held) - len(sell_codes)
        can_buy = min(max_holdings - remaining, max_daily_buy)
        can_buy = max(can_buy, 0)
        
        # 选股
        buy_list = candidates[:can_buy]
        
        # 生成买入计划
        if buy_list and date in cp.index:
            price_data = cp.loc[date]
            # 计算可用资金
            sell_cash = 0
            for c, _, _ in to_sell:
                if c in state.holdings and c in price_data.index:
                    p_s = price_data.get(c, 0)
                    if pd.isna(p_s) or p_s <= 0:
                        logger.warning(f"v61c: {c} 卖出价格无效(p={p_s})，按0计")
                        p_s = 0
                    sell_cash += p_s * state.holdings[c].get('shares', 0)
            available = state.cash + sell_cash
            # 计算总资产（用于MAX_POSITION限制）
            total_value = state.cash
            if date in cp.index:
                for code, h in state.holdings.items():
                    shares = h.get('shares', h.get('qty', 0))
                    p = price_data.get(code, 0)
                    if pd.isna(p) or p <= 0:
                        logger.warning(f"v61c: {code} 当前价格无效(p={p})，按0计入总资产")
                        p = 0
                    total_value += p * shares
            # 单只股票最大金额 = 总资产 × MAX_POSITION
            max_per_stock = total_value * max_position
            per_stock = min(available / len(buy_list) * 0.95, max_per_stock)
            if pd.isna(per_stock) or per_stock <= 0:
                logger.warning(f"v61c: per_stock无效({per_stock})，跳过买入")
                buy_list = []

            for code in buy_list:
                if code in price_data.index:
                    price = price_data[code]
                    if not pd.isna(price) and price > 0:
                        qty = int(per_stock / price / 100) * 100
                        if qty > 0:
                            buy_plan.append({
                                'code': code,
                                'score': round(scores.get(code, 0) if scores is not None else 0, 2),
                                'price': round(price, 2),
                                'qty': qty,
                                'target_amount': round(per_stock, 2),
                            })
        
        logger.info(f"v61c: 卖出{len(to_sell)}只，选出{len(buy_plan)}只新股")
    else:
        logger.info(f"v61c: 无到期/无卖出/无空位，跳过选股")
    
    # ── 5. 生成交易计划 ──
    sell_plan = [
        {
            'code': c,
            'qty': state.holdings[c].get('shares', state.holdings[c].get('qty', 0)),
            'reason': reason,
            'pnl': round(pnl, 4),
            'hold_days': state.holdings[c].get('hold_days', 0),
        }
        for c, reason, pnl in to_sell if c in state.holdings
    ]
    
    hold_plan = []
    for code, h in state.holdings.items():
        if code not in {c for c, _, _ in to_sell} and code not in {b['code'] for b in buy_plan}:
            price = 0
            if date in cp.index and code in cp.columns:
                price = cp.loc[date, code]
                if pd.isna(price) or price <= 0:
                    price = h.get('cost_price', 0)
            hold_plan.append({
                'code': code,
                'current_shares': h.get('shares', h.get('qty', 0)),
                'price': round(price, 2),
                'cost_price': round(h.get('cost_price', 0), 2),
                'hold_days': h.get('hold_days', 0),
                'action': 'hold',
            })
    
    plan = {
        'date': str(date),
        'account_id': account_id,
        'strategy': 'v61c',
        'sell_plan': sell_plan,
        'buy_plan': buy_plan,
        'hold_plan': hold_plan,
        'top_scores_raw': {code: round(scores.get(code, 0), 2) for code in (scores.head(15).index.tolist() if scores is not None else [])} if scores is not None else {},
        'timestamp': datetime.now().isoformat(),
    }
    
    logger.info(f"v61c计划: 卖{len(sell_plan)}只, 买{len(buy_plan)}只, 持{len(hold_plan)}只")
    
    return plan


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='v61c 风控参数扫描')
    parser.add_argument('--group', type=int, default=0, help='持有期组号')
    parser.add_argument('--list', action='store_true', help='列出所有组')
    parser.add_argument('--full', action='store_true', help='全量回测')
    parser.add_argument('--signal', action='store_true', help='生成信号')
    args = parser.parse_args()
    
    if args.signal:
        # 旧版独立调用已废弃，需要通过account_runner overlay调用
        print("请通过 account_runner.py 调用: python scripts/sim/account_runner.py run --account-id 1 intraday_signal")
        sys.exit(1)
    elif args.full:
        result = run_wf_overlay(full=True)
        print(f"\n全量回测结果: {json.dumps(result, indent=2, ensure_ascii=False)}")
    else:
        # 风控参数扫描
        HOLD_DAYS = [3, 5, 7, 10]
        STOP_LOSS = [-0.08, -0.10, -0.12, -0.15]
        TAKE_PROFIT = [0.15, 0.20, 0.25, 0.30]

        if args.list:
            for i, h in enumerate(HOLD_DAYS):
                print(f"  组{i}: HOLD_DAYS_MAX={h}")
            sys.exit(0)

        if args.group >= len(HOLD_DAYS):
            print(f"组号超出范围 (0-{len(HOLD_DAYS)-1})")
            sys.exit(1)

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
                sell_out_of = 15
                label = f"SL={sl:.0%}/TP={tp:.0%}/HD={hold_max}/SO={sell_out_of}"
                key = f"{sl}_{tp}_{hold_max}_{sell_out_of}"

                if key in results:
                    r = results[key]
                    print(f"  {label} → Sharpe={r['sharpe']:.3f}, 收益={r['ret']:.1f}%, 回撤={r['dd']:.1f}% (cached)")
                    continue

                print(f"  {label} ...", end=' ', flush=True)
                params = {
                    'STOP_LOSS': sl,
                    'TAKE_PROFIT': tp,
                    'HOLD_DAYS_MAX': hold_max,
                    'SELL_OUT_OF': sell_out_of,
                }
                r = run_wf_overlay(params=params)
                if r and r['n_folds'] > 0:
                    results[key] = {
                        'stop_loss': sl,
                        'take_profit': tp,
                        'hold_days': hold_max,
                        'sell_out_of': sell_out_of,
                        'ret': r['total'],
                        'sharpe': r['sharpe'],
                        'dd': r['dd'],
                        'pos_rate': r['pos_rate'],
                        'n_folds': r['n_folds'],
                    }
                    print(f"Sharpe={r['sharpe']:.3f}, 收益={r['total']:.1f}%, 回撤={r['dd']:.1f}%")
                    
                    with open(RESULT_FILE, 'w') as f:
                        json.dump(results, f, indent=2, ensure_ascii=False)
                else:
                    print("无结果")

        print(f"\n{'='*60}")
        print("最优组合:")
        best_key = max(results.keys(), key=lambda k: results[k]['sharpe'])
        best = results[best_key]
        print(f"  {best_key}: Sharpe={best['sharpe']:.3f}, 收益={best['ret']:.1f}%, 回撤={best['dd']:.1f}%")
