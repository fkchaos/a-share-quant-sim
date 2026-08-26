# QMT API 使用指南 — 来源: WTSolutions + 官方文档

> 本文档综合了 invest.wtsolutions.cn 博客系列文章和 QMT 官方 Python API 文档。
> 更新时间: 2026-08-26

---

## 目录

1. [passorder 下单函数详解](#1-passorder-下单函数详解)
2. [order_callback / deal_callback 回调详解](#2-order_callback--deal_callback-回调详解)
3. [get_trade_detail_data 交易明细查询](#3-get_trade_detail_data-交易明细查询)
4. [常见错误与注意事项](#4-常见错误与注意事项)
5. [完整代码示例](#5-完整代码示例)

---

## 1. passorder 下单函数详解

> 来源: https://invest.wtsolutions.cn/posts/qmt-passorder-guide/

### 函数签名

```python
passorder(opType, orderType, accountid, orderCode, prType, price, volume,
          strategyName, quickTrade, userOrderId, ContextInfo)
```

### 参数逐个详解

#### 1. opType — 买卖类型（操作类型）

| opType | 含义 | 适用账户 |
|--------|------|---------|
| **23** | 股票买入 | STOCK 普通账户 / 沪港通 / 深港通 |
| **24** | 股票卖出 | STOCK 普通账户 / 沪港通 / 深港通 |

**期货六键：**

| opType | 含义 |
|--------|------|
| 0 | 开多 |
| 1 | 平昨多 |
| 2 | 平今多 |
| 3 | 开空 |
| 4 | 平昨空 |
| 5 | 平今空 |

**期货四键：**

| opType | 含义 |
|--------|------|
| 6 | 平多, 优先平今 |
| 7 | 平多, 优先平昨 |
| 8 | 平空, 优先平今 |
| 9 | 平空, 优先平昨 |

**期货两键：**

| opType | 含义 |
|--------|------|
| 10 | 卖出, 有多仓优先平仓(平今优先), 余量再开空 |
| 11 | 卖出, 有多仓优先平仓(平昨优先), 余量再开空 |
| 12 | 买入, 有空仓优先平仓(平今优先), 余量再开多 |
| 13 | 买入, 有空仓优先平仓(平昨优先), 余量再开多 |
| 14 | 买入, 不优先平仓 |
| 15 | 卖出, 不优先平仓 |

**融资融券：**

| opType | 含义 |
|--------|------|
| 27 | 融资买入 |
| 28 | 融券卖出 |
| 29 | 买券还券 |
| 30 | 直接还券 |
| 31 | 卖券还款 |
| 32 | 直接还款 |
| 33 | 信用账号股票买入 |
| 34 | 信用账号股票卖出 |

**组合交易：**

| opType | 含义 |
|--------|------|
| 25 | 组合买入（含沪港通/深港通） |
| 26 | 组合卖出（含沪港通/深港通） |
| 40 | 期货组合开多 |
| 43 | 期货组合开空 |
| 46 | 期货组合平多, 优先平今 |
| 47 | 期货组合平多, 优先平昨 |
| 48 | 期货组合平空, 优先平今 |
| 49 | 期货组合平空, 优先平昨 |

**期权交易：**

| opType | 含义 |
|--------|------|
| 50 | 买入开仓 |
| 51 | 卖出平仓 |
| 52 | 卖出开仓 |
| 53 | 买入平仓 |
| 54 | 备兑开仓 |
| 55 | 备兑平仓 |
| 56 | 认购行权 |
| 57 | 认沽行权 |
| 58 | 证券锁定 |
| 59 | 证券解锁 |

#### 2. orderType — 委托类型

做股票策略，**无脑用 `1101`** 即可；做期货期权再查阅官方文档对应类型。

| orderType | 说明 |
|-----------|------|
| **1101** | 单股、单账号、普通、股/手方式下单 |
| 1102 | 单股、单账号、普通、金额(元)方式下单（只支持股票） |
| 1113 | 单股、单账号、总资产、比例[0~1]方式下单 |
| 1123 | 单股、单账号、可用、比例[0~1]方式下单 |
| 1201 | 单股、账号组(无权重)、普通、股/手方式下单 |
| 1202 | 单股、账号组(无权重)、普通、金额(元)方式下单（只支持股票） |
| 1213 | 单股、账号组(无权重)、总资产、比例[0~1]方式下单 |
| 1223 | 单股、账号组(无权重)、可用、比例[0~1]方式下单 |
| 2101 | 组合、单账号、普通、按组合股票数量方式下单 |
| 2102 | 组合、单账号、普通、按组合股票权重方式下单 |
| 2103 | 组合、单账号、普通、按账号可用方式下单 |
| 2201 | 组合、账号组(无权重)、普通、按组合股票数量方式下单 |
| 2202 | 组合、账号组(无权重)、普通、按组合股票权重方式下单 |
| 2203 | 组合、账号组(无权重)、普通、按账号可用方式下单 |
| 2331 | 组合、套利、合约价值自动套利、按组合股票数量方式下单 |
| 2332 | 组合、套利、按合约价值自动套利、按组合股票权重方式下单 |
| 2333 | 组合、套利、按合约价值自动套利、按账号可用方式下单 |

> 注：期货不支持 1102 和 1202

#### 3. accountid — 资金账号

字符串，就是模型交易界面选择的账号，例如 `'88888888'`。一般直接用界面传进来的 `ContextInfo` 里的账号。

#### 4. orderCode — 证券代码

字符串，**必须带后缀**：
- 上海: `'600000.SH'`
- 深圳: `'000001.SZ'`

> ⚠️ `'600000'` 不行，必须 `'600000.SH'`。ETF基金、可转债也用股票接口。

#### 5. prType — 报价类型（报价方式）

| prType | 含义 | price参数 |
|--------|------|----------|
| **5** | 最新价 | 填任意值（如 -1） |
| **11** | 指定价/模型价 | 必须填具体价格 |
| **14** | 对手价 | 填 -1（必须！） |
| **49** | 科创板盘后定价 | 填具体价格 |
| 0 | 卖5价 | 无效 |
| 1 | 卖4价 | 无效 |
| 2 | 卖3价 | 无效 |
| 3 | 卖2价 | 无效 |
| 4 | 卖1价 | 无效 |
| 6 | 买1价 | 无效 |
| 7 | 买2价 | 无效 |
| 8 | 买3价 | 无效 |
| 9 | 买4价 | 无效 |
| 10 | 买5价 | 无效 |
| 12 | 涨跌停价 | 无效 |
| 13 | 挂单价 | 无效 |

> 注：只有 `prType=11` 或 `49` 时 `price` 才有效；其他情况 `price` 填 -1/0/2/100 等任意数字。

#### 6. price — 下单价格

仅当 `prType=11`(模型价) 或 `49`(科创板盘后定价) 时有效。

#### 7. volume — 下单数量

根据 `orderType` 最后一位确定单位：
- `1`：股
- `2`：金额（元）
- `3`：比例（%）

#### 8. strategyName — 策略名

字符串，给委托打一个策略标签，方便在终端委托列表里区分多个策略的单子。**可以随便填**。

#### 9. quickTrade — 快速下单

| 值 | 含义 |
|----|------|
| **1** | 快速通道（**推荐**，委托更快落地） |
| 0 | 普通流程 |
| 2 | 不判断bar状态，只要调用就触发（历史bar也能触发，**谨慎使用**） |

> **quickTrade 行为差异：**
> - `quickTrade=0`：对最后一根K线完全走完后生成的模型信号，在下一根K线的第一个tick数据来时触发下单
> - `quickTrade=1`：非历史bar上执行时（`ContextInfo.is_last_bar()` 为 True），只要策略模型中调用到就触发下单
> - `quickTrade=2`：不判断bar状态，只要调用到就触发，历史bar上也能触发，请谨慎使用

#### 10. userOrderId — 自定义订单标识（**非常关键**）

字符串"备注"，会随委托一路传递。你在 `order_callback` / `deal_callback` 里就是靠它把"哪一笔回来了"对上的。

**建议格式：** "策略名 + 动作 + 时间"，唯一可读：

```python
msg = f"双均线 {stock} 买入 {vol}股 {datetime.datetime.now():%H%M%S}"
```

> 如果传入 `userOrderId`，前面的 `strategyName` 和 `quickTrade` 参数也必须填写。

**实际对应关系：** `userOrderId` 对应 order 委托对象和 deal 成交对象中的 `m_strRemark` 属性，通过 `get_trade_detail_data` 函数或委托主推函数 `order_callback` 和成交主推函数 `deal_callback` 可拿到这两个对象信息。

#### 11. ContextInfo — 策略上下文

Python 对象，由系统自动传入，直接使用即可。

### passorder 用法总结

`passorder` 参数拆开后其实就是三件事：
1. **买卖谁** → opType + orderCode
2. **用什么价** → prType + price
3. **下多少** → volume

其余参数是账号、策略标签和订单标识。把 `userOrderId` 用好，配合回调，就能把"下单→确认→防重复"这套实盘必备的闭环跑通。

---

## 2. order_callback / deal_callback 回调详解

> 来源: https://invest.wtsolutions.cn/posts/qmt-order-deal-callback/

### 委托回调 order_callback

```python
def order_callback(stock_code, order_volume, price, order_status, order_sysid, strategy_name, accountid, user_order_id, ContextInfo):
    """
    stock_code: 证券代码，如 '600000.SH'
    order_volume: 委托数量（股）
    price: 委托价格
    order_status: 委托状态
        - 0: 已报
        - 1: 废单
        - 2: 部分成交
        - 3: 已成（全部成交）
        - 4: 已撤
        - 5: 未知
    order_sysid: 交易所委托编号
    strategy_name: 策略名（passorder 中的 strategyName）
    accountid: 资金账号
    user_order_id: 用户自定义订单ID（passorder 中的 userOrderId）
    ContextInfo: 策略上下文
    """
    # 处理逻辑...
```

### 成交回调 deal_callback

```python
def deal_callback(stock_code, deal_volume, deal_price, order_sysid, strategy_name, accountid, user_order_id, ContextInfo):
    """
    stock_code: 证券代码
    deal_volume: 成交数量（股）
    deal_price: 成交价格
    order_sysid: 交易所委托编号
    strategy_name: 策略名
    accountid: 资金账号
    user_order_id: 用户自定义订单ID
    ContextInfo: 策略上下文
    """
    # 处理逻辑...
```

### 回调注册方式

在 `init` 函数中注册：

```python
def init(ContextInfo):
    # 注册委托回调
    ContextInfo.set_callback('order_callback', order_callback_func)
    # 注册成交回调
    ContextInfo.set_callback('deal_callback', deal_callback_func)
```

### 回调中的状态码

| 状态码 | 含义 | 说明 |
|--------|------|------|
| 0 | 已报 | 委托已提交到交易所 |
| 1 | 废单 | 委托被拒绝（如资金不足、涨跌停等） |
| 2 | 部分成交 | 委托部分已成交 |
| 3 | 已成 | 全部成交 |
| 4 | 已撤 | 委托已撤销 |
| 5 | 未知 | 未知状态 |

### userOrderId 在回调中的使用

`userOrderId` 是通过 `passorder` 下单时传入的自定义标识，在回调中作为参数返回。**这是关联下单和回调的核心机制**。

```python
# 下单时传入唯一标识
user_id = f"策略A_买入_{stock}_{datetime.now():%Y%m%d%H%M%S}"
passorder(23, 1101, ContextInfo.accID, stock, 5, -1, 100,
          "策略A", 1, user_id, ContextInfo)

# 在回调中通过 user_order_id 对应
def order_callback(stock_code, order_volume, price, order_status,
                   order_sysid, strategy_name, accountid, user_order_id, ContextInfo):
    if user_order_id == user_id:
        # 这是我们的单子
        if order_status == 3:
            print(f"已成: {stock_code}, 数量: {order_volume}")
        elif order_status == 1:
            print(f"废单: {stock_code}")
```

### 回调使用最佳实践

1. **防重复下单**：handlebar 高频触发，下单后必须登记"待确认"状态，等回调或查到委托后再放行下一笔
2. **状态管理**：用 `userOrderId` 作为 key，在全局字典中追踪每笔委托状态
3. **废单处理**：回调状态码为 1（废单）时要记录原因并更新状态
4. **部分成交**：状态码为 2（部分成交）时需要等待全部成交或主动撤单

---

## 3. get_trade_detail_data 交易明细查询

> 来源: QMT官方Python API文档 + WTSolutions博客

### 函数签名

```python
get_trade_detail_data(accountID, strAccountType, strDatatype [, strategyName])
```

### 参数说明

| 参数 | 类型 | 说明 |
|------|------|------|
| accountID | string | 资金账号 |
| strAccountType | string | 账号类型（见下表） |
| strDatatype | string | 数据类型（见下表） |
| strategyName | string | 策略名，只对 ORDER/DEAL 起作用，可省略 |

**strAccountType 可选值：**

| 值 | 含义 |
|----|------|
| 'FUTURE' | 期货 |
| 'STOCK' | 股票 |
| 'CREDIT' | 信用 |
| 'HUGANGTONG' | 沪港通 |
| 'SHENGANGTONG' | 深港通 |
| 'STOCK_OPTION' | 期权 |

**strDatatype 可选值：**

| 值 | 含义 | 说明 |
|----|------|------|
| 'POSITION' | 持仓 | 返回持仓列表 |
| 'ORDER' | 委托 | 返回委托列表 |
| 'DEAL' | 成交 | 返回成交列表 |
| 'ACCOUNT' | 账号 | 返回账号信息 |
| 'TASK' | 任务 | 返回智能算法任务信息 |

### 返回值

返回 `list`，list 中放的是 `PythonObj`，通过 `dir(pythonobj)` 可返回某个对象的属性列表。

### 常用对象属性

**ORDER 委托对象常用属性：**

| 属性 | 说明 |
|------|------|
| m_strInstrumentID | 证券代码 |
| m_nVolume | 委托数量 |
| m_dPrice | 委托价格 |
| m_strOrderSysID | 交易所委托编号 |
| m_nOrderStatus | 委托状态 |
| m_strRemark | 备注（即 userOrderId） |
| m_strAccountID | 资金账号 |

**DEAL 成交对象常用属性：**

| 属性 | 说明 |
|------|------|
| m_strInstrumentID | 证券代码 |
| m_nVolume | 成交数量 |
| m_dPrice | 成交价格 |
| m_strOrderSysID | 交易所委托编号 |
| m_strRemark | 备注（即 userOrderId） |

**POSITION 持仓对象常用属性：**

| 属性 | 说明 |
|------|------|
| m_strInstrumentID | 证券代码 |
| m_nVolume | 持仓数量 |
| m_dOpenPrice | 开仓价格 |
| m_dSettlementPrice | 结算价 |
| m_nCanUseVolume | 可用数量 |

**ACCOUNT 账号对象常用属性：**

| 属性 | 说明 |
|------|------|
| m_dAvailable | 可用资金 |
| m_dBalance | 总资产 |
| m_dFrozenBalance | 冻结资金 |

### 使用示例

```python
def handlebar(ContextInfo):
    # ---- 查询持仓 ----
    obj_list = get_trade_detail_data('6000000248', 'stock', 'position')
    for obj in obj_list:
        print(obj.m_strInstrumentID)  # 证券代码
        print(obj.m_nVolume)          # 持仓数量

    # ---- 查看所有属性字段 ----
    print(dir(obj))

    # ---- 可用资金查询 ----
    acct_info = get_trade_detail_data('6000000248', 'stock', 'account')
    for i in acct_info:
        print(i.m_dAvailable)  # 可用资金

    # ---- 当前持仓查询 ----
    position_info = get_trade_detail_data('6000000248', 'stock', 'position')
    for i in position_info:
        print(i.m_strInstrumentID, i.m_nVolume)

    # ---- 按策略名查询委托（过滤） ----
    order_info = get_trade_detail_data('6000000248', 'stock', 'order', '我的策略')
    for i in order_info:
        print(i.m_strInstrumentID, i.m_nVolume, i.m_strOrderSysID)
```

### 关联函数

#### get_last_order_id — 获取最新委托号

```python
get_last_order_id(accountID, strAccountType, strDatatype [, strategyName])
# 返回: string，委托号，找不到返回 '-1'
```

#### get_value_by_order_id — 根据委托号查询

```python
get_value_by_order_id(orderId, accountID, strAccountType, strDatatype)
# 返回: pythonObj

# 示例
obj = get_value_by_order_id(orderid, ContextInfo.accid, 'stock', 'order')
print(obj.m_strInstrumentID)
print(obj.m_strRemark)  # userOrderId
```

### 查询-下单-撤单 完整流程

```
(1) get_trade_detail_data → 判定资金/持仓/登录状态
(2) passorder → 下单
(3) get_last_order_id → 获取委托号
(4) get_value_by_order_id → 查看委托状态
    当状态变成"已成" → 对应 deal 信息有一条成交数据
(5) cancel → 根据委托状态撤单
```

> **注意：** 委托列表和成交列表中的委托号是一样的，都是 `m_strOrderSysID` 属性值。

---

## 4. 常见错误与注意事项

> 来源: WTSolutions博客 + 官方文档

### 4.1 代码必须带后缀

```python
# ❌ 错误
passorder(23, 1101, accountid, '600000', ...)

# ✅ 正确
passorder(23, 1101, accountid, '600000.SH', ...)
```

### 4.2 数量必须是100的整数倍

买入数量必须是100股的整数倍（1手=100股），否则会被柜台风控驳回。

### 4.3 防重复下单（userOrderId的用途）

`handlebar` 在盘中会被高频触发，下单后**一定要登记"待确认"状态**，等回调或查到委托后再放行下一笔，否则容易超单。

```python
# 用全局字典追踪委托状态
pending_orders = {}

def handlebar(ContextInfo):
    for stock in watchlist:
        if stock in pending_orders:
            continue  # 已有未完成委托，跳过
        
        user_id = f"{stock}_{datetime.now():%Y%m%d%H%M%S}"
        passorder(23, 1101, ContextInfo.accID, stock, 5, -1, 100,
                  "策略A", 1, user_id, ContextInfo)
        pending_orders[stock] = user_id

def order_callback(stock_code, order_volume, price, order_status,
                   order_sysid, strategy_name, accountid, user_order_id, ContextInfo):
    # 委托确认后从 pending 中移除
    if stock_code in pending_orders:
        del pending_orders[stock_code]
```

### 4.4 市价单价格必须填-1

`prType=14`（对手价）时 `price` 必须填 `-1`，填了正数反而可能被当成限价单。

### 4.5 涨跌停板处理

涨停板买入 / 跌停板卖出基本不会成交，下单前最好用最新价对比涨跌停价做拦截。

### 4.6 quickTrade 的坑

- `quickTrade=1` 只在**当前bar**（非历史bar）时立即触发
- `quickTrade=2` 在历史bar也触发，实盘**不要用**
- `quickTrade=0` 在下一根K线的第一个tick才触发（有延迟）

### 4.7 策略延迟问题

> 来源: https://invest.wtsolutions.cn/posts/qmt-server-delay/

策略在QMT中运行时，由于服务器通信延迟，实际下单时间可能比信号产生时间晚几秒。这在盘中高速行情时尤为明显。建议：
- 使用 `quickTrade=1` 减少延迟
- 关键价位不要依赖精确时间
- 回测和实盘的成交价可能不同

---

## 5. 完整代码示例

### 示例1: 基础买入卖出

```python
def init(ContextInfo):
    ContextInfo.accID = '88888888'  # 你的资金账号

def handlebar(ContextInfo):
    # 以最新价买入 100 股平安银行
    passorder(23, 1101, ContextInfo.accID, '000001.SZ', 5, -1, 100,
              "我的策略", 1, "", ContextInfo)
    
    # 以指定价卖出
    passorder(24, 1101, ContextInfo.accID, '000001.SZ', 11, 15.50, 100,
              "我的策略", 1, "", ContextInfo)
```

### 示例2: 带防重复的完整策略框架

```python
import datetime

def init(ContextInfo):
    ContextInfo.accID = '88888888'
    ContextInfo.pending = {}  # 追踪待确认委托

def handlebar(ContextInfo):
    # 查可用资金
    acct = get_trade_detail_data(ContextInfo.accID, 'stock', 'account')
    available = acct[0].m_dAvailable if acct else 0
    
    if available < 5000:
        return  # 资金不足
    
    stock = '000001.SZ'
    if stock in ContextInfo.pending:
        return  # 已有待确认委托
    
    # 下单
    user_id = f"买入_{stock}_{datetime.datetime.now():%H%M%S}"
    passorder(23, 1101, ContextInfo.accID, stock, 5, -1, 100,
              "防重复策略", 1, user_id, ContextInfo)
    ContextInfo.pending[stock] = user_id
```

### 示例3: 用 get_trade_detail_data 查询全部持仓

```python
def handlebar(ContextInfo):
    # 查询所有持仓
    positions = get_trade_detail_data(ContextInfo.accID, 'stock', 'position')
    for pos in positions:
        code = pos.m_strInstrumentID
        vol = pos.m_nVolume
        open_price = pos.m_dOpenPrice
        print(f"持仓: {code}, 数量: {vol}, 开仓价: {open_price}")
    
    # 查询所有委托
    orders = get_trade_detail_data(ContextInfo.accID, 'stock', 'order')
    for order in orders:
        code = order.m_strInstrumentID
        vol = order.m_nVolume
        status = order.m_nOrderStatus
        remark = order.m_strRemark  # 这就是 userOrderId
        print(f"委托: {code}, 数量: {vol}, 状态: {status}, 备注: {remark}")
```

### 示例4: algo_passorder 算法交易下单

```python
def handlebar(ContextInfo):
    # 使用TWAP算法分拆下单
    algo_passorder(
        23,               # opType: 股票买入
        1101,             # orderType: 单股普通
        '6000000248',     # accountid
        '000001.SZ',      # orderCode
        5,                # prType: 最新价
        -1,               # price
        50000,            # volume: 50000股
        "策略名",          # strategyName (不可缺省)
        1,                # quickTrade
        "备注",            # userOrderId (不可缺省)
        "TWAP",           # smartAlgoType: 算法类型
        20,               # limitOverRate: 量比 20%
        0,                # minAmountPerOrder: 最小委托金额
        ContextInfo
    )
```

### 示例5: 取消指定委托号的委托

```python
def init(ContextInfo):
    ContextInfo.accid = '6000000248'

def handlebar(ContextInfo):
    # 1. 查询所有委托
    obj_list = get_trade_detail_data(ContextInfo.accid, 'stock', 'order')
    for obj in obj_list:
        # 2. 找到可撤销的委托
        orderid = obj.m_strOrderSysID
        can_cancel = can_cancel_order(orderid, ContextInfo.accid, 'stock')
        if can_cancel:
            # 3. 撤单
            cancel(orderid, ContextInfo.accid, 'stock', ContextInfo)
```

---

## 附录: 速查表

### 股票下单速查

| 场景 | 代码 |
|------|------|
| 股票买入(最新价) | `passorder(23, 1101, accid, '600000.SH', 5, -1, 100, name, 1, uid, ctx)` |
| 股票卖出(最新价) | `passorder(24, 1101, accid, '600000.SH', 5, -1, 100, name, 1, uid, ctx)` |
| 股票买入(指定价) | `passorder(23, 1101, accid, '600000.SH', 11, 10.50, 100, name, 1, uid, ctx)` |
| 股票卖出(指定价) | `passorder(24, 1101, accid, '600000.SH', 11, 12.00, 100, name, 1, uid, ctx)` |
| 股票买入(金额) | `passorder(23, 1102, accid, '600000.SH', 5, -1, 10000, name, 1, uid, ctx)` |
| 融资买入 | `passorder(27, 1101, accid, '600000.SH', 5, -1, 100, name, 1, uid, ctx)` |
| 融券卖出 | `passorder(28, 1101, accid, '600000.SH', 5, -1, 100, name, 1, uid, ctx)` |

### 数据查询速查

| 需求 | 代码 |
|------|------|
| 查持仓 | `get_trade_detail_data(accid, 'stock', 'position')` |
| 查委托 | `get_trade_detail_data(accid, 'stock', 'order')` |
| 查成交 | `get_trade_detail_data(accid, 'stock', 'deal')` |
| 查资金 | `get_trade_detail_data(accid, 'stock', 'account')` |
| 按策略过滤 | `get_trade_detail_data(accid, 'stock', 'order', '策略名')` |
| 查最新委托号 | `get_last_order_id(accid, 'stock', 'order')` |
| 查委托详情 | `get_value_by_order_id(orderid, accid, 'stock', 'order')` |
| 查是否可撤 | `can_cancel_order(orderid, accid, 'stock')` |
| 撤单 | `cancel(orderid, accid, 'stock', ContextInfo)` |

---

## 参考链接

- [WTSolutions: passorder 参数详解](https://invest.wtsolutions.cn/posts/qmt-passorder-guide/)
- [WTSolutions: 委托回调与成交回调](https://invest.wtsolutions.cn/posts/qmt-order-deal-callback/)
- [WTSolutions: 服务延迟问题](https://invest.wtsolutions.cn/posts/qmt-server-delay/)
- [QMT Python API 官方文档](https://qmt.ptradeapi.com/QMT_Python_API_Doc.html)
