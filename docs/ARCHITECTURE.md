# 系统架构文档

> 最后更新：2026-07-16（三账户统一走 account_runner）

## 一、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     cron 调度层（7个任务）                     │
│  账户1(v11b) 账户2(v27) 账户3(v20c) 收盘报告                   │
└──────────────┬──────────────────────────┬───────────────────┘
               │                          │
               ▼                          ▼
┌──────────────────────┐   ┌──────────────────────────────┐
│  scripts/sim/        │   │  core/strategy_map.py        │
│  sim_account1.py     │   │  策略注册表（动态加载）         │
│  (v11b legacy)       │   │  v11b → legacy 模式           │
│                      │   │  v27  → v27_select.py        │
│  account_runner.py   │◄──│  v20c → v20_tail_pick.py     │
│  (统一入口)           │   └──────────────────────────────┘
│  --strategy v27|v20c │
│  intraday_signal     │   ┌──────────────────────────────┐
│  intraday_execute    │   │  scripts/strategies/          │
│  tail_signal         │   │  v27_select.py               │
│  tail_execute        │   │    calc_factors()            │
│  report_only         │   │    select_stocks_v27()       │
└──────────┬───────────┘   │  v20_tail_pick.py            │
           │               │    calc_tail_pick_factors()  │
           ▼               │    select_stocks_tail_pick() │
┌──────────────────────┐   └──────────────────────────────┘
│  core/account.py     │
│  PortfolioState      │   ┌──────────────────────────────┐
│  buy/sell/风控       │   │  core/db.py                  │
└──────────┬───────────┘   │  get_account/upsert_account  │
           │               │  get_holdings/upsert_holding │
           ▼               │  delete_holding              │
┌──────────────────────┐   │  get_kline/load_panel        │
│  SQLite: /root/data/ │   └──────────────────────────────┘
│  quant.db            │
│  account/holdings/   │   ┌──────────────────────────────┐
│  trade_log/daily_kline│  │  core/config.py              │
└──────────────────────┘   │  STRATEGY_PROFILES           │
                           │  TradingCosts/RiskLimits     │
                           └──────────────────────────────┘
```

## 二、目录结构

```
a-share-quant-sim/
├── core/                    # 共享引擎（回测+模拟盘共用）
│   ├── config.py            # 策略配置、交易成本、风控参数
│   ├── account.py           # PortfolioState + buy/sell 纯函数
│   ├── db.py                # SQLite 数据库层
│   ├── strategy_map.py      # 策略注册表（动态加载选股函数）
│   ├── factors.py           # 40个技术因子计算
│   ├── scoring.py           # Z-score + Ensemble 评分
│   ├── strategy.py          # StrategyEngine（factor/ensemble/ml 模式）
│   └── ...
│
├── scripts/
│   ├── sim/                 # 模拟盘执行层
│   │   ├── account_runner.py    # 统一入口（v11b/v27/v20c）
│   │   ├── sim_account1.py      # v11b legacy（备份，不再被 cron 调用）
│   │   └── sim_account2_v13.py  # v13 备份
│   │
│   ├── strategies/          # 选股逻辑（可独立测试）
│   │   ├── v27_select.py        # v27 价量共振选股
│   │   └── v20_tail_pick.py     # v20c 尾盘缩量选股
│   │
│   ├── backtest/            # 回测脚本
│   │   └── run_backtest.py      # 回测入口
│   │
│   ├── tools/               # 工具脚本
│   │   ├── update_data.py       # 数据更新
│   │   ├── import_csv_to_db.py  # CSV → DB 导入
│   │   └── ...
│   │
│   └── archive/             # 归档（旧版本脚本）
│
├── docs/                    # 文档
│   ├── ARCHITECTURE.md      # 本文档
│   ├── STRATEGY_REGISTRY.md # 策略注册表
│   ├── RESULTS_LOG.md       # 回测结果记录
│   └── ...
│
└── README.md
```

## 三、策略注册表（strategy_map）

`core/strategy_map.py` 是策略的注册中心，新增策略只需：

1. 在 `scripts/strategies/` 写选股模块（实现 `calc_factors` + `select_stocks`）
2. 在 `strategy_map.py` 注册一行

```python
# core/strategy_map.py 结构
STRATEGY_MAP = {
    "v11b": {
        "mode": "legacy",           # 直接调用原脚本
        "script": "scripts.sim.sim_account1",
        "account_id": 1,
    },
    "v27": {
        "mode": "custom",           # 走 account_runner
        "select_fn": "scripts.strategies.v27_select.select_stocks_v27",
        "calc_factors_fn": "scripts.strategies.v27_select.calc_factors",
        "account_id": 2,
        "timing": "intraday",       # 11:45信号 → 13:00执行
    },
    "v20c": {
        "mode": "custom",
        "select_fn": "scripts.strategies.v20_tail_pick.select_stocks_tail_pick",
        "calc_factors_fn": "scripts.strategies.v20_tail_pick.calc_tail_pick_factors",
        "account_id": 3,
        "timing": "tail",           # 14:45信号 → 14:55执行
    },
}
```

## 四、账户-策略解耦设计

### 4.1 问题
重构前：每个策略对应一个独立的 sim 脚本（sim_account1/2/3.py），切换策略 = 切换脚本。

### 4.2 方案
引入中间层 `account_runner.py` + `strategy_map.py`：
- **account_runner.py**：统一的信号生成/执行/报告入口
- **strategy_map.py**：策略名称 → 选股函数的映射表
- **strategies/**：选股逻辑独立成模块，可被回测和模拟盘共用

### 4.3 数据流
```
cron → account_runner.py --strategy v27 intraday_signal
  → strategy_map 查找 v27 的 select_fn
  → 动态加载 v27_select.select_stocks_v27()
  → 从 DB 加载面板数据（load_panel_from_db）
  → 计算因子 → 选股 → 生成 trade_plan_v27.json
  → 从 DB 加载账户状态（get_account + get_holdings）
  → 风控检查 → 输出信号报告
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

| 任务 | 时间 | 命令 |
|------|------|------|
| 账户1-上午信号 | 11:45 工作日 | `python scripts/sim/account_runner.py --strategy v11b intraday_signal` |
| 账户1-下午执行 | 13:00 工作日 | `python scripts/sim/account_runner.py --strategy v11b intraday_execute` |
| 账户2-上午信号 | 11:45 工作日 | `python scripts/sim/account_runner.py --strategy v27 intraday_signal` |
| 账户2-下午执行 | 13:00 工作日 | `python scripts/sim/account_runner.py --strategy v27 intraday_execute` |
| 账户3-尾盘信号 | 14:45 工作日 | `python scripts/sim/account_runner.py --strategy v20c tail_signal` |
| 账户3-尾盘执行 | 14:55 工作日 | `python scripts/sim/account_runner.py --strategy v20c tail_execute` |
| 收盘报告 | 15:30 工作日 | 三个账户 report_only |

> 2026-07-16 起，所有三个账户统一走 `account_runner.py`，旧 `sim_account1.py` 保留为备份。

## 七、回测与模拟盘一致性

- 回测引擎：`scripts/backtest/run_backtest.py`
- 共享代码：`core/account.py`（PortfolioState + buy/sell）
- 共享选股：`scripts/strategies/` 下的选股模块可被回测直接调用
- 数据源：统一从 `core/db.py` 读取（SQLite）
