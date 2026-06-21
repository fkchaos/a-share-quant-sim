# 系统架构文档

> 最后更新：2026-06-21（文档清理：移除 v20c 退役策略、实验脚本、更新 cron 任务清单）

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     cron 调度层（6个任务）                     │
│  账户1(v11b)  账户2(v27)  收盘报告                 │
└──────────────┬──────────────────────────┬───────────────────┘
               │                          │
               ▼                          ▼
┌──────────────────────┐   ┌──────────────────────────────────┐
│  scripts/sim/        │   │  scripts/backtest/               │
│  account_runner.py   │   │  run_backtest.py (统一入口)       │
│  (模拟盘统一入口)     │   │    ├── 内置策略 → 通用回测框架     │
│                      │   │    └── v27 → wf_runner.py    │
│                      │   │        └── strategy_adapter.py    │
└──────────┬───────────┘   └──────────────────────────────────┘
           │                          │
           ▼                          ▼
┌──────────────────────────────────────────────────────────────┐
│                  core/ (共享引擎)                              │
│  config.py   ← 策略配置、交易成本、风控参数        │
│  account.py  ← PortfolioState + buy/sell (回测+模拟盘共用)     │
│  db.py       ← SQLite 双库 + load_panel_from_db               │
│  strategy_map.py ← 策略注册表（动态加载选股函数）  │
│  factors.py  ← 技术因子计算                                │
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
├── core/                    # 共享引擎
│   ├── config.py            # 策略配置、交易成本、风控参数
│   ├── account.py           # PortfolioState + buy/sell
│   ├── db.py                # SQLite 双库 + load_panel_from_db
│   ├── strategy_map.py      # 策略注册表
│   └── factors.py           # 技术因子计算
│
├── scripts/
│   ├── sim/                 # 模拟盘
│   │   └── account_runner.py    # 统一入口（信号/执行/报告）
│   │
│   ├── strategies/          # 选股逻辑
│   │   ├── v27_select.py        # v27 价量共振
│   │   ├── v32_analyst_expectation.py
│   │   ├── v33_residual_momentum.py
│   │   └── v35_sector_rotation.py
│   │
│   ├── backtest/            # 回测框架
│   │   ├── run_backtest.py      # 统一回测入口
│   │   ├── strategy_adapter.py  # 策略适配器（选股+风控）
│   │   └── wf_runner.py         # Walk-Forward 运行器
│   │
│   └── tools/               # 工具脚本
│       ├── cli.py                # 数据库 CLI
│       ├── init_project.py       # 一键初始化
│       └── update_daily_data_async.py
│
└── docs/
    ├── DEPLOY.md            # 部署指南
    ├── USER_MANUAL.md       # 用户手册
    ├── ARCHITECTURE.md      # 本文档
    ├── RELEASE_NOTES.md     # 版本发布记录
    ├── TODO.md              # 待办事项
    ├── strategy/            # 策略文档
    │   ├── STRATEGY_REGISTRY.md
    │   ├── RESULTS_LOG.md
    │   └── STRATEGIES_DISCARDED.md
    ├── experiments/         # 实验记录
    │   ├── 2026-06-20_factor_survey.md
    │   ├── 2026-06-21_regime_tuning.md
    │   └── api-notes.md
    └── archive/             # 归档
```

## 三、策略注册表（strategy_map + strategy_adapter）

### 3.1 strategy_map（模拟盘入口）

`core/strategy_map.py` 是模拟盘策略的注册中心，策略名 → 选股函数 + 风控参数。

### 3.2 strategy_adapter（回测+模拟盘统一接口）

`scripts/backtest/strategy_adapter.py` 提供统一的 `select()` / `risk_check()` / `calc_regime()` 接口。

**关键设计**：`account_runner.py` 和 `wf_runner.py` 都通过 `strategy_adapter` 调用选股+风控，确保回测和模拟盘逻辑一致。

### 3.3 新增策略流程

1. 在 `scripts/strategies/` 写选股模块
2. 在 `core/strategy_map.py` 注册（模拟盘）
3. 在 `scripts/backtest/strategy_adapter.py` 注册（回测）
4. 跑 WF 验证 → 上线

## 四、账户-策略解耦

### 4.1 架构

- **account_runner.py**：统一的信号生成/执行/报告入口
- **strategy_adapter.py**：统一策略接口（选股+风控），回测和模拟盘共用
- **strategy_map.py**：策略名称 → 选股函数的映射表

### 4.2 数据流

**模拟盘：**
```
cron → account_runner.py --strategy v27 intraday_signal
  → strategy_adapter.select() → 选股
  → strategy_adapter.risk_check() → 风控
  → 生成 trade_plan → 输出信号报告
```

### 4.3 仓位控制

- **POSITION_SCALE**：账户级静态仓位控制（存 DB params_json，默认 1.0）
  - `available = cash × POSITION_SCALE - initial_capital × 0.03`
  - 设为 0.8 则保留 20% 现金，设为 0.5 则半仓
  - 通过 `create --position-scale 0.8` 设置

## 五、账户管理

账户存储在 `quant_accounts.db` 的 `account` 表中，通过 CLI 动态管理：

```bash
python scripts/sim/account_runner.py create --account-id 1 --name "v11b账户" --cash 200000 --strategy v11b
python scripts/sim/account_runner.py create --account-id 2 --name "v27账户" --cash 200000 --strategy v27 --position-scale 0.8
python scripts/sim/account_runner.py list    # 查看所有账户及配置
python scripts/sim/account_runner.py switch --account-id 2 --strategy v35  # 切换策略
```

每个账户独立绑定一个策略，拥有独立的现金、持仓和交易记录。`POSITION_SCALE` 等账户级配置存于 `params_json` 字段。

## 六、数据层

### 6.1 双库架构

SQLite 双库分离，`core/db.py` 统一管理连接：

| 数据库 | 表 | 内容 |
|--------|-----|------|
| `data/quant_stocks.db` | `stock_pool` | 股票池（中证800成分股） |
| | `daily_kline` | 日K线（所有股票+指数） |
| | `index_kline` | 指数K线（上证/中证500等） |
| | `indicators` | 技术指标 |
| | `industry_map` | 行业分类 |
| `data/quant_accounts.db` | `account` | 账户（现金、策略、params_json） |
| | `holdings` | 持仓（account_id + code 联合主键） |
| | `trade_log` | 交易记录 |

### 6.2 核心函数

- `get_kline(code)` / `get_index_kline(code)` — 读取K线
- `get_tradeable_codes()` — 可交易股票池（排除科创板/北交所）
- `load_panel_from_db(start, end)` — 加载面板数据（回测用）
- `get_account(id)` / `upsert_account(id, ...)` — 账户读写
- `get_holdings(id)` / `upsert_holding(...)` — 持仓读写

### 6.3 数据流

```
腾讯行情 → update_daily_data_async.py → quant_stocks.db
                                              ↓
account_runner.py ← core/db.py ← quant_stocks.db (K线面板)
                                              ↓
account_runner.py → quant_accounts.db (交易记录)
```

## 七、Cron 调度（4 个任务，Hermes cron）

| 任务 | 时间 | 命令 | 备注 |
|------|------|------|------|
| 数据更新 | 11:31/15:05 工作日 | `run_and_send.py --task data_update` | 含上证指数 |
| 账户2-上午信号 | 11:45 工作日 | `run_and_send.py --task signal --account 2` | v27 价量共振 |
| 账户2-下午执行 | 13:00 工作日 | `run_and_send.py --task execute --account 2` | v27 价量共振 |
| 收盘报告 | 15:30 工作日 | `run_and_send.py --task report --account 2` | |

> 账户1(v11b)、账户3(v20c) 已暂停，不参与日常调度。

## 七、回测与模拟盘一致性

- 回测引擎：`scripts/backtest/run_backtest.py`
- 共享代码：`core/account.py`（PortfolioState + buy/sell）
- 共享选股：`scripts/strategies/` 下的选股模块可被回测直接调用
- 数据源：统一从 `core/db.py` 读取（SQLite）
