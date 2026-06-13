# A股量化模拟交易系统

> 基于多因子评分的 A 股量化模拟交易系统，使用腾讯行情接口获取数据。
> 回测引擎与模拟盘共享同一套交易逻辑（`core/`），策略参数集中在各选股模块的 Config 中。

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## 架构概览

```
┌──────────────────────────────────────────────────────────────┐
│                     cron 调度层（7个任务）                      │
│  账户1(v11b)  账户2(v27)  账户3(v20c)  收盘报告                 │
└──────────┬──────────────────────────┬───────────────────────┘
           │                          │
           ▼                          ▼
┌─────────────────────┐   ┌──────────────────────────────────┐
│ scripts/sim/        │   │ core/strategy_map.py             │
│ sim_account1.py     │   │  策略注册表（动态加载选股函数）      │
│ (v11b legacy)       │   │  v11b → legacy 模式              │
│                     │   │  v27  → v27_select.py           │
│ account_runner.py   │◄──│  v20c → v20_tail_pick.py        │
│ (统一入口)           │   └──────────────────────────────────┘
│ --strategy v27|v20c │
│ intraday_signal     │   ┌──────────────────────────────────┐
│ intraday_execute    │   │ scripts/strategies/               │
│ tail_signal         │   │  选股逻辑（可被回测+模拟盘共用）     │
│ report_only         │   └──────────────────────────────────┘
└──────────┬──────────┘
           │
           ▼
┌──────────────────────────────────────────────┐
│                  core/ (共享引擎)              │
│  config.py   ← STRATEGY_PROFILES + MarketFilter│
│  account.py  ← PortfolioState + buy/sell       │
│  db.py       ← SQLite 数据库层                 │
│  factors.py  ← 51 技术因子计算                 │
│  scoring.py  ← Z-score + Ensemble 评分         │
│  strategy.py ← StrategyEngine                  │
└──────────────────────────────────────────────┘
           ▲
           │ 数据
┌──────────┴──────────┐
│ /root/data/quant.db  │
│  account/holdings/   │
│  trade_log/daily_kline│
└─────────────────────┘
```

## 目录结构

```text
a-share-quant-sim/
├── core/                    # 共享引擎（回测+模拟盘共用）
│   ├── config.py            # 策略配置、交易成本、风控参数
│   ├── account.py           # PortfolioState + buy/sell 纯函数
│   ├── db.py                # SQLite 数据库层
│   ├── strategy_map.py      # 策略注册表（动态加载选股函数）
│   ├── factors.py           # 51个技术因子计算
│   ├── scoring.py           # Z-score + Ensemble 评分
│   └── strategy.py          # StrategyEngine
│
├── scripts/
│   ├── sim/                 # 模拟盘执行层
│   │   ├── account_runner.py    # 统一入口（v27/v20c）
│   │   ├── sim_account1.py      # v11b legacy
│   │   └── sim_account2_v13.py  # v13 备份
│   │
│   ├── strategies/          # 选股逻辑模块
│   │   ├── v27_select.py        # v27 价量共振选股
│   │   └── v20_tail_pick.py     # v20c 尾盘缩量选股
│   │
│   ├── backtest/            # 回测脚本
│   │   └── run_backtest.py
│   │
│   ├── tools/               # 工具脚本
│   │   ├── cli.py                # DB CLI
│   │   ├── update_daily_data_async.py  # 数据更新
│   │   └── ...
│   │
│   └── archive/             # 归档旧版本
│
└── docs/                    # 文档
    ├── ARCHITECTURE.md      # 架构文档
    ├── USER_MANUAL.md       # 用户手册
    ├── DEPLOY.md            # 部署指南
    ├── STRATEGY_REGISTRY.md # 策略注册表
    ├── RESULTS_LOG.md       # 回测结果记录
    ├── CONFIG_REFERENCE.md  # 参数参考
    └── archive/             # 归档文档
```

## 特性

- **账户-策略解耦**：strategy_map 注册表 + account_runner 统一入口，新增策略只需注册一行
- **三账户并行**：v11b(legacy) + v27(价量共振) + v20c(尾盘缩量)，互补运行
- **盘中三阶段模式**：11:45 上午出信号 → 13:00 下午开盘执行 → 15:30 收盘报告（纯只读）
- **51 个技术因子**：动量/反转/波动率/成交量/RSI/趋势/统计/短线/价量共振
- **共享交易逻辑**：模拟盘和回测共用 `core/`，杜绝回测/实盘不一致
- **回测执行时序**：`--exec-timing close`（理想）/ `--exec-timing open`（接近实盘）
- **Walk-Forward 验证**：16 folds 样本外检测过拟合
- **风控**：止损/止盈/超时/行业分散/换手率上限
- **交易成本**：佣金 0.03% / 印花税 0.1%(卖出) / 滑点 0.1% / 100 股整数倍
- **数据质量**：过期/空值/异常涨跌/复权跳变四维检查

## 当前策略

| 账户 | 策略 | 模式 | 资金 | 全量年化 | WF夏普 | 状态 |
|------|------|------|------|---------|--------|------|
| 账户1 | v11b | Ensemble 多组选股(legacy) | 20万 | ~30% | 0.52 | ⭐ 继续运行 |
| 账户2 | v27 | 价量共振(account_runner) | 10万 | 251% | 8.66 | ✅ WF最优 |
| 账户3 | v20c | 尾盘缩量(account_runner) | 10万 | 52.4% | 1.34 | ✅ WF通过 |

> 完整策略列表见 [docs/STRATEGY_REGISTRY.md](docs/STRATEGY_REGISTRY.md)
> 已证伪策略见 [docs/STRATEGIES_DISCARDED.md](docs/STRATEGIES_DISCARDED.md)

## 快速开始

```bash
# 安装
git clone git@github.com:fkchaos/a-share-quant-sim.git
cd a-share-quant-sim
pip install pandas numpy requests

# 初始化数据（首次运行，约 1 分钟）
export BACKTEST_DATA_DIR=/root/data
export PYTHONPATH=/root/a-share-quant-sim
python scripts/tools/update_daily_data_async.py

# 回测 + Walk-Forward 验证
python scripts/backtest/run_backtest.py --strategy v27 --walk-forward

# 模拟盘 — 账户1（v11b legacy）
python scripts/sim/sim_account1.py intraday_signal   # 11:45
python scripts/sim/sim_account1.py intraday_execute  # 13:00
python scripts/sim/sim_account1.py report_only       # 15:30

# 模拟盘 — 账户2（v27 价量共振）
python scripts/sim/account_runner.py --strategy v27 intraday_signal
python scripts/sim/account_runner.py --strategy v27 intraday_execute
python scripts/sim/account_runner.py --strategy v27 report_only

# 模拟盘 — 账户3（v20c 尾盘缩量）
python scripts/sim/account_runner.py --strategy v20c tail_signal
python scripts/sim/account_runner.py --strategy v20c tail_execute
python scripts/sim/account_runner.py --strategy v20c report_only

# 测试
python -m pytest tests/ -v -k "not slow"  # 70 tests, <1s
```

## 文档索引

| 文档 | 内容 |
|------|------|
| [docs/USER_MANUAL.md](docs/USER_MANUAL.md) | 📖 完整使用说明（命令/参数/配置/工作流） |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 架构详解（解耦设计/数据流/DB读写） |
| [docs/DEPLOY.md](docs/DEPLOY.md) | 部署指南（环境/cron/数据/备份） |
| [docs/STRATEGY_REGISTRY.md](docs/STRATEGY_REGISTRY.md) | 策略注册表（参数+绩效+WF） |
| [docs/STRATEGIES_DISCARDED.md](docs/STRATEGIES_DISCARDED.md) | 已证伪策略记录 |
| [docs/CONFIG_REFERENCE.md](docs/CONFIG_REFERENCE.md) | 配置参数详解 |
| [docs/RESULTS_LOG.md](docs/RESULTS_LOG.md) | 回测结果记录 |
| [docs/BACKLOG.md](docs/BACKLOG.md) | 待办事项 |

## License

MIT — use freely, modify as needed. Not financial advice. Simulation only.
