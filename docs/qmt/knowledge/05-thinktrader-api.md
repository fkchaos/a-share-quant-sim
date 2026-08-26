# QMT (迅投) 内置Python API 参考手册

> 来源：迅投知识库 https://dict.thinktrader.net/innerApi/
> 文档版本：3.3.6 | Python 版本：3.6.8
> 更新日期：整理于 2026-08-26

---

## 目录

1. [系统概述与运行机制](#1-系统概述与运行机制)
2. [核心对象 ContextInfo](#2-核心对象-contextinfo)
3. [交易函数](#3-交易函数)
   - [passorder - 综合下单函数](#31-passorder---综合下单函数)
   - [algo_passorder - 算法拆单函数](#32-algo_passorder---算法拆单函数)
   - [smart_algo_passorder - 智能算法函数](#33-smart_algo_passorder---智能算法函数)
   - [cancel - 撤销委托](#34-cancel---撤销委托)
   - [cancel_task - 撤销任务](#35-cancel_task---撤销任务)
   - [get_trade_detail_data - 查询交易明细](#36-get_trade_detail_data---查询交易明细)
   - [get_history_trade_detail_data - 查询历史交易明细](#37-get_history_trade_detail_data---查询历史交易明细)
   - [get_last_order_id - 获取最新委托号](#38-get_last_order_id---获取最新委托号)
   - [get_value_by_order_id - 根据委托号查询信息](#39-get_value_by_order_id---根据委托号查询信息)
4. [行情函数](#4-行情函数)
   - [get_market_data_ex - 获取行情数据](#41-get_market_data_ex---获取行情数据)
   - [subscribe_quote - 订阅行情](#42-subscribe_quote---订阅行情)
   - [unsubscribe_quote - 反订阅行情](#43-unsubscribe_quote---反订阅行情)
5. [回调函数](#5-回调函数)
   - [order_callback - 委托主推](#51-order_callback---委托主推)
   - [deal_callback - 成交主推](#52-deal_callback---成交主推)
   - [account_callback - 账号状态变化主推](#53-account_callback---账号状态变化主推)
   - [task_callback - 任务状态变化主推](#54-task_callback---任务状态变化主推)
   - [orderError_callback - 异常下单主推](#55-ordererror_callback---异常下单主推)
6. [定时器函数](#6-定时器函数)
7. [数据结构 - 对象属性](#7-数据结构---对象属性)
8. [枚举常量](#8-枚举常量)
9. [注意事项与最佳实践](#9-注意事项与最佳实践)

---

## 1. 系统概述与运行机制

QMT 极速策略交易系统内置 Python 3.6 版本运行环境，提供**行情数据**与**交易下单**两大核心功能。

### 1.1 编码要求

```python
#coding:gbk    # 第一行必须声明GBK编码
```

### 1.2 三种运行机制

| 机制 | 分类 | 特点 | 匹配需求 |
|------|------|------|----------|
| 逐K线运行 (`handlebar`) | 事件驱动 | 同时支持历史回测和盘中模拟 | 实盘中模拟逐K线效果 |
| 订阅推送 (`subscribe`) | 事件驱动 | 盘中行情分笔触发回调 | 盘中随分笔行情判断交易 |
| 定时运行 (`run_time` / `schedule_run`) | 定时任务 | 固定间隔触发回调 | 盘中固定时间间隔判断交易 |

### 1.3 核心流程

```
(1) 下单前 → get_trade_detail_data 查询资金/持仓
(2) 满足条件 → passorder 下单
(3) 下单后 → get_last_order_id 获取最新委托号
(4) 用委托号 → get_value_by_order_id 查看委托状态
(5) 状态"已成" → deal 成交数据生成
(6) 根据委托状态 → cancel 撤单
```

### 1.4 回测 vs 实盘

**回测模型：**
- 使用 `get_market_data_ex` 读取本地数据，`subscribe=False`
- 不需要向服务器订阅行情
- 撮合规则：指定价在K线高低点间按指定价撮合，超出按收盘价撮合

**实盘模型：**
- 在模型交易界面运行
- 支持模拟信号模式和实盘交易模式
- 撮合规则以交易所为准

---

## 2. 核心对象 ContextInfo

ContextInfo 是策略运行环境的全局对象，在 `init`, `after_init`, `handlebar`, `run_time` 等函数中传递。

> ⚠️ ContextInfo 会随K线切换重置到上一根bar的结束状态，建议用自建全局变量存储数据。

### 2.1 常用属性

| 属性 | 类型 | 说明 |
|------|------|------|
| `ContextInfo.barpos` | int | 当前K线索引号（从0开始） |
| `ContextInfo.period` | string | 当前周期，如 `'1d'`, `'1m'`, `'5m'`, `'1h'` |
| `ContextInfo.stockcode` | string | 当前主图品种代码 |
| `ContextInfo.market` | string | 当前主图市场，`'SH'`/`'SZ'` |
| `ContextInfo.time_tick_size` | int | 当前图K线总数 |
| `ContextInfo.dividend_type` | string | 复权方式：`'none'`/`'front'`/`'back'` |
| `ContextInfo.do_back_test` | bool | 是否回测模式 |
| `ContextInfo.capital` | number | 回测初始资金（默认1000000） |
| `ContextInfo.start` | string | 回测开始时间 |
| `ContextInfo.end` | string | 回测结束时间 |

### 2.2 常用方法

| 方法 | 说明 |
|------|------|
| `ContextInfo.set_account(account)` | 设置交易账号（可多次调用，init中设置） |
| `ContextInfo.set_universe(stocklist)` | 设置股票池 |
| `ContextInfo.is_last_bar()` | 判断是否最后一根K线 |
| `ContextInfo.is_new_bar()` | 判断是否新K线第一个tick |
| `ContextInfo.get_stock_name(code)` | 根据代码获取名称 |
| `ContextInfo.get_bar_timetag(barpos)` | 获取指定K线时间戳 |
| `ContextInfo.get_market_data_ex(...)` | 获取行情数据 |
| `ContextInfo.subscribe_quote(...)` | 订阅行情 |
| `ContextInfo.unsubscribe_quote(...)` | 反订阅行情 |
| `ContextInfo.get_all_subscription()` | 获取所有订阅信息 |
| `ContextInfo.get_instrument_detail(code)` | 获取合约详细信息 |
| `ContextInfo.set_slippage(type, val)` | 设定回测滑点 |
| `ContextInfo.set_commission(type, list)` | 设定回测手续费 |
| `ContextInfo.set_output_index_property(...)` | 设定指标绘制属性 |
| `ContextInfo.run_time(func, period, start)` | 设置定时器（旧版） |
| `ContextInfo.schedule_run(...)` | 设置定时器（新版，支持分组/取消） |
| `ContextInfo.cancel_schedule_run(key)` | 取消定时任务 |

### 2.3 K线/品种相关方法

| 方法 | 说明 |
|------|------|
| `ContextInfo.get_sector('沪深300')` | 获取指数成分股 |
| `ContextInfo.get_industry('SW', '600000.SH')` | 获取行业成分股 |
| `ContextInfo.get_stock_list_in_sector('板块名')` | 获取板块内品种列表 |
| `ContextInfo.is_suspended_stock('600004.SH')` | 判断是否停牌 |
| `ContextInfo.is_sector_stock('沪深300', 'SH', '600000')` | 判断是否在板块中 |
| `ContextInfo.load_stk_list('文件路径')` | 从文件读取股票列表 |
| `ContextInfo.load_stk_vol_list('文件路径')` | 从文件读取股票及数量 |

---

## 3. 交易函数

### 3.1 passorder - 综合下单函数

**推荐使用**。用于股票、期货、期权等下单和新股、新债申购、融资融券等交易操作。

#### 函数签名

```python
passorder(
    opType,         # 操作类型
    orderType,      # 下单方式
    accountid,      # 资金账号
    orderCode,      # 下单代码
    prType,         # 报价类型
    price,          # 下单价格
    volume,         # 下单数量
    strategyName,   # 策略名称（可选）
    quickTrade,     # 快速下单标记（可选）
    userOrderId,    # 用户自设委托ID（可选）
    ContextInfo     # 策略上下文对象
)
```

#### 参数详细说明

##### opType - 操作类型

**期货六键：**
| 值 | 说明 |
|----|------|
| 0 | 开多 |
| 1 | 平昨多 |
| 2 | 平今多 |
| 3 | 开空 |
| 4 | 平昨空 |
| 5 | 平今空 |

**期货四键：**
| 值 | 说明 |
|----|------|
| 6 | 平多，优先平今 |
| 7 | 平多，优先平昨 |
| 8 | 平空，优先平今 |
| 9 | 平空，优先平昨 |

**期货两键：**
| 值 | 说明 |
|----|------|
| 10 | 卖出，优先平今开空 |
| 11 | 卖出，优先平昨开空 |
| 12 | 买入，优先平今开多 |
| 13 | 买入，优先平昨开多 |
| 14 | 买入，不优先平仓 |
| 15 | 卖出，不优先平仓 |

**股票/ETF/可转债：**
| 值 | 说明 |
|----|------|
| 23 | 买入（股票/ETF/可转债/沪港通/深港通） |
| 24 | 卖出（股票/ETF/可转债/沪港通/深港通） |

**融资融券：**
| 值 | 说明 |
|----|------|
| 33 | 融资买入 |
| 34 | 卖券还款 |
| 35 | 直接还款 |
| 36 | 融券卖出 |
| 37 | 买券还券 |
| 38 | 直接还券 |

**ETF申赎：**
| 值 | 说明 |
|----|------|
| 60 | ETF申购 |
| 61 | ETF赎回 |

**可转债：**
| 值 | 说明 |
|----|------|
| 40 | 可转债转股 |
| 41 | 可转债回售 |

##### orderType - 下单方式

| 值 | 说明 |
|----|------|
| 1101 | 单股、单账号、普通、股/手方式 |
| 1102 | 单股、单账号、普通、金额（元）方式（仅股票） |
| 1201 | 单股、多账号组、普通、股/手方式 |
| 1202 | 单股、多账号组、普通、金额（元）方式（仅股票） |
| 6001 | ETF组合申购（单账号） |
| 6002 | ETF组合赎回（单账号） |

> ⚠️ 期货不支持 1102 和 1202

##### prType - 下单选价类型

| 值 | 说明 |
|----|------|
| 0 | 卖5 |
| 1 | 卖4 |
| 2 | 卖3 |
| 3 | 卖2 |
| 4 | 卖1 |
| 5 | 最新价 |
| 6 | 买1 |
| 7 | 买2 |
| 8 | 买3 |
| 9 | 买4 |
| 10 | 买5 |
| 11 | **指定价**（只对单股支持） |
| 12 | 市价（涨跌停价） |
| 13 | 挂单价（本方一档） |
| 14 | **对手价**（对方一档，常用） |
| 15 | 自动盘口 |
| 16 | 昨收价 |
| 49 | 盘后定价申报 |

> 当 prType=11 时，price 参数为指定交易价格；prType 为其他值时，price 随意填写（如 -1, 0, 2, 100）

##### price - 下单价格

- 单股下单时，prType 为 11（指定价）或 49（盘后定价）时有效
- 其他情况无效但必须填写
- 组合套利时 price 作套利比例有效

##### volume - 下单数量

根据 orderType 最后一位确定单位：
- 单股下单：`1`=股/手，`2`=金额（元），`3`=比例（%）
- 组合下单：`1`=按组合数量，`2`=按组合权重（元），`3`=按账号可用（%）

##### strategyName - 策略名

- 用来区分 order 委托和 deal 成交来自不同策略
- 可缺省不写
- 只对同账号本地客户端有效

##### quickTrade - 快速下单标记

| 值 | 说明 |
|----|------|
| 0 | 否（默认） |
| 1 | 是，仅在最新K线（`is_last_bar()`为True）时立即触发 |
| 2 | 是，不判断bar状态，任何K线都立即触发（⚠️ 慎用，可能导致重复下单） |

> 默认（quickTrade=0）时，passorder 对最后一根K线完全走完后生成的模型信号，在下一根K线第一个tick来时触发下单

##### userOrderId - 用户自设委托ID

- 对应 order/deal 对象中的 `m_strRemark` 属性
- 通过 `get_trade_detail_data` 或 `order_callback`/`deal_callback` 可获取
- 若填写此参数，strategyName 和 quickTrade 也必须填写

##### userOrderParam - 用户自定义交易参数

- dict类型，主要用于修改算法交易参数
- 可缺省

#### 使用示例

```python
# coding:gbk

def init(C):
    C.stock = C.stockcode + '.' + C.market
    C.accountid = "testS"

def handlebar(C):
    account = get_trade_detail_data('test', 'stock', 'account')
    available_cash = int(account[0].m_dAvailable)
    holdings = get_trade_detail_data('test', 'stock', 'position')
    holdings_dict = {i.m_strInstrumentID + '.' + i.m_strExchangeID: i.m_nVolume for i in holdings}
    holding_vol = holdings_dict.get(C.stock, 0)

    # 买入示例（对手价，100股）
    if holding_vol == 0:
        vol = int(available_cash / C.get_market_data_ex(['close'], [C.stock])['close'][-1] / 100) * 100
        passorder(23, 1101, C.accountid, C.stock, 14, -1, vol, '策略名', 2, '买入备注', C)

    # 卖出示例（对手价，全仓卖出）
    elif holding_vol > 0:
        passorder(24, 1101, C.accountid, C.stock, 14, -1, holding_vol, '策略名', 2, '卖出备注', C)

# 指定价格买入
# passorder(23, 1101, ContextInfo.accID, "512000.SH", 11, 0.580, 100, "限价买入", 1, "指定价格买入", ContextInfo)

# 最新价买入
# passorder(23, 1101, ContextInfo.accID, "512000.SH", 5, 0, 100, "最新价买入", 1, "最新价买入", ContextInfo)

# 指定价格卖出
# passorder(24, 1101, ContextInfo.accID, "512000.SH", 11, 0.600, 100, "限价卖出", 1, "指定价格卖出", ContextInfo)
```

### 3.2 algo_passorder - 算法下单（拆单）函数

```python
algo_passorder(
    opType, orderType, accountid, orderCode, prType, price, volume,
    strategyName, quickTrade, userOrderId, userOrderParam, ContextInfo
)
```

参数与 passorder 基本一致，新增 `userOrderParam`（dict）用于算法交易参数配置。

### 3.3 smart_algo_passorder - 智能算法函数

```python
smart_algo_passorder(
    opType, orderType, accountid, orderCode, prType, price, volume,
    strategyName, quickTrade, userOrderId, userOrderParam, smartAlgoType, ContextInfo
)
```

新增参数：
- `smartAlgoType`：智能算法类型（如 VWAP、TWAP 等）

### 3.4 cancel - 撤销委托

```python
cancel(orderid, ContextInfo)
```

- `orderid`: int，委托号
- 通过 `get_last_order_id` 获取最新委托号后调用

### 3.5 cancel_task - 撤销任务

```python
cancel_task(taskId, accountId, accountType, ContextInfo)
```

### 3.6 get_trade_detail_data - 查询交易明细

**最重要的查询函数之一。**

```python
get_trade_detail_data(accountID, strAccountType, strDatatype[, strategyName])
```

#### 参数

| 参数 | 类型 | 说明 |
|------|------|------|
| accountID | string | 资金账号 |
| strAccountType | string | 账号类型 |
| strDatatype | string | 数据类型 |
| strategyName | string | 策略名（可选，仅对ORDER/DEAL有效） |

#### strAccountType 可选值

| 值 | 说明 |
|----|------|
| `'FUTURE'` | 期货 |
| `'STOCK'` | 股票 |
| `'CREDIT'` | 信用 |
| `'HUGANGTONG'` | 沪港通 |
| `'SHENGANGTONG'` | 深港通 |
| `'STOCK_OPTION'` | 期权 |

#### strDatatype 可选值

| 值 | 说明 | 返回类型 |
|----|------|----------|
| `'POSITION'` | 持仓 | list[Position] |
| `'ORDER'` | 委托 | list[Order] |
| `'DEAL'` | 成交 | list[Deal] |
| `'ACCOUNT'` | 账号 | Account对象 |
| `'TASK'` | 任务 | list[Task] |

#### 返回值

返回 list，list 中包含 Python 对象。通过 `dir(obj)` 查看属性。

#### 使用示例

```python
# 查询账号资金
account_info = get_trade_detail_data('10000001', 'stock', 'ACCOUNT')
cash = account_info[0].m_dAvailable  # 可用资金

# 查询持仓
positions = get_trade_detail_data('10000001', 'stock', 'POSITION')
for pos in positions:
    code = pos.m_strInstrumentID + '.' + pos.m_strExchangeID  # 完整代码如 '600519.SH'
    name = pos.m_strInstrumentName   # 证券名称
    volume = pos.m_nVolume           # 持仓量
    avail = pos.m_nCanUseVolume      # 可用数量
    cost = pos.m_dOpenPrice          # 开仓均价
    profit = pos.m_dPositionProfit   # 持仓盈亏
    market_val = pos.m_dInstrumentValue  # 当前市值

# 查询委托
orders = get_trade_detail_data('10000001', 'stock', 'ORDER')
for o in orders:
    print(f'代码: {o.m_strInstrumentID}, 委托量: {o.m_nVolumeTotalOriginal}, '
          f'成交均价: {o.m_dTradedPrice}, 成交量: {o.m_nVolumeTraded}')

# 查询成交
deals = get_trade_detail_data('10000001', 'stock', 'DEAL')
for d in deals:
    print(f'代码: {d.m_strInstrumentID}, 成交价: {d.m_dPrice}, '
          f'成交量: {d.m_nVolume}, 成交额: {d.m_dTradeAmount}')

# 按策略名查询
orders = get_trade_detail_data('10000001', 'stock', 'ORDER', 'myStrategy')
```

### 3.7 get_history_trade_detail_data - 查询历史交易明细

```python
get_history_trade_detail_data(accountID, strAccountType, strDatatype, strStartDate, strEndDate)
```

- `strDatatype`: `'POSITION'`, `'ORDER'`, `'DEAL'`
- `strStartDate`: `'20240513'`
- `strEndDate`: `'20240514'`
- 返回：`([timetag, obj...])` 元组

### 3.8 get_last_order_id - 获取最新委托号

```python
get_last_order_id(accountID, strAccountType, strDatatype[, strategyName])
```

- 返回最近一次的委托ID编号
- 无 strategyName 时返回所有委托的最近一次ID

### 3.9 get_value_by_order_id - 根据委托号查询信息

```python
get_value_by_order_id(orderID, accountID, strAccountType, strDatatype)
```

- 查询指定委托号的委托或成交信息

---

## 4. 行情函数

### 4.1 get_market_data_ex - 获取行情数据

```python
ContextInfo.get_market_data_ex(
    field_list,         # 字段列表，如 ['open', 'high', 'low', 'close', 'volume']
    stock_list,         # 品种列表，如 ['000001.SZ', '600519.SH']
    period='1d',        # 周期：'1m', '5m', '15m', '30m', '1h', '1d', '1w', '1mon'
    start_time='',      # 开始时间
    end_time='',        # 结束时间
    count=-1,           # 获取K线数量，-1为全部
    dividend_type='',   # 复权方式：'none', 'front', 'back'
    subscribe=True      # 是否订阅行情（回测时设为False读取本地数据）
)
```

**返回值：** dict，key为品种代码，value为 DataFrame

#### 使用示例

```python
# 回测时读取本地数据（subscribe=False）
local_data = C.get_market_data_ex(
    ['close'], [C.stock],
    end_time=bar_date,
    period=C.period,
    count=max(C.line1, C.line2),
    subscribe=False
)
close_list = list(local_data[C.stock].iloc[:, 0])

# 实盘时订阅数据
market_data = C.get_market_data_ex(
    ['open', 'high', 'low', 'close'],
    C.stock_list,
    period='1d',
    end_time=bar_date
)
```

### 4.2 subscribe_quote - 订阅行情

```python
ContextInfo.subscribe_quote(
    stock,          # 品种代码
    period='1d',    # 周期
    count=-1,       # 数量
    callback=None   # 回调函数（可选）
)
```

### 4.3 unsubscribe_quote - 反订阅行情

```python
ContextInfo.unsubscribe_quote(stock)
```

---

## 5. 回调函数

> ⚠️ 所有回调函数仅在**实盘运行模式**下生效，需要先在 init 中调用 `ContextInfo.set_account(account)`。

### 5.1 order_callback - 委托主推

当委托状态有变化时，客户端调用此函数。

```python
def order_callback(ContextInfo, orderInfo):
    # orderInfo 为委托对象，包含以下常用属性：
    # m_strInstrumentID     - 品种代码
    # m_strExchangeID       - 交易所代码
    # m_strInstrumentName   - 证券名称
    # m_nOrderSysID         - 委托编号（合同号）
    # m_nRef                - 订单编号
    # m_nVolumeTotalOriginal - 委托数量
    # m_nVolumeTraded       - 成交数量
    # m_dTradedPrice        - 成交均价
    # m_nOffsetFlag         - 买卖方向（48买入/开仓, 49卖出/平仓）
    # m_strRemark           - 投资备注（对应 userOrderId）
    # m_nOrderStatus        - 委托状态
    # m_nTaskId             - 任务号
    pass
```

### 5.2 deal_callback - 成交主推

当有成交时，客户端调用此函数。

```python
def deal_callback(ContextInfo, dealInfo):
    # dealInfo 为成交对象，包含以下常用属性：
    # m_strInstrumentID     - 品种代码
    # m_strExchangeID       - 交易所代码
    # m_strInstrumentName   - 证券名称
    # m_nRef                - 订单编号
    # m_strOrderSysID       - 委托编号（合同号）
    # m_strTradeDate        - 成交日期
    # m_strTradeTime        - 成交时间
    # m_dPrice              - 成交价格
    # m_nVolume             - 成交数量
    # m_dTradeAmount        - 成交金额
    # m_nOffsetFlag         - 买卖方向（48买入/开仓, 49卖出/平仓）
    # m_strRemark           - 投资备注
    pass
```

### 5.3 account_callback - 账号状态变化主推

```python
def account_callback(ContextInfo, accountInfo):
    # accountInfo 为账号对象，常用属性：
    # m_strAccountID        - 账号ID
    # m_dBalance            - 资金余额
    # m_dAvailable          - 可用资金
    # m_dAssetBalance       - 资产总值
    # m_dFrozenCash         - 冻结资金
    # m_dPositionProfit     - 持仓盈亏
    # m_dStockValue         - 股票市值
    # m_dCommission         - 手续费
    # m_strStatus           - 账号状态
    # m_strTradingDate      - 交易日期
    pass
```

### 5.4 task_callback - 任务状态变化主推

```python
def task_callback(ContextInfo, taskInfo):
    # taskInfo 为任务对象（CTaskDetail）
    pass
```

### 5.5 orderError_callback - 异常下单主推

```python
def orderError_callback(ContextInfo, orderArgs, errMsg):
    # orderArgs 为下单参数对象（PassorderArguments），包含：
    # accountID, opType, orderCode, orderType, prType, modelPrice, modelVolume, strategyName
    # errMsg 为错误信息字符串
    print(f"下单异常: {errMsg}")
```

### 5.6 credit_account_callback - 信用账户明细回调

```python
def credit_account_callback(ContextInfo, seq, result):
    # seq: query_credit_account 时输入的查询序号
    # result: 信用账户明细对象
    pass
```

### 5.7 credit_opvolume_callback - 两融最大可下单量回调

```python
def credit_opvolume_callback(ContextInfo, accid, seq, ret, result):
    # ret: 查询结果状态（1正常, -1查询中, -2非法账号, -3非法参数, -4超时）
    # result: 查询结果
    pass
```

---

## 6. 定时器函数

### 6.1 run_time（旧版）

```python
def init(ContextInfo):
    ContextInfo.run_time("myFunc", "5nSecond", "2019-10-14 13:20:00")

def myFunc(ContextInfo):
    # 每5秒执行一次
    pass
```

**period 格式：**
- `'5nSecond'` - 每5秒
- `'500nMilliSecond'` - 每500毫秒
- `'5nDay'` - 每5天

> ⚠️ 回测时无效；定时器无结束方法，随策略结束而结束。

### 6.2 schedule_run（新版）

```python
import datetime as dt

def on_timer(C):
    print('hello')

def init(ContextInfo):
    tid = ContextInfo.schedule_run(
        on_timer,                           # 回调函数
        '20231231235959',                   # 首次触发时间
        -1,                                 # 重复次数（-1无限）
        dt.timedelta(minutes=1),            # 间隔
        'my_timer'                          # 任务组名
    )

# 取消定时任务
# ContextInfo.cancel_schedule_run('my_timer')  # 按组名取消
# ContextInfo.cancel_schedule_run(1)            # 按任务号取消
```

---

## 7. 数据结构 - 对象属性

### 7.1 Position - 持仓对象

| 属性 | 类型 | 说明 |
|------|------|------|
| `m_strInstrumentID` | string | 品种代码 |
| `m_strExchangeID` | string | 交易所代码 |
| `m_strInstrumentName` | string | 证券名称 |
| `m_nVolume` | int | 持仓量 |
| `m_nCanUseVolume` | int | 可用数量 |
| `m_dOpenPrice` | float | 开仓均价 |
| `m_dPositionCost` | float | 持仓成本 |
| `m_dPositionProfit` | float | 持仓盈亏 |
| `m_dTodayPositionProfit` | float | 当日持仓盈亏 |
| `m_dCloseProfit` | float | 平仓盈亏 |
| `m_dInstrumentValue` | float | 当前市值 |
| `m_dMarketValue` | float | 市值/合约价值 |

### 7.2 Order - 委托对象

| 属性 | 类型 | 说明 |
|------|------|------|
| `m_strInstrumentID` | string | 品种代码 |
| `m_strExchangeID` | string | 交易所代码 |
| `m_strInstrumentName` | string | 证券名称 |
| `m_nOrderSysID` | string | 委托编号（合同号） |
| `m_nRef` | int | 订单编号 |
| `m_nVolumeTotalOriginal` | int | 委托数量 |
| `m_nVolumeTraded` | int | 成交数量 |
| `m_dTradedPrice` | float | 成交均价 |
| `m_nOffsetFlag` | int | 买卖方向（48=买/开, 49=卖/平） |
| `m_strRemark` | string | 投资备注（userOrderId） |
| `m_nOrderStatus` | int | 委托状态 |
| `m_nTaskId` | int | 任务号 |

### 7.3 Deal - 成交对象

| 属性 | 类型 | 说明 |
|------|------|------|
| `m_strInstrumentID` | string | 品种代码 |
| `m_strExchangeID` | string | 交易所代码 |
| `m_strInstrumentName` | string | 证券名称 |
| `m_nRef` | int | 订单编号 |
| `m_strOrderSysID` | string | 委托编号 |
| `m_strTradeDate` | string | 成交日期 |
| `m_strTradeTime` | string | 成交时间 |
| `m_dPrice` | float | 成交价格 |
| `m_nVolume` | int | 成交数量 |
| `m_dTradeAmount` | float | 成交金额 |
| `m_nOffsetFlag` | int | 买卖方向（48=买/开, 49=卖/平） |
| `m_strRemark` | string | 投资备注 |

### 7.4 Account - 账号对象

| 属性 | 类型 | 说明 |
|------|------|------|
| `m_strAccountID` | string | 账号ID |
| `m_dBalance` | float | 资金余额 |
| `m_dAvailable` | float | 可用资金 |
| `m_dAssetBalance` | float | 资产总值 |
| `m_dFrozenCash` | float | 冻结资金 |
| `m_dFrozenCommission` | float | 冻结手续费 |
| `m_dPositionProfit` | float | 持仓盈亏 |
| `m_dStockValue` | float | 股票市值 |
| `m_dInstrumentValue` | float | 合约价值 |
| `m_dCommission` | float | 手续费 |
| `m_dCloseProfit` | float | 平仓盈亏 |
| `m_dPreBalance` | float | 昨日余额 |
| `m_strStatus` | string | 账号状态 |
| `m_strTradingDate` | string | 交易日期 |
| `m_Enable` | bool | 是否启用 |

### 7.5 TaskDetail - 任务对象

| 属性 | 类型 | 说明 |
|------|------|------|
| `m_nTaskId` | int | 任务号 |
| `m_nTaskStatus` | int | 任务状态 |

### 7.6 PassorderArguments - 下单参数对象

| 属性 | 类型 | 说明 |
|------|------|------|
| `accountID` | string | 资金账号 |
| `opType` | int | 操作类型 |
| `orderCode` | string | 品种代码 |
| `orderType` | int | 下单方式 |
| `prType` | int | 报价类型 |
| `modelPrice` | float | 模型价格 |
| `modelVolume` | float | 模型数量 |
| `formulaName` | string | 策略名 |
| `strategyName` | string | 策略名称 |
| `currentTime` | int | 当前时间 |

---

## 8. 枚举常量

### 8.1 EOperationType - 下单操作类型

| 常量 | 值 | 说明 |
|------|----|------|
| `OPT_OPEN_LONG` | 0 | 开多 |
| `OPT_CLOSE_LONG_HISTORY` | 1 | 平昨多 |
| `OPT_CLOSE_LONG_TODAY` | 2 | 平今多 |
| `OPT_OPEN_SHORT` | 3 | 开空 |
| `OPT_CLOSE_SHORT_HISTORY` | 4 | 平昨空 |
| `OPT_CLOSE_SHORT_TODAY` | 5 | 平今空 |
| `OPT_CLOSE_LONG_TODAY_FIRST` | 6 | 平多优先平今 |
| `OPT_CLOSE_LONG_HISTORY_FIRST` | 7 | 平多优先平昨 |
| `OPT_CLOSE_SHORT_TODAY_FIRST` | 8 | 平空优先平今 |
| `OPT_CLOSE_SHORT_HISTORY_FIRST` | 9 | 平空优先平昨 |
| `OPT_CLOSE_LONG` | 14 | 平多 |
| `OPT_CLOSE_SHORT` | 15 | 平空 |
| `OPT_OPEN` | 16 | 开仓 |
| `OPT_CLOSE` | 17 | 平仓 |
| `OPT_BUY` | 18 | 买入 |
| `OPT_SELL` | 19 | 卖出 |
| `OPT_FIN_BUY` | 20 | 融资买入 |
| `OPT_SLO_SELL` | 21 | 融券卖出 |

### 8.2 EPriceType - 价格类型

| 常量 | 值 | 说明 |
|------|----|------|
| `PRTP_SALE5` | 0 | 卖5 |
| `PRTP_SALE4` | 1 | 卖4 |
| `PRTP_SALE3` | 2 | 卖3 |
| `PRTP_SALE2` | 3 | 卖2 |
| `PRTP_SALE1` | 4 | 卖1 |
| `PRTP_LATEST` | 5 | 最新价 |
| `PRTP_BUY1` | 6 | 买1 |
| `PRTP_BUY2` | 7 | 买2 |
| `PRTP_BUY3` | 8 | 买3 |
| `PRTP_BUY4` | 9 | 买4 |
| `PRTP_BUY5` | 10 | 买5 |
| `PRTP_FIX` | 11 | 指定价 |
| `PRTP_MARKET` | 12 | 市价 |
| `PRTP_HANG` | 13 | 挂单价 |
| `PRTP_COMPETE` | 14 | 对手价 |
| `PRTP_AUTO` | 15 | 自动盘口 |
| `PRTP_CLOSE` | 16 | 昨收价 |
| `PRTP_AFTER_FIX_PRICE` | 49 | 盘后定价 |

### 8.3 EOrderStatus - 委托状态

| 常量 | 说明 |
|------|------|
| `ORDER_STATUS_UNREPORTED` | 未报 |
| `ORDER_STATUS_WAITING` | 待报 |
| `ORDER_STATUS_REPORTED` | 已报 |
| `ORDER_STATUS_PARTDEAL` | 部分成交 |
| `ORDER_STATUS_DEAL` | 已成 |
| `ORDER_STATUS撤销` | 已撤 |
| `ORDER_STATUS_REJECTED` | 废单 |

### 8.4 OffsetFlag - 买卖方向

| 值 | 说明 |
|----|------|
| 48 | 买入/开仓 |
| 49 | 卖出/平仓 |
| 50 | 强平 |
| 51 | 平今 |
| 52 | 平昨 |
| 53 | 强减 |

### 8.5 CompactStatus - 合约状态

| 常量 | 值 | 说明 |
|------|----|------|
| `COMPACT_STATUS_UNKOWN` | 51 | 未知 |
| `COMPACT_STATUS_DEBT` | 52 | 已形成负债 |
| `COMPACT_STATUS_NOT_DEBT` | 53 | 未形成负债 |
| `COMPACT_STATUS_EXPIRY` | 54 | 合约已过期 |

---

## 9. 注意事项与最佳实践

### 9.1 编码

- 脚本第一行必须是 `#coding:gbk`
- 缩进统一使用4个空格或Tab

### 9.2 passorder 注意事项

1. **quickTrade=0（默认）**：信号在下一根K线第一个tick时触发下单
2. **quickTrade=1**：仅在最新K线（`is_last_bar()`为True）立即触发
3. **quickTrade=2**：不判断bar状态，历史K线也会触发，**慎用**
4. 指定价格交易时，`prType`必须设为`11`
5. 参数顺序不可乱，不支持具名参数调用

### 9.3 get_trade_detail_data 注意事项

1. 数据从**客户端本地缓存**读取，不是实时查询柜台
2. 有交易主推的柜台50ms刷新一次，没有的1-6秒一次
3. 卖出委托后**立刻查询**，不会查到对应委托，可用资金也不会变化
4. strategyName 只对 ORDER 和 DEAL 有效

### 9.4 回调函数注意事项

1. 仅在**实盘运行模式**下生效
2. 需在 `init` 中调用 `ContextInfo.set_account(account)` 订阅账号
3. 回调函数中的下单也需使用 `quickTrade=2` 才能立即触发

### 9.5 ContextInfo 注意事项

1. ContextInfo 会随K线切换重置，不要依赖其自定义属性跨bar保存
2. 使用自定义全局变量存储跨bar数据
3. `after_init` 函数在 `init` 完成后、`handlebar` 之前调用，适合一次性操作

### 9.6 第三方库安装

```bash
pip install <库名> -t <QMT安装目录>/bin.x64/Lib/site-packages
```

内置库：NumPy 1.16+, Pandas 0.22, SciPy 1.2+, TA-Lib 0.4.17, sklearn 0.20, matplotlib 3.0

---

> 参考资料：
> - 官方知识库: https://dict.thinktrader.net/innerApi/
> - QMT Python API 文档: https://qmt.ptradeapi.com/QMT_Python_API_Doc.html
> - 迅投QMT文档: https://zilchyao.github.io/xuntou_yao
> - 博客园API详解: https://www.cnblogs.com/liubo0056/p/19118538
