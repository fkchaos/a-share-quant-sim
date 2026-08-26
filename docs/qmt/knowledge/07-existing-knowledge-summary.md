# QMT 知识库现有文档综合摘要

> 生成时间: 2026-08-26
> 数据来源: `/root/a-share-quant-sim/docs/qmt/knowledge/` (16个文件) + `wtsolutions/` (26个文件)

---

## 一、文档结构概览

### knowledge/ 目录 (16个文件)

| 目录 | 文件 | 核心内容 |
|------|------|----------|
| 01-入门 | 快速开始.md | 回测/实盘模型区别、handlebar/subscribe/run_time三种机制、完整代码示例 |
| 01-入门 | QMT新人上手教程.md | 安装配置、通信设置、Python库下载、行情设置、数据下载、VIP函数列表 |
| 01-入门 | 使用须知.md | 环境准备 |
| 01-入门 | 变量约定.md | symbol_code格式、交易所代码、账号类型、ContextInfo属性、回测/模拟/实盘模式 |
| 01-入门 | 界面操作.md | 界面操作指南 |
| 01-入门 | 迅投研新手指南.md | 新手引导 |
| 02-API | **交易函数.md** | passorder、get_trade_detail_data、cancel_order、order_lots等(2186行) |
| 02-API | **行情函数.md** | get_market_data_ex、get_full_tick、subscribe_quote等(22690字符) |
| 02-API | **引用函数.md** | 技术指标引用函数(8951字符) |
| 02-API | **系统函数.md** | run_time、timetag_to_datetime等系统函数(12094字符) |
| 02-API | **绘图函数.md** | paint、draw_text、draw_number等绘图函数 |
| 02-API | **成交回报实时主推函数.md** | order_callback、deal_callback、account_callback等回调函数 |
| 03-数据与枚举 | **数据结构.md** | Tick/Bar/l2quote/Account/Order/Deal/Position等数据对象定义(874行) |
| 03-数据与枚举 | **枚举常量.md** | opType/orderType/prType/quicktrade及全部枚举值(826行) |
| 04-示例与FAQ | **完整示例.md** | 行情获取、交易下单、回测完整示例(1338行) |
| 04-示例与FAQ | **常见问题.md** | Python环境、策略运行、行情、交易常见问题(359行) |

### wtsolutions/ 目录 (26个文件，第三方社区整理)

关键文件:
- **qmt-passorder-guide.md** — passorder参数逐一详解
- **qmt-order-deal-callback.md** — 委托/成交回调用法、userOrderId状态机
- **qmt-knowledge-base.md** — 官方文档PDF下载指引
- **miniqmt-termination.md** — MiniQMT停服影响与迁移方案
- **qmt-account-not-defined.md** — 账号未定义问题
- **qmt-server-connection.md** — 服务器连接问题
- **qmt-history-data.md** — 历史数据问题
- **qmt-monitoring-alert.md** — 监控告警
- **qmt-email-notification.md** — 邮件通知
- **qmt-wechat-notification.md** — 微信通知

---

## 二、核心 API 详细汇总

### 2.1 交易函数 (交易函数.md)

#### passorder — 核心下单函数

```python
passorder(opType, orderType, accountid, orderCode, prType, price, volume, 
          strategyName, quickOrder, userOrderId, ContextInfo)
```

| 参数 | 说明 | 常用值 |
|------|------|--------|
| opType | 操作类型 | **23**=股票买入, **24**=股票卖出 |
| orderType | 下单方式 | **1101**=单股按股数, 1102=按金额, 1113=总资产比例, 1123=可用比例 |
| accountid | 资金账号 | 字符串，如 `'testS'` |
| orderCode | 证券代码 | `'000001.SZ'` (必须带交易所后缀) |
| prType | 价格类型 | **5**=最新价, **11**=指定价, **14**=对手价, **42-48**=市价单 |
| price | 价格 | prType=11时填具体价格, 其他填-1 |
| volume | 数量 | 按orderType最后一位确定单位: 1=股/手, 2=金额, 3=比例 |
| strategyName | 策略名称 | 字符串标识，用于筛选委托/成交 |
| quickOrder | 快速下单 | **0**=逐K线(默认), **1**=当前K线, **2**=立即下单(危险) |
| userOrderId | 用户委托ID | 字符串，写入m_strRemark字段，回调时读出 |
| ContextInfo | 上下文 | 传入ContextInfo对象 |

#### get_trade_detail_data — 查询交易数据

```python
get_trade_detail_data(accountID, strAccountType, strDatatype[, strategyName])
```

| strDatatype | 返回对象 | 说明 |
|-------------|----------|------|
| `'ACCOUNT'` | Account | 账号资金信息 |
| `'POSITION'` | Position | 持仓明细 |
| `'POSITION_STATISTICS'` | PositionStatistics | 持仓统计 |
| `'ORDER'` | Order | 委托记录 |
| `'DEAL'` | Deal | 成交记录 |
| `'TASK'` | TaskDetail | 任务记录 |

**关键字段速查:**

| 对象 | 常用字段 |
|------|----------|
| Account | `m_dBalance`(总资产), `m_dAvailable`(可用), `m_dInstrumentValue`(总市值), `m_dPositionProfit`(盈亏) |
| Position | `m_strInstrumentID`(代码), `m_nVolume`(持仓量), `m_nCanUseVolume`(可用量), `m_dOpenPrice`(成本价), `m_dPositionProfit`(盈亏) |
| Order | `m_strInstrumentID`, `m_nOrderStatus`(49-57状态码), `m_nVolumeTraded`, `m_strOrderSysID` |
| Deal | `m_strInstrumentID`, `m_dPrice`(成交价), `m_nVolume`(成交量), `m_dTradeAmount`(成交额) |

#### 其他交易函数

| 函数 | 用途 |
|------|------|
| `cancel_order(accountID, strAccountType, orderSysID, ContextInfo)` | 撤单 |
| `cancel_task(taskId, accountId, accountType, ContextInfo)` | 取消任务 |
| `pause_task(taskId, accountId, accountType, ContextInfo)` | 暂停任务 |
| `resume_task(taskId, accountId, accountType, ContextInfo)` | 恢复任务 |
| `get_last_order_id(accountID, strAccountType, strDatatype[, strategyName])` | 获取最新委托号 |
| `get_value_by_order_id(orderId, accountID, strAccountType, strDatatype)` | 按委托号查询 |
| `get_history_trade_detail_data(accountID, strAccountType, strDatatype, startDate, endDate)` | 历史成交明细 |
| `get_ipo_data([type])` | 新股新债信息 |
| `get_new_purchase_limit(accid)` | 新股申购额度 |
| `get_basket(basketName)` | 获取股票篮子 |
| `set_basket(basketDict)` | 设置股票篮子 |

#### 回测专用函数

| 函数 | 用途 |
|------|------|
| `order_lots(stockcode, lots[, style, price], ContextInfo[, accId])` | 按手数交易 |
| `order_shares(stockcode, shares[, style, price], ContextInfo[, accId])` | 按股数交易 |
| `order_value(stockcode, value[, style, price], ContextInfo[, accId])` | 按金额交易 |
| `order_percent(stockcode, percent[, style, price], ContextInfo[, accId])` | 按比例交易 |
| `order_target_value(stockcode, tar_value[, style, price], ContextInfo[, accId])` | 目标金额调仓 |
| `order_target_percent(stockcode, tar_percent[, style, price], ContextInfo[, accId])` | 目标比例调仓 |
| `buy_open / sell_open / buy_close_* / sell_close_*` | 期货专用 |

---

### 2.2 行情函数 (行情函数.md)

#### get_market_data_ex — 获取行情数据（最核心）

```python
ContextInfo.get_market_data_ex(stock_code, fields, period, start_time, end_time, 
                                count, dividend_type, fill_data, subscribe)
```

**subscribe参数关键区别:**
- `subscribe=True`: 从订阅数据获取(实时更新)，受订阅数限制(≤500)
- `subscribe=False`: 从本地数据获取(不更新)，无限制，需提前下载数据

**支持周期:** `'1m','3m','5m','15m','30m','1h','2h','1d','1w','1mon'`
**Level2周期:** `'l2transaction','l2order','l2quote','l2transactioncount','l2orderqueue'`

#### get_full_tick — 获取全推数据

```python
ContextInfo.get_full_tick(stock_list)
```

返回全市场最新快照，50ms更新一次，无历史数据，无订阅数限制。

#### subscribe_quote — 订阅行情

```python
ContextInfo.subscribe_quote(stock_code, period, dividend_type, result_type, callback)
```

返回订阅号，用于unsubscribe_quote取消订阅。

#### 其他行情函数

| 函数 | 用途 |
|------|------|
| `get_market_data(stock_list, period, start_time, end_time, count)` | 旧版获取(不推荐) |
| `get_local_data(fields, stock_list, period, start_time, end_time)` | 取本地数据 |
| `download_history_data(stock_code, period, start_time, end_time)` | 下载历史数据 |
| `subscribe_whole_quote(code_list, callback)` | 订阅全推增量 |
| `unsubscribe_quote(subscribe_num)` | 取消订阅 |
| `get_stock_list_in_sector(sector)` | 获取板块股票列表 |
| `get_stock_name(stock_code)` | 获取证券名称 |
| `get_instrumentdetail(stock_code)` | 获取合约详情 |
| `get_last_volume(stock_code)` | 获取最新成交量 |

---

### 2.3 回调函数 (成交回报实时主推函数.md)

**所有回调函数仅在实盘运行模式下生效，需先调用`ContextInfo.set_account`。**

| 回调函数 | 触发时机 | 参数 |
|----------|----------|------|
| `account_callback(ContextInfo, accountInfo)` | 账号资金状态变化 | accountInfo: Account对象 |
| `task_callback(ContextInfo, taskInfo)` | 账号任务状态变化 | taskInfo: TaskDetail对象 |
| `order_callback(ContextInfo, orderInfo)` | 委托状态变化 | orderInfo: Order对象 |
| `deal_callback(ContextInfo, dealInfo)` | 成交状态变化 | dealInfo: Deal对象 |
| `position_callback(ContextInfo, positionInfo)` | 持仓状态变化 | positionInfo: Position对象 |
| `orderError_callback(ContextInfo, orderArgs, errMsg)` | 下单异常 | orderArgs: 参数对象, errMsg: 错误信息 |
| `credit_account_callback(ContextInfo, seq, result)` | 信用账户查询结果 | 配合query_credit_account使用 |
| `credit_opvolume_callback(ContextInfo, accid, seq, ret, result)` | 两融下单量查询结果 | 配合query_credit_opvolume使用 |

---

### 2.4 数据结构 (数据结构.md)

#### Tick 对象 (get_full_tick/get_market_data_ex返回)

| 字段 | 类型 | 含义 |
|------|------|------|
| `time` | int | 时间戳 |
| `lastPrice` | float | 最新价 |
| `open/high/low` | float | 开高低 |
| `lastClose` | float | 前收盘价 |
| `amount` | float | 成交总额 |
| `volume` | int | 成交总量(**手**) |
| `askPrice/bidPrice` | list[float] | 多档委买卖价(5档) |
| `askVol/bidVol` | list[int] | 多档委买卖量 |

#### Bar 对象

| 字段 | 类型 | 含义 |
|------|------|------|
| `time` | int | 时间 |
| `open/high/low/close` | float | OHLC |
| `volume` | float | 成交量 |
| `amount` | float | 成交额 |
| `preClose` | float | 前收盘价 |

#### Account 对象 (关键字段)

| 字段 | 含义 |
|------|------|
| `m_dBalance` | 总资产 |
| `m_dAvailable` | 可用金额 |
| `m_dInstrumentValue` | 总市值 |
| `m_dPositionProfit` | 持仓盈亏 |
| `m_dCloseProfit` | 平仓盈亏 |
| `m_dCommission` | 手续费 |
| `m_strTradingDate` | 交易日 |

#### Position 对象 (关键字段)

| 字段 | 含义 |
|------|------|
| `m_strInstrumentID` | 证券代码 |
| `m_strExchangeID` | 交易所 |
| `m_nVolume` | 当前持仓量 |
| `m_nCanUseVolume` | 可用数量 |
| `m_nFrozenVolume` | 冻结数量 |
| `m_dOpenPrice` | 成本价 |
| `m_dLastPrice` | 最新价 |
| `m_dFloatProfit` | 浮动盈亏 |
| `m_dPositionProfit` | 持仓盈亏 |
| `m_dMarketValue` | 市值 |
| `m_bIsToday` | 是否今仓 |

#### Order 对象 (关键字段)

| 字段 | 含义 |
|------|------|
| `m_strInstrumentID` | 证券代码 |
| `m_nOrderStatus` | 委托状态(49-57) |
| `m_nVolumeTotalOriginal` | 委托数量 |
| `m_nVolumeTraded` | 已成交量 |
| `m_dTradedPrice` | 成交均价 |
| `m_strOrderSysID` | 合同编号 |
| `m_strRemark` | 投资备注(userOrderId) |

#### Deal 对象 (关键字段)

| 字段 | 含义 |
|------|------|
| `m_strInstrumentID` | 证券代码 |
| `m_dPrice` | 成交均价 |
| `m_nVolume` | 成交量 |
| `m_dTradeAmount` | 成交额 |
| `m_dCommission` | 手续费 |
| `m_strRemark` | 投资备注 |

---

### 2.5 枚举常量 (枚举常量.md)

#### opType — 操作类型 (股票最常用)

| 值 | 含义 |
|----|------|
| **23** | 股票/ETF/可转债买入 |
| **24** | 股票/ETF/可转债卖出 |

#### orderType — 下单方式

| 值 | 含义 |
|----|------|
| **1101** | 单股、单账号、普通、按股/手数下单 |
| 1102 | 单股、单账号、按金额下单(仅股票) |
| 1113 | 单股、单账号、总资产比例下单 |
| 1123 | 单股、单账号、可用比例下单 |
| 1201-1223 | 单股、账号组版本 |

#### prType — 价格类型 (股票常用)

| 值 | 含义 |
|----|------|
| **5** | 最新价 |
| **11** | 指定价(配合price参数) |
| **14** | 对手价 |
| 0-10 | 卖5价到买5价 |
| 12 | 涨跌停价 |
| 13 | 挂单价 |
| 42-49 | 各种市价单(需注意交易所限制) |

#### quickTrade — 快速下单

| 值 | 含义 |
|----|------|
| **0** | 默认，逐K线生效 |
| **1** | 当前K线立即生效 |
| **2** | 任何时刻立即生效(**危险，历史bar也会触发**) |

#### 委托状态 EEntrustStatus

| 值 | 含义 |
|----|------|
| 49 | 待报 |
| 50 | 已报 |
| 51 | 已报待撤 |
| 52 | 部成待撤 |
| 53 | 部撤 |
| 54 | 已撤 |
| 55 | 部成 |
| 56 | 已成 |
| 57 | 废单 |

---

### 2.6 系统函数与ContextInfo属性

#### ContextInfo 关键属性

| 属性 | 说明 |
|------|------|
| `ContextInfo.start/end` | 回测起止时间(仅init中设置) |
| `ContextInfo.capital` | 回测初始资金(默认100万) |
| `ContextInfo.period` | 当前周期('1d','1m'等) |
| `ContextInfo.barpos` | 当前K线索引号 |
| `ContextInfo.stockcode` | 当前主图代码 |
| `ContextInfo.market` | 当前主图市场 |
| `ContextInfo.do_back_test` | 是否回测模式 |
| `ContextInfo.is_last_bar()` | 是否最后一根bar |
| `ContextInfo.set_account(account)` | 设置监控账号(回调生效) |

#### 系统函数

| 函数 | 用途 |
|------|------|
| `timetag_to_datetime(timetag, format)` | 时间戳转日期字符串 |
| `ContextInfo.run_time(funcName, period, startTime)` | 注册定时任务 |
| `ContextInfo.subscribe_quote(...)` | 订阅行情 |
| `ContextInfo.unsubscribe_quote(num)` | 取消订阅 |
| `ContextInfo.get_stock_list_in_sector(sector)` | 获取板块股票 |
| `ContextInfo.get_stock_name(code)` | 获取证券名称 |
| `ContextInfo.get_bar_timetag(pos)` | 获取bar时间戳 |
| `ContextInfo.get_instrumentdetail(code)` | 获取合约详情 |
| `ContextInfo.get_market_data_ex(...)` | 获取行情数据 |
| `ContextInfo.get_full_tick(stock_list)` | 获取全推数据 |
| `ContextInfo.get_last_volume(code)` | 获取最新成交量 |
| `ContextInfo.paint(name, value, index, style)` | 画图 |
| `ContextInfo.draw_text(cond, pos, text)` | 绘制文字 |

---

### 2.7 入门与最佳实践

#### 策略骨架 (handlebar模式)

```python
#coding:gbk
def init(ContextInfo):
    # 初始化：设置参数、账号等
    pass

def handlebar(ContextInfo):
    # 每根K线触发一次
    if not ContextInfo.is_last_bar():
        return
    # ... 交易逻辑
    pass
```

#### 回测 vs 实盘关键差异

| 特性 | 回测 | 实盘 |
|------|------|------|
| 数据源 | 本地数据(subscribe=False) | 订阅数据(subscribe=True) |
| 撮合规则 | 高低点间按指定价, 超出按收盘价 | 交易所实际撮合 |
| passorder | 记录信号，不真实下单 | 真实下单 |
| 账号ID | 任意字符串 | 必须是真实账号 |
| is_last_bar() | 固定为True | 实时判断 |

#### Python 3.6 兼容性注意事项

- QMT内置Python为3.6.8版本
- 不能用 walrus operator `:=`
- 不能用 `dict | dict` 合并
- 不能用 f-string 中的 `=` 号
- 不能用 `pd.DataFrame.map()` 的某些新参数

---

## 三、已知知识缺口 (Gap Analysis)

### 3.1 完全缺失的内容

| 缺口 | 说明 | 优先级 |
|------|------|--------|
| **xtquant 外部调用文档** | MiniQMT已停服，但xtquant作为独立库的使用文档未收录 | 高 |
| **passorder 错误码完整列表** | 只有枚举值，缺少每个错误码的具体含义和处理建议 | 高 |
| **多账号/组合交易完整示例** | 有passorder组合交易参数，但缺少完整的篮子交易实战示例 | 中 |
| **融资融券完整实战** | 有函数定义，但缺少从开户到下单的完整流程示例 | 中 |
| **ETF申赎交易** | 有枚举值(60=申购,61=赎回)，但无实际使用示例 | 低 |
| **可转债转股/回售** | 有枚举值(80-83)，但无使用示例 | 低 |
| **期货交易完整示例** | 有buy_open/sell_open等函数，但无完整期货策略示例 | 低 |
| **期权交易** | 有枚举值(50-59)，但无期权策略示例 | 低 |

### 3.2 内容不完整或过时

| 问题 | 说明 |
|------|------|
| **miniQMT状态** | 知识库中多处提到miniQMT，但已停服(2026.7.6)，需标注 |
| **xtquant外部调用** | wtsolutions/miniqmt-termination.md提到迁移方案，但未与API文档整合 |
| **回调函数参数** | wtsolutions/qmt-order-deal-callback.md指出回调有3个参数(account)，但knowledge目录中的回调只有2个参数(ContextInfo, info)——可能是不同版本差异 |
| **行情订阅上限** | 不同文档提到不同数字(300/500)，需统一确认 |
| **get_market_data已弃用** | 知识库中标注"不再推荐使用"，但仍大量出现在示例中 |

### 3.3 实战经验缺口

| 缺口 | 说明 |
|------|------|
| **QMT回测无交易记录问题** | CLAUDE.md陷阱#17提到"passorder调用成功但回测界面无记录"，但knowledge中未收录此问题 |
| **passorder在回测中不生效的排查** | 常见问题中提到，但缺少系统性的排查清单 |
| **ContextInfo逐K线保存机制的坑** | 常见问题中有说明，但缺少与passorder quickTrade配合的最佳实践 |
| **多策略并发执行** | 提到"所有策略在同一线程"，但缺少并发策略的架构指导 |
| **日线级别策略的passorder延迟** | 日线周期下passorder延迟到下一个分笔才生效，容易被忽视 |

---

## 四、知识库使用建议

### 对QMT adapter开发最关键的信息

1. **passorder参数** (交易函数.md + 枚举常量.md): `passorder(23, 1101, account, code, 5, -1, vol, name, 0, remark, ContextInfo)` — 股票买入标准用法
2. **get_trade_detail_data** (交易函数.md): 查询ACCOUNT/POSITION/ORDER/DEAL
3. **get_market_data_ex** (行情函数.md): `subscribe=False`用于本地数据, `subscribe=True`用于实时
4. **回调函数** (成交回报实时主推函数.md): order_callback/deal_callback的结构
5. **数据结构** (数据结构.md): Account/Position/Order/Deal的字段定义
6. **ContextInfo属性** (变量约定.md): is_last_bar(), set_account(), period等

### 推荐阅读顺序

1. `01-入门/快速开始.md` → 了解整体架构
2. `03-数据与枚举/枚举常量.md` → 掌握passorder参数取值
3. `02-API/交易函数.md` → 核心交易API
4. `03-数据与枚举/数据结构.md` → 返回对象字段
5. `02-API/成交回报实时主推函数.md` → 回调机制
6. `wtsolutions/qmt-passorder-guide.md` → passorder实战详解
7. `wtsolutions/qmt-order-deal-callback.md` → 回调实战
