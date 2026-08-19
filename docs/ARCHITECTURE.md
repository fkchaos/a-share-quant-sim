# 系统架构文档

> 最后更新：2026-08-19（v61c + v75j 双账户运行）

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     cron 调度层（8个任务）                     │
│  账户1(v61c)  账户2(v75j)  数据更新  收盘报告                    │
└──────────────┬──────────────────────────┬───────────────────┘
               │                          │
               ▼                          ▼
┌──────────────────────┐   ┌──────────────────────────────────┐
│  scripts/sim/        │   │  scripts/backtest/               │
│  account_runner.py   │   │  wf_runner.py (回测入口)          │
└──────────┬───────────┘   └──────────────────────────────────┘
           │                          │
           ▼                          ▼
┌──────────────────────────────────────────────────────────────┐
│              core/trading.py（交易门面层）                      │
│  统一 buy/sell/portfolio_value 入口，自动路由到                  │
│  Provider 模式或直接模式（向后兼容）                              │
└──────────┬───────────────────────────────────────────────────┘
           │                          │
           ▼                          ▼
┌──────────────────────────────────────────────────────────────┐
│              core/（共享引擎）                                  │
│  config.py           ← 策略配置、交易成本、风控参数             │
│  trading_provider.py ← TradingProvider 基类（统一交易接口）     │
│  provider_factory.py ← 根据配置创建 Provider（sim/qmt）        │
│  providers/          ← Provider 实现                           │
│    ├── sim_provider.py  ← 模拟盘 Provider（JSON持久化）        │
│    └── qmt_provider.py  ← QMT Provider（待实现）              │
│  account.py          ← PortfolioState + buy/sell（回测共用）    │
│  db.py               ← SQLite 双库 + load_panel_from_db       │
│  strategy_map.py     ← 策略注册表（动态加载选股函数）            │
│  factors.py          ← 技术因子计算                             │
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
│   ├── trading_provider.py  # TradingProvider 基类（统一交易接口）
│   ├── trading.py           # 交易门面层（路由到 Provider 或直接模式）
│   ├── provider_factory.py  # 根据配置创建 Provider（sim/qmt）
│   ├── providers/           # Provider 实现
│   │   ├── sim_provider.py  # 模拟盘 Provider（JSON 持久化）
│   │   ├── qmt_provider.py  # QMT Provider（待实现）
│   │   ├── tencent.py       # 腾讯行情数据源
│   │   └── baostock.py      # BaoStock 数据源
│   ├── account.py           # PortfolioState + buy/sell（回测共用）
│   ├── db.py                # SQLite 双库 + load_panel_from_db
│   ├── strategy_map.py      # 策略注册表
│   └── factors.py           # 技术因子计算
│
├── scripts/
│   ├── sim/                 # 模拟盘
│   │   └── account_runner.py    # 统一入口（信号/执行/报告）
│   │
│   ├── strategies/          # 选股逻辑（活跃）
│   │   ├── v61c_turnover_size.py     # v61c低换手小票因子（⭐ 账户1）
│   │   ├── v61c_sentiment.py         # v61c情绪版
│   │   ├── v75j_liquidity_only.py    # v75j纯流动性因子（⭐ 账户2）
│   │   ├── v75a_tech_momentum.py     # v75a技术动量
│   │   ├── v39g_optimized.py         # v39g基准策略
│   │   └── ...
│   │
│   ├── backtest/            # 回测框架
│   │   ├── wf_runner.py         # Walk-Forward 运行器 + 全量回测（--full）
│   │   ├── strategy_adapter.py  # 策略适配器（选股+风控，overlay注册）
│   │   ├── v61c_risk_scan.py    # v61c overlay信号/回测入口
│   │   └── sweep_v27_*.py       # 参数扫描脚本（调参用）
│   │
│   ├── factors/             # 因子研究脚本（v82/v83系列）
│   │   ├── v82_batch_ic.py       # 迅投因子批量IC测试
│   │   ├── v82a_ic_analysis.py   # v82a IC分析
│   │   ├── v82b_ic_analysis.py   # v82b IC分析
│   │   ├── v82_hs300_reproduce.py    # v82 沪深300复现
│   │   ├── v82_hs300_reproduce_v2.py # v82 沪深300复现v2
│   │   ├── v83_root_cause_r1.py  # v83 IC根因调查 R1
│   │   ├── v83_root_cause_r2.py  # v83 IC根因调查 R2
│   │   └── v83_root_cause_r3.py  # v83 IC根因调查 R3
│   │
│   └── tools/               # 工具脚本
│       ├── cmd.py                # 数据库 CLI
│       ├── init_project.py       # 一键初始化
│       └── update_daily_data_async.py
│
├── alpha-research/          # 因子研究报告
│   └── reports/
│       └── xuntou/          # 迅投因子研究结果
│           ├── xuntou_factor_analysis_20260819.md
│           └── v82_*.csv    # 各因子IC数据
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
    │   ├── 2026-08-19_qmt_migration_plan.md        # QMT迁移计划
    │   ├── 2026-08-19_qmt_trading_provider_design.md # Provider设计
    │   ├── 2026-08-19_v82_xuntou_factors_design.md   # v82因子设计
    │   ├── 2026-08-19_v82_xuntou_factors_results.md  # v82因子结果
    │   ├── 2026-08-19_v83_ic_root_cause_plan.md      # v83根因调查
    │   ├── 2026-06-20_factor_survey.md
    │   ├── 2026-06-21_qmt_regime_research.md
    │   ├── 2026-06-30_dragon_concept_design.md
    │   └── ...
    └── archive/             # 归档（废弃策略脚本/工具）
```

## 三、策略注册表（strategy_map + strategy_adapter）

### 3.1 strategy_map（模拟盘入口）

`core/strategy_map.py` 是模拟盘策略的注册中心，策略名 → 选股函数 + 风控参数 + 股票池（pool字段）。

每个策略通过 `pool` 字段指定股票池：
- `'zz1800'`（默认）— 中证1800范围
- `'full_a'` — 全A范围（如 v43）

### 3.2 strategy_adapter（回测+模拟盘统一接口）

`scripts/backtest/strategy_adapter.py` 提供统一的 `select()` / `risk_check()` / `calc_regime()` 接口。

**关键设计**：`account_runner.py` 和 `wf_runner.py` 都通过 `strategy_adapter` 调用选股+风控，确保回测和模拟盘逻辑一致。

### 3.3 Overlay 机制（特殊策略扩展标准框架）

部分策略（如 v61c）有特殊交易逻辑（调仓日判断、卖出即买、排名淘汰），无法用标准 `select()` / `risk_check()` 接口表达。Overlay 机制允许这些策略保留独立脚本，同时通过统一入口（`wf_runner` / `account_runner`）调用。

**工作原理：**
```
wf_runner.py --strategy v61c --full
  → 检测 adapter._overlay_scripts["v61c"]
  → 动态导入 scripts.backtest.v61c_risk_scan
  → 调用 run_wf_overlay(full=True, params={...})
  → 外部脚本完成回测，返回标准结果格式
```
**配置位置：** `strategy_adapter.py` → `_overlay_scripts["v61c"]`

```python
{
    "module": "scripts.backtest.v61c_risk_scan",
    "entry_func": "run_wf_overlay",      # WF/全量回测入口
    "select_func": "select_stocks",       # 模拟盘选股入口
    "signal_func": "run_signal",          # 完整信号流程入口
    "params": {
        "REBALANCE_DAYS": 5,
        "STOP_LOSS": -0.08,
        "TAKE_PROFIT": 0.25,
        "HOLD_DAYS_MAX": 5,
        "MAX_DAILY_BUY": 5,
        "MAX_POSITION": 0.25,
        "MAX_HOLDINGS": 5,
        "SENTIMENT_WINDOW": 0,            # 0=不启用情绪过滤
        "SENTIMENT_THRESHOLD": 5.0,
        "SENTIMENT_COLD_MODE": True,
    }
}
```

**支持模式：**
- `full=False`（默认）：WF 切分回测，train/test/step 控制窗口
- `full=True`：全量连续回测，跑完整个区间

**已知问题（2026-07-29）：**
- wf_runner.py 之前缺少 `main()` 和 `__main__` 入口，导致命令行运行无输出。已修复。
- `_calc_factors()` 中存在死代码（return后的代码），已清理。

### 3.4 新增策略流程

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
python scripts/sim/account_runner.py create --account-id 1 --name "v61c账户" --cash 100000 --strategy v61c
python scripts/sim/account_runner.py create --account-id 2 --name "v75j账户" --cash 100000 --strategy v75j
python scripts/sim/account_runner.py list    # 查看所有账户及配置
python scripts/sim/account_runner.py switch --account-id 2 --strategy v75j  # 切换策略
```

每个账户独立绑定一个策略，拥有独立的现金、持仓和交易记录。`POSITION_SCALE` 等账户级配置存于 `params_json` 字段。

## 六、数据层

### 6.1 数据源 Provider 架构（可扩展）

数据获取采用 **Provider 抽象层**，支持多数据源自动切换和手动指定：

```
config/data_sources.yaml    ← 配置文件（primary/backup/override）
         ↓
core/provider_manager.py    ← ProviderManager（fallback 链管理）
         ↓
core/data_provider.py       ← DataProvider 抽象接口
         ↓
core/providers/tencent.py   ← 腾讯行情（主数据源，免费）
core/providers/baostock.py  ← BaoStock（备用数据源，免费）
```

**Fallback 机制**：
1. **override**（手动指定）→ 优先级最高，设置后忽略 primary/backup
2. **primary**（主数据源）→ 日常使用
3. **backup**（备用数据源）→ primary 失败时自动切换

**切换数据源**（详见 `docs/DEPLOY.md`）：
```bash
# 临时强制使用 BaoStock
vim config/data_sources.yaml
# 取消注释 override: baostock

# 恢复默认（Tencent 主 → BaoStock 备）
# 注释掉 override 行
```

**可扩展**：添加新数据源只需：
1. 在 `core/providers/` 新建 `xxx.py`，继承 `DataProvider` 接口
2. 实现 `get_daily_kline()`、`get_float_shares()`、`get_index_components()` 三个方法
3. 在 `core/provider_manager.py` 的 `_register_builtin_providers()` 中注册
4. 在 `config/data_sources.yaml` 中配置 primary/backup

### 6.2 双库架构

SQLite 双库分离，`core/db.py` 统一管理连接：

| 数据库 | 表 | 内容 |
|--------|-----|------|
| `data/quant_stocks.db` | `stock_pool` | 股票池（中证1800成分股） |
| | `daily_kline` | 日K线（所有股票+指数） |
| | `index_kline` | 指数K线（上证/中证500等） |
| | `indicators` | 技术指标 |
| | `industry_map` | 行业分类 |
| `data/quant_accounts.db` | `account` | 账户（现金、策略、params_json） |
| | `holdings` | 持仓（account_id + code 联合主键） |
| | `trade_log` | 交易记录 |

### 6.3 核心函数

- `get_kline(code)` / `get_index_kline(code)` — 读取K线
- `get_tradeable_codes()` — 可交易股票池（排除科创板/北交所）
- `load_panel_from_db(start, end)` — 加载面板数据（回测用）
- `get_account(id)` / `upsert_account(id, ...)` — 账户读写
- `get_holdings(id)` / `upsert_holding(...)` — 持仓读写

### 6.4 数据流

```
Provider（腾讯/BaoStock）→ update_daily_data_async.py → quant_stocks.db
                                              ↓
account_runner.py ← core/db.py ← quant_stocks.db (K线面板)
                                              ↓
account_runner.py → quant_accounts.db (交易记录)
```

## 七、定时调度（两条执行路径，case by case）

> 详细配置说明见 `docs/CRON_SETUP.md`

### 路径 A：非 Agent 用户（系统 crontab）

```cron
# 数据更新
01,31 9-11,13-15 * * 1-5 python3 scripts/tools/update_daily_data_async.py 2>/dev/null | python3 scripts/tools/format_report.py --type data_update

# 账户1(v61c) - overlay模式，直接运行signal
45 11 * * 1-5 python3 scripts/sim/account_runner.py run --account-id 1 intraday_signal 2>/dev/null | python3 scripts/tools/format_report.py --type signal --account 1
0 13 * * 1-5 python3 scripts/sim/account_runner.py run --account-id 1 intraday_execute 2>/dev/null | python3 scripts/tools/format_report.py --type execute --account 1

# 账户2(v75j) - 标准策略，switch && run
50 11 * * 1-5 python3 scripts/sim/account_runner.py switch --account-id 2 --strategy v75j && python3 scripts/sim/account_runner.py run --account-id 2 intraday_signal 2>/dev/null | python3 scripts/tools/format_report.py --type signal --account 2
5 13 * * 1-5 python3 scripts/sim/account_runner.py switch --account-id 2 --strategy v75j && python3 scripts/sim/account_runner.py run --account-id 2 intraday_execute 2>/dev/null | python3 scripts/tools/format_report.py --type execute --account 2

# 收盘报告
30 15 * * 1-5 python3 scripts/sim/account_runner.py run --account-id 1 report_only 2>/dev/null | python3 scripts/tools/format_report.py --type report --account 1
31 15 * * 1-5 python3 scripts/sim/account_runner.py switch --account-id 2 --strategy v75j && python3 scripts/sim/account_runner.py run --account-id 2 report_only 2>/dev/null | python3 scripts/tools/format_report.py --type report --account 2
```

### 路径 B：Agent 用户（Hermes cron）

| 任务 | 时间 | 策略 |
|------|------|------|
| 数据更新(每半小时) | 9:01-11:31,13:01-15:31 工作日 | — |
| 账户1-上午信号 | 11:45 工作日 | **v61c** |
| 账户1-下午执行 | 13:00 工作日 | **v61c** |
| 账户2-上午信号 | 11:50 工作日 | **v75j** |
| 账户2-下午执行 | 13:05 工作日 | **v75j** |
| 收盘报告 | 16:00 工作日 | — |

> 两个账户均在运行中，各自独立调度。

## 八、回测与模拟盘一致性

- 回测引擎：`scripts/backtest/wf_runner.py`（WF 回测 + `--full` 全量回测）
- 共享代码：`core/account.py`（PortfolioState + buy/sell）
- 共享选股：`scripts/strategies/` 下的选股模块可被回测直接调用
- 数据源：统一从 `core/db.py` 读取（SQLite）
- **回归测试**：`tests/golden_test.py`（Golden Test 套件，验证策略/数据变动前后结果一致性，详见 `docs/TESTING.md`）

## 九、QMT 迁移状态

### 9.1 miniQMT 现状

- miniQMT（QMT迷你版）于 **2026年7月6日停止运行**
- 券商已不再支持 miniQMT 模式，仅保留大QMT
- miniQMT 的交易功能已无法使用，QMT迁移策略需重新评估

### 9.2 迁移计划

- 详见 `docs/experiments/2026-08-19_qmt_migration_plan.md`
- 交易Provider架构已预留QMT接口（`core/trading_provider.py` + `core/providers/qmt_provider.py`）
- QMTProvider 待券商政策确认后再开发

## 十、交易Provider架构

### 10.1 设计目标

交易操作采用 **Provider 抽象层**，实现：
- 策略代码与交易执行解耦
- 模拟盘/QMT/实盘无缝切换
- 向后兼容现有 `core/account.py` 直接模式

### 10.2 架构

```
策略代码 / account_runner.py
         ↓
core/trading.py（门面层）
  ├─ 有 Provider → 通过 Provider 执行
  └─ 无 Provider → 直接调用 core/account.py（向后兼容）
         ↓
core/trading_provider.py（基类）
  ├─ SimProvider  → core/account.py（JSON持久化，模拟盘）
  └─ QMTProvider  → QMT API（待实现）
```

### 10.3 核心文件

| 文件 | 职责 |
|------|------|
| `core/trading_provider.py` | `TradingProvider` 基类，定义统一接口（buy/sell/get_positions/get_balance/portfolio_value） |
| `core/trading.py` | 交易门面层，提供 `buy()` / `sell()` / `portfolio_value()` 统一入口，自动路由 |
| `core/provider_factory.py` | 工厂函数 `create_provider(config)`，根据配置创建 SimProvider 或 QMTProvider |
| `core/providers/sim_provider.py` | `SimProvider` 实现，封装 `core/account.py` 逻辑，JSON 文件持久化 |

### 10.4 使用方式

```python
# 模式1：直接使用（现有代码，向后兼容）
from core.trading import buy, sell

# 模式2：Provider模式（新代码，QMT移植时使用）
from core.provider_factory import create_provider
from core.trading import set_provider, buy, sell

config = {
    'provider': 'sim',
    'sim': {'account_id': '1', 'portfolio_dir': 'data/portfolio', 'initial_cash': 100000}
}
provider = create_provider(config)
set_provider(provider)

# 之后统一使用 core.trading 的 buy/sell
buy(state, '600000', 10.5, '2026-08-19')
```

### 10.5 可扩展性

添加新Provider只需：
1. 在 `core/providers/` 新建 `xxx_provider.py`
2. 继承 `TradingProvider`，实现 `buy()` / `sell()` / `get_positions()` / `get_balance()` / `portfolio_value()`
3. 在 `core/provider_factory.py` 的 `create_provider()` 中注册

## 十一、v82/v83 因子研究

### 11.1 v82：迅投因子验证

- 来源：迅投（XunTou）量化因子库
- 范围：8个因子（v82a-v82h），包括换手率、波动率、BBI、ARBR、流动性比率等
- 股票池：沪深300（hs300），回测周期1年
- **结果：所有8个因子在zz1800中IC均为零或无效**
- 详见：`scripts/factors/v82_*.py`、`alpha-research/reports/xuntou/`

### 11.2 v83：IC根因调查

- 目标：调查v82因子IC为零的根因
- 调查轮次：R1-R3
  - R1：基础IC计算验证
  - R2：数据源交叉验证（Tencent vs BaoStock）
  - R3：因子定义/计算逻辑审查
- 详见：`scripts/factors/v83_root_cause_r*.py`、`docs/experiments/2026-08-19_v83_ic_root_cause_plan.md`
