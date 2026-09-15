#coding:gbk
"""
qmt_adapter/strategy_skeleton.py �� QMT���ԹǼ�
================================================
init()+handlebar() ���ģ�壬�����ǵĲ����߼�����QMT���л�����

�÷�:
  1. ���Ʊ��ļ���QMT���Ա༭��
  2. �޸� strategy_module ָ����Ĳ����ļ�
  3. ��QMT�����лز��ʵ��

ע��: ���ļ�������QMT����Python 3.6�����С�
"""
#coding:gbk

import sys
import os
import numpy as np
import pandas as pd
from datetime import datetime

# ���� �������� ����������������������������������������������������������������������������������������������������������
# �޸�����ָ����Ĳ���
STRATEGY_NAME = 'v61c'           # ������
ACCOUNT_ID = 'testS'             # �ʽ��˺ţ��ز��������ֵ��
ACCOUNT_TYPE = 'stock'           # 'stock' / 'credit'
MAX_HOLDINGS = 5                 # ���ֲ���
MAX_DAILY_BUY = 5                # ÿ�����������
POSITION_SCALE = 1.0             # ��λ����
REBALANCE_DAYS = 5               # �������ڣ��죩


# ���� ȫ��״̬��QMTҪ����ȫ�ֱ�������״̬�� ��������������������������������������������
class State(object):
    pass
S = State()
S.initialized = False
S.holdings = {}                  # {code: {'shares': n, 'cost': p, 'entry_date': d}}
S.trade_log = []
S.last_rebalance_date = None
S.day_count = 0


def init(C):
    """QMT��ʼ����������������ʱ����һ�Ρ�

    Parameters
    ----------
    C : ContextInfo
        QMT�����Ķ���
    """
    # ���ý���Ʒ�֣���ͼƷ�֣�
    S.stock = C.stockcode + '.' + C.market
    S.account_id = ACCOUNT_ID
    S.account_type = ACCOUNT_TYPE

    # �������ǵĲ���ģ�飨��Ҫ�Ѳ����ļ��ŵ�QMT�ɷ��ʵ�·����
    # ��������ļ��ͱ��ļ���ͬһĿ¼������ֱ��import
    try:
        from strategy import select, get_params
        S.select = select
        S.params = get_params()
    except ImportError:
        # ��ѡ��ֱ����Ĭ�ϲ���
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
    print('[INIT] ����=%s �˺�=%s ����=%s' % (STRATEGY_NAME, S.account_id, S.params))


def handlebar(C):
    """QMT��ѭ��������ÿ��K�ߵ���һ�Ρ�

    Parameters
    ----------
    C : ContextInfo
    """
    if not S.initialized:
        return

    # ��ȡ��ǰ����
    bar_date = timetag_to_datetime(C.get_bar_timetag(C.barpos), '%Y%m%d%H%M%S')

    # �ز�ģʽ��������ʷK�ߣ�ֻ�����һ��ִ��
    # ʵ��ģʽ��ÿ���ֱʶ�ִ�У���quicktrade�������ƣ�
    if not C.is_last_bar():
        return

    # ����ʱ���飨ʵ���ã�
    now = datetime.now()
    now_time = now.strftime('%H%M%S')
    if now_time < '093000' or now_time > '150000':
        return

    # ���� 1. ��ȡ�˻���Ϣ ����
    from qmt_adapter.trading import QmtAccount
    acct = QmtAccount(C, S.account_id, S.account_type)
    cash = acct.get_cash()
    current_holdings = acct.get_holdings()

    # ���� 2. ��ؼ�飨ֹ��/ֹӯ/���ڣ� ����
    sell_codes = []
    for code, info in list(S.holdings.items()):
        # ��ȡ��ǰ�۸�
        data = C.get_market_data_ex(['close'], [code], period='1d', count=1, subscribe=False)
        if code not in data:
            continue
        current_price = data[code]['close'].values[-1]
        cost = info.get('cost', 0)
        if cost <= 0:
            continue

        pnl = (current_price - cost) / cost

        # ֹ��
        if pnl <= S.params['STOP_LOSS']:
            sell_codes.append((code, 'STOP_LOSS'))
            continue

        # ֹӯ
        if pnl >= S.params['TAKE_PROFIT']:
            sell_codes.append((code, 'TAKE_PROFIT'))
            continue

        # �ֲ�����
        entry_date = info.get('entry_date', bar_date)
        # �򻯣���bar_count��������
        hold_days = S.day_count - info.get('entry_day', S.day_count)
        if hold_days >= S.params['HOLD_DAYS_MAX']:
            sell_codes.append((code, 'HOLD_DAYS'))

    # ִ������
    for code, reason in sell_codes:
        if code in current_holdings:
            acct.sell_all(code, reason=reason, strategy_name=STRATEGY_NAME)
            if code in S.holdings:
                del S.holdings[code]
            print('[SELL] %s %s %s' % (bar_date, code, reason))

    # ���� 3. ѡ�ɣ�������ִ�У� ����
    days_since_rebalance = S.day_count
    if S.last_rebalance_date is not None:
        days_since_rebalance = S.day_count - S.last_rebalance_date

    if days_since_rebalance >= S.params.get('REBALANCE_DAYS', REBALANCE_DAYS):
        S.last_rebalance_date = S.day_count

        # ��ȡ��Ʊ�����飨�򻯣�����ͼƷ�֣�
        # ʵ��Ӧ������Ҫ��ȡ������Ʊ�ص�����
        # ������QMT��get_stock_list_in_sector��ȡ����A��
        stock_list = C.get_stock_list_in_sector('����A��')

        # ��ȡ�����������ڴ��
        close_data = C.get_market_data_ex(
            ['close', 'volume', 'amount'],
            stock_list[:200],  # QMT�������������ƣ���ȡǰ200
            period='1d',
            count=120,
            subscribe=False,
        )

        # ת��Ϊ���ǵĸ�ʽ
        from qmt_adapter.data import qmt_to_our_format
        factor_data = {}
        for code in stock_list[:200]:
            if code in close_data:
                factor_data[code] = qmt_to_our_format(close_data, code)

        # �������ǵ�ѡ���߼�
        if S.select is not None and factor_data:
            candidates = S.select(factor_data, bar_date, current_holdings, S.params)
        else:
            candidates = []

        # ���� 4. ���� ����
        available = cash * POSITION_SCALE
        to_buy = [c for c in candidates if c not in current_holdings]
        buy_count = min(len(to_buy), MAX_DAILY_buy - len([t for t in S.trade_log if t.get('date') == bar_date and t.get('action') == 'BUY']))

        for code in to_buy[:buy_count]:
            if len(current_holdings) >= MAX_HOLDINGS:
                break
            # ��ȡ�۸�
            if code not in close_data:
                continue
            price = close_data[code]['close'].values[-1]
            if price <= 0:
                continue

            # �����������
            per_stock = min(available / buy_count, S.params.get('MAX_POSITION', 0.25) * 100000)
            shares = int(per_stock / price / 100) * 100
            if shares < 100:
                continue

            # �µ�
            success = acct.buy(code, shares, reason='BUY', strategy_name=STRATEGY_NAME)
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
