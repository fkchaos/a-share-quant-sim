# 系统架构文档

> 最后更新：2026-06-17（cron 极简 prompt + 监控增强 + 报告格式统一）

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     cron 调度层（7个任务）                     │
## 一、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     cron 调度层（9个任务）                     │
│  账户1(v11b)  账户2(v27)  账户3(v20c)  收盘报告                 │
└──────────────┬──────────────────────────┬───────────────────┘
               │                          │
               ▼                          ▼
┌──────────────────────┐   ┌──────────────────────────────────┐
│  scripts/sim/        │   │  scripts/backtest/               │
│  account_runner.py   │   │  run_backtest.py (统一入口)       │
│  (模拟盘统一入口)     │   │    ├── 内置策略 → 通用回测框架     │
│                      │   │    └── v27/v20c → wf_runner.py    │
│                      │   │        └── strategy_adapter.py    │
│                      │   │            (统一选股+风控+市场状态)  │
└──────────┬───────────┘   └──────────────────────────────────┘
           │                          │
           ▼                          ▼
┌──────────────────────────────────────────────────────────────┐
│                  core/ (共享引擎)                              │
│  config.py   ← STRATEGY_PROFILES + MarketFilter              │
│  account.py  ← PortfolioState + buy/sell (回测+模拟盘共用)     │
│  db.py       ← SQLite 双库 + load_panel_from_db               │
│  factors.py  ← 51 技术因子计算                                │
│  scoring.py  ← Z-score + Ensemble 评分                        │
└──────────────────────────────────────────────────────────────┘
           ▲                          ▲
           │ 数据                     │ 数据
┌──────────┴──────────┐   ┌──────────┴──────────┐
│ data/quant_stocks.db │   │ data/quant_accounts.db│
│  stock_pool          │   │  account              │
│  daily_kline         │   │  holdings             │
│  indicators          │   │  trade_log            │
│  industry_map        │   │                       │
└─────────────────────┘   └───────────────────────┘
```

## 二、目录结构

```
a-share-quant-sim/
├── core/                    # 共享引擎（回测+模拟盘共用）
│   ├── config.py            # 策略配置、交易成本、风控参数
│   ├── account.py           # PortfolioState + buy/sell 纯函数
│   ├── db.py                # SQLite 双库 + load_panel_from_db
│   ├── strategy_map.py      # 策略注册表（动态加载选股函数）
│   ├── factors.py           # 51个技术因子计算
│   ├── scoring.py           # Z-score + Ensemble 评分
│   └── strategy.py          # StrategyEngine
│
├── scripts/
│   ├── sim/                 # 模拟盘执行层
│   │   ├── account_runner.py    # 统一入口（v11b/v27/v20c）
│   │   └── sim_account1.py      # v11b legacy（备份）
│   │
│   ├── strategies/          # 选股逻辑（可独立测试）
│   │   ├── v27_select.py        # v27 价量共振选股
│   │   └── v20_tail_pick.py     # v20c 尾盘缩量选股
│   │
│   ├── backtest/            # 回测脚本
│   │   ├── run_backtest.py      # 统一回测入口（路由到 wf_runner）
│   │   ├── strategy_adapter.py  # 策略适配器（选股+风控+市场状态）
│   │   └── wf_runner.py         # Walk-Forward 通用运行器
│   │
│   ├── tools/               # 工具脚本
│   │   ├── cli.py                # 数据库 CLI（账户/持仓/买卖）
│   │   ├── init_project.py       # 一键初始化
│   │   └── update_daily_data_async.py
│   │
│   └── archive/             # 归档（旧版本脚本）
│
├── docs/                    # 文档
│   ├── DEPLOY.md            # 部署指南
│   ├── USER_MANUAL.md       # 用户手册
│   ├── ARCHITECTURE.md      # 本文档
│   └── STRATEGY_REGISTRY.md # 策略注册表
│
└── data/
    ├── quant_stocks.db      # 股票池 + K线 + 技术指标
    ├── quant_accounts.db    # 账户 + 持仓 + 交易记录
    └── portfolio/           # 交易计划 + 日志
```

## 三、策略注册表（strategy_map + strategy_adapter）

### 3.1 strategy_map（模拟盘入口）

`core/strategy_map.py` 是模拟盘策略的注册中心：

```python
STRATEGY_MAP = {
    "v11b": { "mode": "legacy", "account_id": 1, ... },
    "v27":  { "mode": "custom", "select_fn": "scripts.strategies.v27_select.select_stocks_v27", ... },
    "v20c": { "mode": "custom", "select_fn": "scripts.strategies.v20_tail_pick.select_stocks_tail_pick", ... },
}
```

### 3.2 strategy_adapter（回测+模拟盘统一接口）

`scripts/backtest/strategy_adapter.py` 是回测和模拟盘的统一策略接口层：

```python
adapter = get_adapter()

# 选股（自动路由到 v27/v20c 的选股函数）
cands = adapter.select("v27", factors, date, close_panel, ...)

# 风控（统一的止损/止盈/封板判断）
to_sell = adapter.risk_check("v27", state, date, price_data, params)

# 市场状态（上证指数 MA20 斜率 + 价格 vs MA60）
regime, mult = adapter.calc_regime("v27", close_panel, date, params)
```

**关键设计**：`account_runner.py` 和 `wf_runner.py` 都通过 `strategy_adapter` 调用选股+风控，确保回测和模拟盘逻辑一致。

### 3.3 新增策略流程

1. 在 `scripts/strategies/` 写选股模块
2. 在 `core/strategy_map.py` 注册（模拟盘）
3. 在 `scripts/backtest/strategy_adapter.py` 注册（回测）
4. 跑 WF 验证 → 上线

## 四、账户-策略解耦设计

### 4.1 问题
重构前：每个策略对应一个独立的 sim 脚本（sim_account1/2/3.py），切换策略 = 切换脚本。

### 4.2 方案
引入三层架构：
- **account_runner.py**：统一的信号生成/执行/报告入口
- **strategy_adapter.py**：统一策略接口（选股+风控+市场状态），回测和模拟盘共用
- **strategy_map.py**：策略名称 → 选股函数的映射表（模拟盘入口）

### 4.3 数据流

**模拟盘：**
```
cron → account_runner.py --strategy v27 intraday_signal
  → strategy_adapter.select() → 选股
  → strategy_adapter.risk_check() → 风控
  → strategy_adapter.calc_regime() → 市场状态
  → 生成 trade_plan → 输出信号报告
```

**回测：**
```
run_backtest.py --strategy v27
  → wf_runner.py
  → strategy_adapter.select() → 选股（同模拟盘）
  → strategy_adapter.risk_check() → 风控（同模拟盘）
  → core/account.py buy/sell → 交易执行（同模拟盘）
  → WF 分割 → 绩效计算
```

### 4.4 DB 读写
- **load**：`get_account(id)` 读现金 + `get_holdings(id)` 读持仓，从 `added_at` 计算 `hold_days`
- **save**：`upsert_account` 写现金 + `upsert_holding` 写持仓 + `delete_holding` 删已清仓
- **注意**：holdings 表无 `hold_days` 字段，每次 load 时实时计算

## 五、三账户体系

| 账户 | ID | 策略 | 初始资金 | 调仓时间 | 入口 |
|------|-----|------|---------|---------|------|
| 账户1 | 1 | v11b (legacy) | 20万 | 11:45信号/13:00执行 | `account_runner --strategy v11b` |
| 账户2 | 2 | v27 (价量共振) | 10万 | 11:45信号/13:00执行 | `account_runner --strategy v27` |
| 账户3 | 3 | v20c (尾盘缩量) | 10万 | 14:45信号/14:55执行 | `account_runner --strategy v20c` |

## 六、Cron 调度

### 6.1 任务清单

| 任务 | 时间 | 命令 | 备注 |
|------|------|------|------|
| 数据更新-上午 | 11:31 工作日 | `update_daily_data_async.py` | 含上证指数更新 |
| 数据更新-下午 | 14:40 工作日 | `update_daily_data_async.py` | 含上证指数更新 |
| 账户1-上午信号 | 11:45 工作日 | `--strategy v11b intraday_signal` | ⏸️ 已暂停 |
| 账户1-下午执行 | 13:00 工作日 | `--strategy v11b intraday_execute` | ⏸️ 已暂停 |
| 账户2-上午信号 | 11:45 工作日 | `--strategy v27 intraday_signal` | |
| 账户2-下午执行 | 13:00 工作日 | `--strategy v27 intraday_execute` | |
| 账户3-尾盘信号 | 14:45 工作日 | `--strategy v20c tail_signal` | |
| 账户3-尾盘执行 | 14:55 工作日 | `--strategy v20c tail_execute` | |
| 收盘报告 | 15:30 工作日 | `--strategy all report_only` | 三账户统一 |
| Cron监控-巡检 | */10 11-15 工作日 | `cron_monitor.py` | 漏执行/失败/超时检测 |
| Cron监控-心跳 | 16:00 工作日 | `cron_monitor.py --heartbeat` | 每日汇总 |
| 每日记忆整理 | 08:00 每日 | hermes-memery 备份 | ⏸️ 已暂停 |

> 2026-06-17 起，所有 cron 统一为极简 prompt（一条命令 + 整理报告 + CRON_STATUS 标记），避免 agent 推理消耗 API 导致 429 限流。

### 6.2 Cron Prompt 设计原则

**核心原则：脚本做所有工作，agent 只负责格式化输出。**

每个 cron prompt 固定结构：
```
执行<任务名>。

运行命令：
python3 <脚本> <参数>

整理为报告，包含股票代码和名称。

[CRON_STATUS] job_id=<id> status=ok duration=0 ts=<时间>
```

**为什么这样设计：**
- 旧 prompt 有 3-5 步操作（git pull + 跑脚本 + 读文件 + 整理报告），每步都触发 API 调用
- OpenRouter Stealth provider 有严格速率限制，下午密集时段（14:40-15:30）容易打满 429
- 新 prompt 只需 1 次 API 调用（跑命令 + 格式化输出），大幅降低 429 风险

### 6.3 报告格式规范

所有信号/执行/报告 cron 的输出必须包含：
- 市场状态（牛/熊/震荡 + 仓位乘数）
- 现金 + 持仓数
- 卖出明细：代码 + 名称 + 股数 + 价格 + 原因
- 买入明细：代码 + 名称 + 股数 + 价格 + 目标金额
- 持仓明细：代码 + 名称 + 股数 + 成本 + 市值 + 盈亏%

### 6.4 Cron 监控系统

`scripts/cron_monitor.py` 功能：
- 解析每个 job 输出中的 `[CRON_STATUS]` 标记
- 检测漏执行（计划时间 + 容忍窗口后仍未运行）
- 检测连续失败（可配置阈值，默认 2 次）
- 检测超时（超过历史均值 2 倍）
- 告警抑制（30 分钟内不重复告警）
- 心跳报告（每日 16:00 汇总所有 job 状态）

**无标记失败检测**：当 agent 在写 CRON_STATUS 之前崩溃（如 HTTP 429），输出文件存在但无标记。监控脚本会检查文件内容是否含 `Error`/`FAILED`/`RuntimeError`/`HTTP 429` 等关键词，标记为 `error_no_marker` 并告警。

## 七、回测与模拟盘一致性

- 回测引擎：`scripts/backtest/run_backtest.py`
- 共享代码：`core/account.py`（PortfolioState + buy/sell）
- 共享选股：`scripts/strategies/` 下的选股模块可被回测直接调用
- 数据源：统一从 `core/db.py` 读取（SQLite）
