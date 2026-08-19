# -*- coding: utf-8 -*-
"""
qmt_adapter/strategy_skeleton.py — QMT策略骨架
================================================
init()+handlebar() 入口模板，把我们的策略逻辑接入QMT运行环境。

用法:
  1. 复制本文件到QMT策略编辑器
  2. 修改 strategy_module 指向你的策略文件
  3. 在QMT中运行回测或实盘

注意: 本文件运行在QMT内置Python 3.6环境中。
"""
#coding:gbk

import sys
import os
import numpy as np
import pandas as pd
from datetime import datetime

# ── 策略配置 ─────────────────────────────────────────────────────
# 修改这里指向你的策略
STRATEGY_NAME = 'v61c'           # 策略名
ACCOUNT_ID = 'testS'             # 资金账号（回测可填任意值）
ACCOUNT_TYPE = 'stock'           # 'stock' / 'credit'
MAX_HOLDINGS = 5                 # 最大持仓数
MAX_DAILY_BUY = 5                # 每日最大买入数
POSITION_SCALE = 1.0             # 仓位比例
REBALANCE_DAYS = 5               # 调仓周期（天）


# ── 全局状态（QMT要求用全局变量保存状态） ──────────────────────
class State(object):
    pass
S = State()
S.initialized = False
S.holdings = {}                  # {code: {'shares': n, 'cost': p, 'entry_date': d}}
S.trade_log = []
S.last_rebalance_date = None
S.day_count = 0


def init(C):
    """QMT初始化函数。策略启动时调用一次。

    Parameters
    ----------
    C : ContextInfo
        QMT上下文对象
    """
    # 设置交易品种（主图品种）
    S.stock = C.stockcode + '.' + C.market
    S.account_id = ACCOUNT_ID
    S.account_type = ACCOUNT_TYPE

    # 导入我们的策略模块（需要把策略文件放到QMT可访问的路径）
    # 如果策略文件和本文件在同一目录，可以直接import
    try:
        from strategy import select, get_params
        S.select = select
        S.params = get_params()
    except ImportError:
        # 备选：直接用默认参数
        S.select = None
        S.params = {
            'STOP_LOSS': -0.08,
            'TAKE_PROFIT': 0.25,
            'HOLD_DAYS_MAX': 5 if STRATEGY_NAME.startswith('v61') else 20,
            'MAX_DAILY_BUY': MAX_DAILY_BUY,
            'MAX_POSITION': 0.25,
            'MAX_HOLDINGS': MAX_HOLDINGS,
        }

    S.initialized = True
    print('[INIT] 策略=%s 账号=%s 参数=%s' % (STRATEGY_NAME, S.account_id, S.params))


def handlebar(C):
    """QMT主循环函数。每根K线调用一次。

    Parameters
    ----------
    C : ContextInfo
    """
    if not S.initialized:
        return

    # 获取当前日期
    bar_date = timetag_to_datetime(C.get_bar_timetag(C.barpos), '%Y%m%d%H%M%S')

    # 回测模式：跳过历史K线，只在最后一根执行
    # 实盘模式：每个分笔都执行（由quicktrade参数控制）
    if not C.is_last_bar():
        return

    # 交易时间检查（实盘用）
    now = datetime.now()
    now_time = now.strftime('%H%M%S')
    if now_time < '093000' or now_time > '150000':
        return

    # ── 1. 获取账户信息 ──
    from qmt_adapter.trading import QmtAccount
    acct = QmtAccount(C, S.account_id, S.account_type)
    cash = acct.get_cash()
    current_holdings = acct.get_holdings()

    # ── 2. 风控检查（止损/止盈/到期） ──
    sell_codes = []
    for code, info in list(S.holdings.items()):
        # 获取当前价格
        data = C.get_market_data_ex(['close'], [code], period='1d', count=1, subscribe=False)
        if code not in data:
            continue
        current_price = data[code]['close'].values[-1]
        cost = info.get('cost', 0)
        if cost <= 0:
            continue

        pnl = (current_price - cost) / cost

        # 止损
        if pnl <= S.params['STOP_LOSS']:
            sell_codes.append((code, 'STOP_LOSS'))
            continue

        # 止盈
        if pnl >= S.params['TAKE_PROFIT']:
            sell_codes.append((code, 'TAKE_PROFIT'))
            continue

        # 持仓天数
        entry_date = info.get('entry_date', bar_date)
        # 简化：用bar_count估算天数
        hold_days = S.day_count - info.get('entry_day', S.day_count)
        if hold_days >= S.params['HOLD_DAYS_MAX']:
            sell_codes.append((code, 'HOLD_DAYS'))

    # 执行卖出
    for code, reason in sell_codes:
        if code in current_holdings:
            acct.sell_all(code, reason=reason)
            if code in S.holdings:
                del S.holdings[code]
            print('[SELL] %s %s %s' % (bar_date, code, reason))

    # ── 3. 选股（调仓日执行） ──
    days_since_rebalance = S.day_count
    if S.last_rebalance_date is not None:
        days_since_rebalance = S.day_count - S.last_rebalance_date

    if days_since_rebalance >= S.params.get('REBALANCE_DAYS', REBALANCE_DAYS):
        S.last_rebalance_date = S.day_count

        # 获取股票池行情（简化：用主图品种）
        # 实际应用中需要获取整个股票池的行情
        # 这里用QMT的get_stock_list_in_sector获取沪深A股
        stock_list = C.get_stock_list_in_sector('沪深A股')

        # 获取行情数据用于打分
        close_data = C.get_market_data_ex(
            ['close', 'volume', 'amount'],
            stock_list[:200],  # QMT可能有数量限制，先取前200
            period='1d',
            count=120,
            subscribe=False,
        )

        # 转换为我们的格式
        from qmt_adapter.data import qmt_to_our_format
        factor_data = {}
        for code in stock_list[:200]:
            if code in close_data:
                factor_data[code] = qmt_to_our_format(close_data, code)

        # 调用我们的选股逻辑
        if S.select is not None and factor_data:
            candidates = S.select(factor_data, bar_date, current_holdings, S.params)
        else:
            candidates = []

        # ── 4. 买入 ──
        available = cash * POSITION_SCALE
        to_buy = [c for c in candidates if c not in current_holdings]
        buy_count = min(len(to_buy), MAX_DAILY_buy - len([t for t in S.trade_log if t.get('date') == bar_date and t.get('action') == 'BUY']))

        for code in to_buy[:buy_count]:
            if len(current_holdings) >= MAX_HOLDINGS:
                break
            # 获取价格
            if code not in close_data:
                continue
            price = close_data[code]['close'].values[-1]
            if price <= 0:
                continue

            # 计算买入股数
            per_stock = min(available / buy_count, S.params.get('MAX_POSITION', 0.25) * 100000)
            shares = int(per_stock / price / 100) * 100
            if shares < 100:
                continue

            # 下单
            success = acct.buy(code, shares, reason=STRATEGY_NAME)
            if success:
                S.holdings[code] = {
                    'shares': shares,
                    'cost': price,
                    'entry_date': bar_date,
                    'entry_day': S.day_count,
                }
                available -= shares * price
                print('[BUY] %s %s x%d @%.2f' % (bar_date, code, shares, price))

    S.day_count += 1
