# MiniQMT停用？HTTP桥接拯救旧策略

> 来源: https://invest.wtsolutions.cn/posts/qmt-miniqmt-bridge/

## 一、背景：MiniQMT 退出舞台，旧策略怎么办？

自 2026 年 7 月起，多家券商已陆续收紧 MiniQMT 权限，存量客户大概率会在 1～2 个月内被清退，本地原生 Python + `xtquant` 直连模式即将成为历史。

但现实是：**大多数个人量化的策略代码已经跑了很多年**——指标计算、信号判断、行情订阅都依赖 `xtquant`，迁移成本极高。重写一遍不仅费时，还容易把原本稳定的策略写崩。

那有没有办法**既不放弃 MiniQMT 端成熟的策略代码，又能把"真实下单"放到合规的大 QMT 里跑**？答案是：**HTTP 桥接**。

## 二、方案核心思想：分析在 miniQMT，下单在大 QMT

把整个交易流程拆成两段：

| 角色 | 职责 | 运行环境 |
| --- | --- | --- |
| **QMT 外 python端** | 用python 代码计算指标、产生信号、通过 HTTP 发起下单 | Python |
| **大 QMT 端** | 起一个 HTTP 服务，接收下单/撤单/查询请求，在 QMT 主线程通过 `passorder` 执行 | 大 QMT 模型交易沙箱 |

## 三、架构总览

```
+----------------------+          +----------------------+
|      外部python端     |          |      大 QMT 端       |
| +------------------+ |  HTTP    | +------------------+ |
| | xtquant 取行情    | | --POST-->| | passorder 下单    | |
| | 计算指标 / 信号    | | <-JSON--| | get_full_tick 行情 | |
| | QMTClient 下单    | |         | | get_trade_detail  | |
| +------------------+ |          | +------------------+ |
+----------------------+          +----------------------+
```

## 四、部署步骤

### 4.1 大 QMT 端（`qmt_http_server.py`）

启动后输出：

```
[Bridge] HTTP server started on 0.0.0.0:8899
[Bridge] init done. account=XXX type=STOCK consume_interval=200ms
```

### 4.2 外部 python 端（`miniqmt_client.py`）

配置连接地址：

```python
QMT_HTTP_HOST = 'http://127.0.0.1:8899'  # 跨机时改大 QMT 的实际 IP
```

## 五、代码详解

### 5.1 大 QMT 端：HTTP 服务 + 任务队列

核心是三件事：`init`、`consume_tasks`、`passorder`

**启动 HTTP 服务 + 定时器（init 钩子）**

```python
def init(ContextInfo):
    global G_CONTEXT, G_ACCOUNT, G_ACCOUNT_TYPE
    G_CONTEXT = ContextInfo
    G_ACCOUNT = account
    G_ACCOUNT_TYPE = accountType

    # 起 daemon HTTP 线程，策略停止时自动退出
    t = threading.Thread(target=_start_http_server, daemon=True)
    t.start()

    # 注册 200ms 定时器消费任务队列
    period = '%dnMilliSecond' % CONSUME_INTERVAL_MS
    ContextInfo.run_time('consume_tasks', period, '2000-01-01 00:00:00')

    print('[Bridge] init done. account=%s type=%s consume_interval=%dms'
          % (G_ACCOUNT, G_ACCOUNT_TYPE, CONSUME_INTERVAL_MS))
```

**定时器消费任务（主线程，安全调 passorder）**

```python
def consume_tasks(ContextInfo):
    batch = []
    with TASK_LOCK:
        for _ in range(MAX_BATCH_PER_TICK):
            if not TASK_QUEUE:
                break
            batch.append(TASK_QUEUE.popleft())
    if not batch:
        return

    for task in batch:
        try:
            if task['kind'] == 'order':
                passorder(
                    task['opType'], task['orderType'], task['accountid'],
                    task['orderCode'], task['prType'], task['price'],
                    task['volume'], task['strategyName'],
                    task['quickTrade'], task['userOrderId'], ContextInfo
                )
            elif task['kind'] == 'cancel':
                cancel(task['orderId'], task['accountid'],
                       task['accountType'], ContextInfo)
        except Exception as e:
            print('[Bridge] task error: %s | task=%s' % (e, task))
```

**为什么必须用队列 + 定时器，不能直接在 HTTP 线程里调 `passorder`？**

`passorder` 是 QMT 客户端交易主线程的同步调用，从其他线程直接调可能导致问题。QMT 官方文档明确要求：**所有交易相关 API 必须在策略主线程调用**。所以最稳的做法是 HTTP 线程只入队，主线程定时器出队执行。

### 5.2 miniQMT 端：策略 + HTTP 客户端封装

`QMTClient` 类把所有 HTTP 调用封装成 Python 方法：

```python
class QMTClient:
    def __init__(self, host=QMT_HTTP_HOST, timeout=HTTP_TIMEOUT):
        self.host = host.rstrip('/')
        self.timeout = timeout

    def buy_stock(self, code, volume, price=0, prType=5,
                  strategyName='', userOrderId=''):
        """股票买入：opType=23 orderType=1101（按数量）"""
        return self.order(23, 1101, code, prType, price, volume,
                          strategyName, 2, userOrderId)

    def sell_stock(self, code, volume, price=0, prType=5,
                   strategyName='', userOrderId=''):
        """股票卖出：opType=24 orderType=1101（按数量）"""
        return self.order(24, 1101, code, prType, price, volume,
                          strategyName, 2, userOrderId)
```

**行情双通道（重点）**

```python
def _get_ticks(codes):
    """优先 xtquant 取行情，否则走大 QMT HTTP"""
    # 方式 A：xtquant（miniQMT 本地行情源，无网络往返）
    if xt is not None:
        try:
            data = xt.get_full_tick(codes)
            return data or {}
        except Exception as e:
            print('[miniQMT] xtquant get_full_tick 失败，转 HTTP:', e)

    # 方式 B：大 QMT HTTP（兜底）
    r = cli.quote(codes)
    if r.get('ok'):
        return r.get('data', {})
    return {}
```

MiniQMT 真正停用后，把方式 A 注释掉，方式 B 自动接管，几乎零修改就能继续跑。

## 六、HTTP 接口文档

所有响应均为 JSON。

| 路径 | 方法 | 用途 |
| --- | --- | --- |
| `/ping` | GET | 健康检查 |
| `/quote?code=A,B,C` | GET | 取最新 tick 快照 |
| `/position` | GET | 查询持仓 |
| `/order` | GET | 查询当日委托 |
| `/deal` | GET | 查询当日成交 |
| `/account` | GET | 查询账号资金 |
| `/order` | POST | 下单（入队，200ms 内消费） |
| `/cancel` | POST | 撤单（入队，200ms 内消费） |

## 七、可调参数（`qmt_http_server.py` 顶部）

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `HTTP_HOST` | `'0.0.0.0'` | 监听网卡；只本机访问改 `'127.0.0.1'` |
| `HTTP_PORT` | `8899` | HTTP 端口 |
| `CONSUME_INTERVAL_MS` | `200` | 任务消费周期（毫秒） |
| `MAX_BATCH_PER_TICK` | `20` | 定时器单次最多消费任务数 |

## 八、安全 / 风险注意事项

1. **务必先用模拟账号验证**：`quickTrade=2`（立即下单），任何能访问该端口的请求都会真实下单。
2. **价格笼子（2% 规则）**：沪深主板/创业板委托价超出基准价 ±2% 会废单。
3. **委托数量上限**：主板 100 万股、创业板 30 万股、科创板 10 万股。
4. **废单查询延迟**：下单后约 50ms~6s 才能通过 `/order` 接口查到。
5. **GBK 编码**：三个 `.py` 文件本身是 GBK 编码（QMT 内置 Python 3.6 的硬性要求）。

## 九、迁移路径建议

```
[阶段 1] miniQMT 还能用        →  分析 + 下单都在 miniQMT（旧代码不动）
    ↓ MiniQMT 收紧
[阶段 2] MiniQMT 还能用 + 大 QMT →  分析在 mini，下单走桥接（本文方案）
    ↓ MiniQMT 完全停用
[阶段 3] 只有大 QMT             →  分析也迁大 QMT，QMTClient 改成本地调用
```

到了阶段 3，`QMTClient` 这个类仍然有用——把它的 `_post` 方法替换成对大 QMT 本地 `passorder` 的直接调用，上层策略代码一行都不用改。

这套设计真正的价值：**让迁移成本分摊到多年，而不是被迫在一个周末里全部重写**。
