# [2026-06-21_qmt_research.md]
============================================================

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


============================================================
# [2026-06-21_regime_tuning.md]
============================================================

# v27 Regime 择时精调实验记录（2026-06-21）

## 背景
v27 价量共振策略 WF 13/13 正收益，夏普 7.15。regime 择时模块（牛熊判断→仓位乘数）是否对收益有贡献？

## 实验方案

### 方案A：斜率阈值化
- 在 `slope > 0` 基础上增加 `SLOPE_THRESHOLD`，过滤微小斜率
- 测试 ST=0.0/0.0003/0.0005/0.0008/0.001/0.0015/0.002，SD=3/5/10
- **结果：❌ 无效**，所有阈值 WF 结果完全一致（夏普6.419）
- 原因：SD=5 时 91.6% 的 |slope| > 0.001，阈值几乎不改变牛熊判定

### 方案B：线性连续映射
- slope 线性映射到 [bear_alloc, bull_alloc]
- 测试 slope_cap=0.01/0.008，aggressive bear=0.1
- **结果：❌ 无效**，与基准完全一致

### 方案C：多指数切换
- 在 calc_regime 中支持 REGIME_INDEX 参数切换上证/中证500
- **结果：⏳ 未单独测试**（中证500 K线数据未接入 DB）
- 代码已支持，后续有数据可直接用

### 方案D：波动率过滤
- 短期波动率 > 长期波动率×1.5 → 强制降仓到 bear_alloc
- 测试 vol_window=10/20, threshold=1.3/1.5
- **结果：❌ 无效**，与基准一致

### 方案E：熊市减仓
- 熊市时 MAX_HOLDINGS=4, MAX_DAILY_BUY=2（原来8/4）
- **结果：⚠️ 略降**，夏普6.831 vs 6.896
- 原因：限制持仓数后牛市也少买了票，错过机会

### 基准对比
关闭 REGIME_ENABLED 后结果完全一样 → **Regime 对 v27 WF 零贡献**

## 核心结论
v27 选股逻辑（mom_5>5% + 量价共振确认）**自带市场状态过滤**，熊市里很少有票能同时满足两个条件，仓位管理边际贡献几乎为零。

## 代码改动
- `strategy_adapter.py` calc_regime 升级：支持 REGIME_MODE=3class/linear/vol
- `strategy_map.py` v27 params 清理：移除 REGIME_ 相关参数
- `account_runner.py` 新增 POSITION_SCALE 账户级仓位控制参数（默认1.0）
- 收盘报告显示当前 POSITION_SCALE 设置

## POSITION_SCALE 设计
- 账户级配置（存 DB params_json），非策略参数
- 公式：available = cash × POSITION_SCALE - initial_capital × 0.03
- POSITION_SCALE=1.0 → 满仓（默认），0.8 → 80%仓位，0.5 → 半仓
- 通过 `create --position-scale 0.8` 或 DB 直接修改
- 收盘报告显示当前设置

## 教训
- 不要在选股策略自带择时特性的基础上叠加复杂择时模块
- 先验证模块是否对收益有贡献，再决定是否优化
- 简单三档（1.0/0.7/0.3）不比复杂线性映射差

## 实验脚本
- `scripts/backtest/sweep_v27_regime_slope.py` — 斜率阈值扫描
- `scripts/backtest/sweep_v27_regime_bcde.py` — B/C/D/E 综合扫描


============================================================
