# QMT 实盘接入调研

> 调研日期：2026-06-21
> 结论：QMT 不支持 Linux，需 Windows 桥接；推荐云 Windows Server 方案

---

## 一、QMT 简介

QMT（迅投）是国金证券等 **40+ 主流券商**使用的量化交易平台。

**核心特点：**
- 本地运行，策略代码不泄露
- 支持 Python（内置 3.7-3.9，也支持外部 3.10+）
- 数据下载 → 回测 → 模拟盘 → 实盘，一站式
- 双模式：**大 QMT**（完整 GUI 客户端）/ **miniQMT**（极简交易通道）

---

## 二、Linux 兼容性

### ❌ 结论：QMT 不支持 Linux

- 官方仅支持 **Windows 7/10/11 64位**，明确不支持 Linux 和 Mac
- 根本原因：券商柜台系统（迅投/恒生）从供应商层面就是 Windows 架构，涉及交易所通道认证、安全控件等
- xtquant Python 包虽然能跨平台安装，但底层调用 Windows DLL/OCX 组件

### ✅ 可行方案：Windows 桥接模式

社区成熟方案：**Windows 主机跑 QMT 客户端 + Linux 主机跑策略，通过局域网通信**

```
┌─────────────────────┐          ┌──────────────────────┐
│   Windows 主机       │          │   Linux 服务器        │
│                     │          │                      │
│  miniQMT 客户端      │◄─Socket─►│  策略引擎             │
│  xtquant 行情/交易   │  /Redis  │  account_runner      │
│  桥接脚本 (Python)   │          │  strategy_adapter    │
│                     │          │  cron 调度           │
└─────────────────────┘          └──────────────────────┘
```

**桥接方式：**
1. **Socket Server**：Windows 端启动 Python socket server，封装 xtquant API
2. **Redis 中转**：Windows 端写 Redis，Linux 端读 Redis
3. **REST API**：Windows 端启动 HTTP 服务（端口 8000）
4. **gRPC**：Windows 端启动 gRPC 服务（端口 50051）

---

## 三、核心 API

```python
from xtquant import xtdata, xttype, xttrader

# 初始化
trader = xttrader.XtQuantTrader("路径", "session_id")
trader.start()

# 下单
order_id = trader.order_stock(acc_id, '000001', 
                               xttype.STOCK_BUY, 100, 
                               xttype.FIX_PRICE, 10.5)

# 查持仓
positions = trader.query_stock_positions(acc_id)

# 行情
xtdata.download_history_data('000001', '1day', '2024-01-01', '2024-12-31')
data = xtdata.get_full_tick(['000001', '000002'])
```

---

## 四、与现有系统对接方案

### 推荐：QMT 纯执行层

```
我们现有的架构                      QMT
┌──────────────┐              ┌──────────┐
│ cron 调度     │              │          │
│ account_runner│───信号──────▶│ xttrader │──▶ 券商
│ strategy_     │              │ 下单/撤单 │
│  adapter      │◀───持仓──────│          │
│ 选股+风控     │              │          │
└──────────────┘              └──────────┘
```

- **保留**：选股逻辑（v27/v35 因子计算）、风控（止损/止盈/封板）、参数管理
- **替换**：模拟盘执行层 → QMT xttrader 实盘执行
- **新增**：QMT 执行适配器（类似 strategy_adapter 的模式）

### 改造点

| 改造项 | 工作量 | 说明 |
|--------|--------|------|
| QMT 桥接服务（Windows） | 1-2 天 | 封装 xtquant 行情+交易 API |
| QMT 执行适配器（Linux） | 1-2 天 | 替代模拟盘执行层 |
| 持仓同步 | 0.5 天 | 每天开盘前从 QMT 拉取持仓到 DB |
| 风控适配 | 0.5 天 | 涨停判断、止盈止损逻辑移植 |

---

## 五、开通条件

1. **券商开户**：需要支持 QMT 的券商（国金/华泰/国泰君安等 40+）
2. **资金门槛**：一般 10 万起（各券商不同）
3. **签署协议**：量化交易协议
4. **申请流程**：开户 → 入金 10 万 → 线上申请 → 2 个工作日开通

---

## 六、风险

| 风险 | 应对 |
|------|------|
| 网络稳定性 | 写断线重连 + 本地队列缓冲 |
| QMT 客户端必须保持登录 | Windows 端 miniQMT 必须一直开着 |
| xtquant session 唯一性 | 每次 connect 间隔至少 3 秒 |
| 数据源差异 | QMT 自带行情 vs 腾讯行情，需交叉验证 |

---

## 七、成本

| 项目 | 费用 |
|------|------|
| Windows 云主机（2核4G） | ~50-100元/月 |
| QMT 软件 | 免费（券商提供） |
| 开发工作量 | 3-5 天 |

---

## 八、建议

1. **先用云 Windows Server 跑通桥接**：不需要买物理机
2. **用 QMT 模拟盘测试**：开通权限后先用模拟盘跑
3. **逐步迁移**：先让 v27 跑 QMT 模拟盘，验证收益一致后再上实盘
