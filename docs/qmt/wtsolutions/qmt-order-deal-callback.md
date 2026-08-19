# 讯投QMT使用小技巧: 委托回调 order_callback 与成交回调 deal_callback 的正确用法

> 来源: https://invest.wtsolutions.cn/posts/qmt-order-deal-callback/

## 概述

很多新手写 QMT 策略时，下单用的是 `passorder`，但"怎么知道这单到底成了没"全靠下一次 `handlebar` 里去 `get_trade_detail_data` 轮询。轮询当然能用，但有延迟、又费资源，还容易在查不到委托的瞬间误判成"没下出去"导致重复下单。

更优雅的做法是用 QMT 提供的两个回调函数：`order_callback`（委托回调）和 `deal_callback`（成交回调）。本文讲清楚它们的触发时机、参数结构，以及怎么和 `passorder` 的 `userOrderId` 串起来做一个可靠的订单状态机。

## 两个回调的触发时机

| 回调 | 触发时机 | 典型用途 |
| --- | --- | --- |
| `order_callback` | 委托被柜台接收（已报单） | 确认"单子下出去了"，从待确认列表移除 |
| `deal_callback` | 委托部分或全部成交 | 确认"真成交了"，更新持仓、发通知 |

一句话区分：`order_callback` 管的是"报单到达"，`deal_callback` 管的是"撮合成交"。一个委托可能触发一次 `order_callback`，但可能分多次 `deal_callback`（部分成交）。

## 函数签名

```python
def order_callback(ContextInfo, account, orderInfo):
    # 委托回调
    pass

def deal_callback(ContextInfo, account, dealInfo):
    # 成交回调
    pass
```

注意：这两个函数名是 QMT 约定的，**写对名字就会自动被调用**，不需要你在 `init` 里注册。

## 订单对象常用字段

`orderInfo` / `dealInfo` 是 QMT 传进来的对象，常用属性：

| 属性 | 含义 |
| --- | --- |
| `m_strInstrumentID` | 证券代码（不带后缀） |
| `m_strExchangeID` | 交易所，如 `'SH'`、`'SZ'` |
| `m_strRemark` | 你下单时传的 `userOrderId`（备注） |
| `m_nVolume` / `m_nCanUseVolume` | 委托/可用数量 |
| `m_dPrice` | 委托/成交价格 |

其中 `m_strRemark` 最关键——它是你和 `passorder` 之间的"暗号"。下单时写进去，回调时读出来，就能精确定位是哪一笔单子回来了。

## 用 userOrderId 把下单和回调串起来

下面是一个最小可用的"下单 → 委托确认 → 成交确认"状态机：

```python
# -*- coding: utf-8 -*-
#encoding:gbk
import datetime

class a(): pass
A = a()

def init(C):
    A.acct = account
    A.acct_type = accountType
    A.buy_code = 23 if A.acct_type == 'STOCK' else 33
    # 订单状态字典：remark -> {'status': 'pending'/'ordered'/'filled', ...}
    A.orders = {}

def place_buy(C, stock, vol, price):
    """下单并登记到状态机"""
    remark = f"buy-{stock}-{datetime.datetime.now():%H%M%S%f}"
    passorder(A.buy_code, 1101, A.acct, stock, 0, price, vol,
              '回调示例', 1, remark, C)
    A.orders[remark] = {
        'status': 'pending',  # 已调用passorder，等柜台回报
        'stock': stock,
        'vol': vol,
        'price': price,
        'filled': 0,
    }
    print(f"已下单 {remark}")

def order_callback(C, acct, orderInfo):
    """委托被柜台接收"""
    remark = orderInfo.m_strRemark
    if remark in A.orders:
        A.orders[remark]['status'] = 'ordered'
        print(f"委托确认 {remark} 数量{orderInfo.m_nVolume} @{orderInfo.m_dPrice}")

def deal_callback(C, acct, dealInfo):
    """成交回报"""
    remark = dealInfo.m_strRemark
    if remark in A.orders:
        o = A.orders[remark]
        o['filled'] += dealInfo.m_nVolume
        if o['filled'] >= o['vol']:
            o['status'] = 'filled'
            print(f"全部成交 {remark} 成交{dealInfo.m_nVolume}股 @{dealInfo.m_dPrice}")
            # 这里可以触发：发通知、更新目标持仓、释放下一次下单许可
```

## 为什么这样写更可靠

### 1. 不再误判"没下出去"

轮询派最大的坑是：下单后立刻去查 `get_trade_detail_data('order')`，可能那一瞬间委托还没回报，查不到，于是以为没下出去又下一笔——超单。用 `order_callback` 等回报到了再改状态，从根上避免。

### 2. 部分成交也能正确累计

一笔 1000 股的买单可能分 3 次 `deal_callback`（300 + 500 + 200）。上面代码用 `o['filled'] += dealInfo.m_nVolume` 累加，只有累计等于委托量时才标记 `filled`，符合真实撮合过程。

### 3. 失败委托可识别

如果一个 `pending` 状态的订单长时间没有 `order_callback`，基本可以判定是废单（价格不合规、数量不对、柜台风控等）。可以用一个定时任务扫描超时的 `pending` 订单，触发告警：

```python
def check_timeout(C):
    now = datetime.datetime.now()
    for remark, o in list(A.orders.items()):
        if o['status'] == 'pending':
            age = (now - o.get('ts', now)).total_seconds()
            if age > 60:
                print(f"警告：{remark} 超过60秒未确认，疑似废单")
                o['status'] = 'rejected'
```

## 配合通知推送到手机

成交回调是发通知的最佳时机——不是"下单了"就通知，而是"真成交了"才通知，避免被一堆废单打扰。把 `deal_callback` 里的 `send_notify` 接上邮件/微信推送即可。

## 注意事项

1. **回调里不要做重活**：回调在 QMT 主线程触发，里面不要写耗时 IO（比如同步发邮件阻塞几秒），否则会拖慢整个行情/交易处理。需要时把消息丢到一个队列，由定时任务慢慢发。
2. **回调里不要直接 `passorder`**：在 `deal_callback` 里立刻根据成交结果反手下另一笔是常见需求，但要确认账户状态、行情就绪，且做好防重入，否则容易连环下单。
3. **字段名以实测为准**：不同券商版本的 QMT，`orderInfo` 字段名可能有细微差别，第一次用建议先 `print(dir(orderInfo))` 看一下实际属性。
4. **回测里回调行为不同**：回测中 `deal_callback` 通常按 K 线撮合触发，频率和实盘完全不同。
5. `order_callback`和`deal_callback`中可能存在时间延迟，正常情况下是很快回报，但是有时候券商服务器可能是几个小时后才能 callback，所以建议自己增加判断是否真正下单成交，当然这种情况极少发生。
6. 当系统断网重连之后，服务器可能会把当天的所有单子全部给你 callback 一遍，所以不能完全相信 callback 的准确性，需要自己增加判断是否真正下单成交。通常是需要自己建立一个小型数据库，记录下单状态，等成交回调来更新。如果 callback 中出现的单子在数据库里面已经显示完成状态，那么可能这个 callback 就是券商服务器重复推送了。

## 总结

* `order_callback` = 报单到达，`deal_callback` = 撮合成交。
* 用 `passorder` 的 `userOrderId`（`m_strRemark`）做暗号，把下单和回调串成一个订单状态机。
* 状态机里区分 `pending / ordered / filled / rejected`，配合超时扫描，就能解决实盘最头疼的"重复下单"和"废单识别"问题。
* 回调里保持轻量，重活丢给定时任务，通知只在真正成交时发。

把这套机制跑通，你的实盘策略才谈得上"可靠"二字。
