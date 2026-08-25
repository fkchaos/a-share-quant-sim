#coding:gbk
"""
v61c_qmt.py - v61c QMT实盘版
============================
换手率+小市值策略，适配QMT数据源。

与原版区别：
- 原版从DB读float_shares计算换手率
- QMT版用成交额/收盘价估算成交股数，用成交额作为流动性代理

Python 3.6.8兼容。
"""

import numpy as np
import pandas as pd

# 策略参数（与原版一致）
MAX_HOLDINGS = 5
REBALANCE_DAYS = 5
STOP_LOSS = -0.08
TAKE_PROFIT = 0.25
HOLD_DAYS_MAX = 5
SELL_OUT_OF = 15

class G():
    pass
g = G()
g.initialized = False
g.holdings = {}  # {code: {'shares': n, 'cost': p, 'entry_day': d}}
g.trade_log = []
g.last_rebalance_day = -999
g.day_count = 0


def init(C):
    """QMT初始化"""
    g.stock_pool = C.get_stock_list_in_sector('沪深A股')
    g.initialized = True
    print('[INIT] v61c_qmt, pool=%d stocks' % len(g.stock_pool))


def handlebar(C):
    """QMT主循环"""
    if not g.initialized:
        return

    # 只在最后一根K线执行
    if not C.is_last_bar():
        return

    g.day_count += 1

    # 1. 获取当前持仓
    from qmt_adapter.trading import QmtAccount
    acct = QmtAccount(C, 'testS', 'stock')
    cash = acct.get_cash()
    qt_holdings = acct.get_holdings()

    # 同步本地持仓
    for code in list(g.holdings.keys()):
        if code not in qt_holdings:
            del g.holdings[code]

    # 2. 获取行情数据（最近60根日K）
    stock_list = list(qt_holdings.keys()) + g.stock_pool[:200]  # 取部分股票池
    stock_list = list(set(stock_list))[:300]  # QMT限制

    data = C.get_market_data_ex(
        ['open', 'high', 'low', 'close', 'volume', 'amount'],
        stock_list,
        period='1d',
        count=60,
        subscribe=False,
    )

    if not data:
        return

    # 3. 风控检查（止损/止盈/到期）
    sell_codes = []
    for code, info in list(g.holdings.items()):
        if code not in data or len(data[code]) == 0:
            continue
        current_price = data[code]['close'].values[-1]
        cost = info.get('cost', 0)
        if cost <= 0:
            continue

        pnl = (current_price - cost) / cost

        # 止损
        if pnl <= STOP_LOSS:
            sell_codes.append((code, 'STOP_LOSS'))
            continue

        # 止盈
        if pnl >= TAKE_PROFIT:
            sell_codes.append((code, 'TAKE_PROFIT'))
            continue

        # 持仓天数
        hold_days = g.day_count - info.get('entry_day', g.day_count)
        if hold_days >= HOLD_DAYS_MAX:
            # v61c: 到期检查是否还在Top15
            sell_codes.append((code, 'HOLD_DAYS'))

    # 执行卖出
    for code, reason in sell_codes:
        if code in qt_holdings:
            acct.sell_all(code, reason=reason)
            if code in g.holdings:
                del g.holdings[code]
            print('[SELL] %s %s %s' % (C.get_bar_timetag(C.barpos), code, reason))

    # 4. 调仓日选股
    days_since_rebalance = g.day_count - g.last_rebalance_day
    if days_since_rebalance < REBALANCE_DAYS:
        return

    # 计算因子（换手率+小市值）
    scores = calc_scores(data, g.stock_pool[:200])

    # 选股：选前N只，排除已持仓
    n = MAX_HOLDINGS - len(g.holdings)
    if n <= 0:
        return

    candidates = scores.head(MAX_HOLDINGS * 2).index.tolist()
    held = set(g.holdings.keys())
    buy_list = [c for c in candidates if c not in held][:n]

    # 执行买入
    if buy_list:
        available = cash * 0.95  # 留5%余量
        per_stock = available / len(buy_list)

        for code in buy_list:
            if code not in data or len(data[code]) == 0:
                continue
            price = data[code]['close'].values[-1]
            if price <= 0:
                continue

            shares = int(per_stock / price / 100) * 100
            if shares <= 0:
                continue

            acct.buy(code, shares, price, reason='v61c')
            g.holdings[code] = {
                'shares': shares,
                'cost': price,
                'entry_day': g.day_count,
            }
            print('[BUY] %s %s %d @%.2f' % (C.get_bar_timetag(C.barpos), code, shares, price))

    g.last_rebalance_day = g.day_count


def calc_scores(data, stock_pool):
    """计算换手率+小市值因子评分"""
    scores = pd.Series(0.0, index=stock_pool)

    for code in stock_pool:
        if code not in data or len(data[code]) < 10:
            continue

        df = data[code]
        close = df['close'].values
        volume = df['volume'].values  # 手
        amount = df['amount'].values  # 元

        # 换手率代理 = 成交额 / (收盘价 * 100) 估算流通股
        # 实际用成交额作为流动性代理
        avg_amount_5 = np.mean(amount[-5:]) if len(amount) >= 5 else np.nan
        avg_amount_20 = np.mean(amount[-20:]) if len(amount) >= 20 else np.nan

        # 市值代理 = 收盘价 * 成交量（简化）
        market_cap = close[-1] * volume[-1] if volume[-1] > 0 else np.nan

        # 低换手（低成交额）= 高分，小市值 = 高分
        if not np.isnan(avg_amount_5) and not np.isnan(market_cap):
            scores[code] = -avg_amount_5 * 0.5 - market_cap * 0.5

    # rank评分
    valid = scores[scores != 0].dropna()
    if len(valid) > 50:
        scores = valid.rank(ascending=True, pct=True)
    else:
        scores = pd.Series(0.0, index=stock_pool)

    return scores.sort_values(ascending=False)
