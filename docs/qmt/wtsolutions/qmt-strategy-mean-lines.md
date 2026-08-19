# 迅投QMT自带策略-双均线实盘示例PY策略-解读

> 来源: https://invest.wtsolutions.cn/posts/qmt-strategy-mean-lines/

## 首先po一下迅投QMT自带的策略

```python
# -*- coding: utf-8 -*-
#encoding:gbk
import pandas as pd
import numpy as np
import datetime

"""
示例说明：双均线实盘策略，通过计算快慢双均线，在金叉时买入，死叉时做卖出
"""

class a():
    pass
A = a()  # 创建空的类的实例 用来保存委托状态
#ContextInfo对象在盘中每次handlebar调用前都会被深拷贝, 如果调用handlebar的分笔不是k线最后分笔
#ContextInfo会被回退到深拷贝的内容 所以ContextInfo不能用来记录快速交易的信号

def init(C):
    A.stock = C.stockcode + '.' + C.market  # 品种为模型交易界面选择品种
    A.acct = account  # 账号为模型交易界面选择账号
    A.acct_type = accountType  # 账号类型为模型交易界面选择账号
    A.amount = 10000  # 单笔买入金额 触发买入信号后买入指定金额
    A.line1 = 17  # 快线周期
    A.line2 = 27  # 慢线周期
    A.waiting_list = []  # 未查到委托列表 存在未查到委托情况暂停后续报单 防止超单
    A.buy_code = 23 if A.acct_type == 'STOCK' else 33  # 买卖代码 区分股票 与 两融账号
    A.sell_code = 24 if A.acct_type == 'STOCK' else 34

    # 设置股票池 订阅品种行情
    C.set_universe([A.stock])
    print(f'双均线实盘示例{A.stock} {A.acct} {A.acct_type} 单笔买入金额{A.amount}')

def handlebar(C):
    # 跳过历史k线
    if not C.is_last_bar():
        return

    now = datetime.datetime.now()
    now_time = now.strftime('%H%M%S')

    # 跳过非交易时间
    if now_time < '093000' or now_time > "150000":
        return

    account = get_trade_detail_data(A.acct, A.acct_type, 'account')
    if len(account) == 0:
        print(f'账号{A.acct} 未登录 请检查')
        return
    account = account[0]

    available_cash = int(account.m_dAvailable)

    # 如果有未查到委托 查询委托
    if A.waiting_list:
        found_list = []
        orders = get_trade_detail_data(A.acct, A.acct_type, 'order')
        for order in orders:
            if order.m_strRemark in A.waiting_list:
                found_list.append(order.m_strRemark)
        A.waiting_list = [i for i in A.waiting_list if i not in found_list]
        if A.waiting_list:
            print(f"当前有未查到委托 {A.waiting_list} 暂停后续报单")
            return

    holdings = get_trade_detail_data(A.acct, A.acct_type, 'position')
    holdings = {i.m_strInstrumentID + '.' + i.m_strExchangeID: i.m_nCanUseVolume for i in holdings}

    # 获取行情数据
    data = C.get_history_data(max(A.line1, A.line2)+1, '1d', 'close', dividend_type='front_ratio')
    close_list = data[A.stock]
    if len(close_list) < max(A.line1, A.line2)+1:
        print('行情长度不足(新上市或最近有停牌) 跳过运行')
        return

    pre_line1 = np.mean(close_list[-A.line1-1: -1])
    pre_line2 = np.mean(close_list[-A.line2-1: -1])
    current_line1 = np.mean(close_list[-A.line1:])
    current_line2 = np.mean(close_list[-A.line2:])

    # 如果快线穿过慢线,则买入委托 当前无持仓 买入
    vol = int(A.amount / close_list[-1] / 100) * 100  # 买入数量 向下取整到100的整数倍
    if A.amount < available_cash and vol >= 100 and A.stock not in holdings and pre_line1 < pre_line2 and current_line1 > current_line2:
        # 下单开仓 ，参数说明可搜索PY交易函数 passorder
        msg = f"双均线实盘 {A.stock} 上穿均线 买入 {vol}股"
        passorder(A.buy_code, 1101, A.acct, A.stock, 14, -1, vol, '双均线实盘', 1, msg, C)
        print(msg)
        A.waiting_list.append(msg)

    # 如果快线下穿慢线,则卖出委托
    if A.stock in holdings and holdings[A.stock] > 0 and pre_line1 > pre_line2 and current_line1 < current_line2:
        msg = f"双均线实盘 {A.stock} 下穿均线 卖出 {holdings[A.stock]}股"
        passorder(A.sell_code, 1101, A.acct, A.stock, 14, -1, holdings[A.stock], '双均线实盘', 1, msg, C)
        print(msg)
        A.waiting_list.append(msg)
```

## 解读

这个代码实现了一个基于双均线策略的量化交易系统。以下是代码的主要功能：

### 1. 策略概述

**双均线策略**：该策略通过计算两个不同周期的移动平均线（快线和慢线），在快线从下方穿过慢线时（金叉）买入，在快线从上方穿过慢线时（死叉）卖出。

- **交易品种**：代码中交易的品种是用户在模型交易界面选择的股票或融资融券品种。
- **交易账号**：使用用户在模型交易界面选择的账号进行交易。

### 2. 代码结构

- **init(C) 函数**：初始化函数，设置交易品种、账号、交易金额、快慢线周期等参数，并订阅品种的行情数据。
- **handlebar(C) 函数**：处理每个K线的函数，根据当前的市场行情和持仓情况，判断是否触发买入或卖出信号，并执行相应的交易操作。

### 3. 主要功能

**初始化：**
- 设置交易品种、账号、交易金额、快慢线周期等参数。
- 订阅品种的行情数据。

**行情数据处理：**
- 获取历史收盘价数据，计算快线和慢线的移动平均值。
- 判断是否满足买入或卖出的条件。

**交易执行：**
- **买入条件**：当快线从下方穿过慢线（金叉），且当前没有持仓时，执行买入操作。
- **卖出条件**：当快线从上方穿过慢线（死叉），且当前有持仓时，执行卖出操作。

**委托管理：**
- 通过 `A.waiting_list` 管理未查到的委托，防止重复下单。
- 在每次交易后，将委托信息添加到 `A.waiting_list` 中，并在查询到委托后从列表中移除。

### 4. 交易逻辑

**买入逻辑：**
- 检查账户可用资金是否足够。
- 计算买入数量（向下取整到100的整数倍）。
- 如果满足买入条件，执行买入操作，并记录委托信息。

**卖出逻辑：**
- 检查当前是否有持仓。
- 如果满足卖出条件，执行卖出操作，并记录委托信息。

### 5. 其他功能

- **时间过滤**：只在交易时间段（09:30:00 到 15:00:00）内执行交易逻辑。
- **历史K线过滤**：跳过非最新的K线，只在最新的K线上执行交易逻辑。
- **委托查询**：在每次交易前，查询是否有未查到的委托，防止重复下单。

### 6. 依赖库

- **pandas**：用于数据处理。
- **numpy**：用于计算移动平均线。
- **datetime**：用于处理时间相关的操作。

### 总结

这个代码实现了一个基于双均线策略的量化交易系统，通过计算快慢均线的交叉点来判断买入和卖出的时机，并在满足条件时执行相应的交易操作。代码中还包含了委托管理、时间过滤、历史K线过滤等功能，以确保交易的准确性和安全性。
