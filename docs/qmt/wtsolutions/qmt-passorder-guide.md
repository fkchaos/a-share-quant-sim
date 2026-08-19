# 讯投QMT使用小技巧: 下单函数 passorder 参数详解与实战

> 来源: https://invest.wtsolutions.cn/posts/qmt-passorder-guide/

## 概述

`passorder` 是 QMT 中最核心的下单函数，几乎所有实盘策略最终都要通过它把信号变成真实的委托。但它的参数有 11 个，官方文档对每个参数的取值又多以代码表形式罗列，新手第一眼常常懵。

本文把 `passorder` 的每个参数拆开讲透，并给出股票、两融账户的常用取值，最后用一段可直接运行的实战代码串起来。

> 完整的取值代码表（如期货、期权的 opType/orderType）请以官方文档为准，可参考 [QMT 知识库 / XtQuant 使用文档 pdf](../qmt-knowledge-base)。

## 函数签名

```
passorder(opType, orderType, accountid, orderCode, prType, price, volume, strategyName, quickOrder, userOrderId, ContextInfo)
```

## 参数逐个详解

### 1. opType —— 买卖类型

最常用的几个：

| opType | 含义 | 适用账户 |
| --- | --- | --- |
| 23 | 股票买入 | STOCK 普通账户 |
| 24 | 股票卖出 | STOCK 普通账户 |

### 2. orderType —— 委托类型

* `1101`：单边，股票普通买卖最常用，按股数委托

做股票策略，无脑用 `1101` 即可；做期货期权再查阅官方文档对应类型。

### 3. accountid —— 资金账号

字符串，就是模型交易界面选择的账号，例如 `'88888888'`。一般直接用界面传进来的 `account` 变量。

### 4. orderCode —— 证券代码

带交易所后缀的代码，格式 `'代码.市场'`：

* 沪市：`'600000.SH'`
* 深市：`'000001.SZ'`
* 北交所：`'430047.BJ'`

注意：很多新手会漏掉后缀，导致报"找不到合约"之类的错误。

### 5. prType —— 价格类型

| prType | 含义 | price 参数怎么填 |
| --- | --- | --- |
| 11 | 限价委托 | 填具体的限价金额 |

实战中最常用两种：**限价单**（`prType=11`，自己给价）和**最新价单**（`prType=14`，`price=-1`，跟盘口最新成交价走）。

### 6. price —— 委托价格

* 限价单：填你想要的价格，例如 `10.55`
* 市价/最新价单：填 `-1`，由 `prType` 决定实际价格

### 7. volume —— 委托数量

A 股买入需为 100 的整数倍（1 手 = 100 股），卖出不足 100 股的零股需单独处理。

### 8. strategyName —— 策略名称

字符串，给委托打一个策略标签，方便在终端委托列表里区分多个策略的单子。

### 9. quickOrder —— 快速下单

* `1`：快速通道（推荐，委托更快落地）
* `0`：普通流程

样例策略里基本都用 `1`。

### 10. userOrderId —— 自定义订单标识

非常关键的一个参数。它是一个字符串"备注"，会随委托一路传递。你在 `order_callback` / `deal_callback` 里就是靠它把"哪一笔回来了"对上的。建议用"策略名 + 动作 + 时间"这种唯一可读的格式：

```python
msg = f"双均线 {stock} 买入 {vol}股 {datetime.datetime.now():%H%M%S}"
```

具体怎么用回调匹配，看下一篇文章 [委托回调与成交回调的正确用法](../qmt-order-deal-callback)。

### 11. ContextInfo —— 上下文对象

策略上下文，照传 `C` / `ContextInfo` 即可。

## 撤单

撤单用 `cancel` 函数，传入系统委托号 `m_strOrderSysID`：

```python
# 撤掉某笔委托
cancel(order_sysid, acct, acct_type)
```

`order_sysid` 可通过 `get_trade_detail_data(acct, acct_type, 'order')` 查到。

## 常见错误与注意事项

1. **代码漏后缀**：`'600000'` 不行，必须 `'600000.SH'`。
2. **数量不是 100 整数倍**：买入会被柜台风控驳回。
3. **没做防重复**：`handlebar` 在盘中会被高频触发，下单后一定要登记"待确认"状态，等回调或查到委托后再放行下一笔，否则容易超单。这正是 `userOrderId` 存在的意义。
4. **市价单价格填错**：`prType=14` 时 `price` 必须填 `-1`，填了正数反而可能被当成限价。
5. **涨跌停板**：涨停板买入 / 跌停板卖出基本不会成交，下单前最好用最新价对比涨跌停价做拦截。

## 总结

`passorder` 看着参数多，拆开后其实就三件事：**买卖谁（opType + orderCode）、用什么价（prType + price）、下多少（volume）**，其余参数是账号、策略标签和订单标识。把 `userOrderId` 用好，配合回调，就能把"下单—确认—防重复"这套实盘必备的闭环跑通。
