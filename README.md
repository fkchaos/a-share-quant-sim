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
│ account_runner.py   │◄──│  策略注册表（动态加载选股函数）      │
│ --strategy v27|v20c │   │  v11b → legacy 模式              │
└──────────┬──────────┘   │  v27  → v27_select.py           │
           │              │  v20c → v20_tail_pick.py        │
           ▼              └──────────────────────────────────┘
┌──────────────────────────────────────────────┐
│                  core/ (共享引擎)              │
│  config.py   ← STRATEGY_PROFILES + MarketFilter│
│  account.py  ← PortfolioState + buy/sell       │
│  db.py       ← SQLite 数据库层                 │
│  factors.py  ← 51 技术因子计算                 │
│  scoring.py  ← Z-score + Ensemble 评分         │
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
│   ├── strategy_map.py      # 策略注册表
│   ├── factors.py           # 51个技术因子计算
│   ├── scoring.py           # Z-score + Ensemble 评分
│   └── strategy.py          # StrategyEngine
│
├── scripts/
│   ├── sim/                 # 模拟盘执行层
│   │   ├── account_runner.py    # 统一入口（v11b/v27/v20c）
│   │   └── sim_account1.py      # v11b legacy（备份，不再被 cron 调用）
│   ├── strategies/          # 选股逻辑模块
│   │   ├── v27_select.py        # v27 价量共振选股
│   │   └── v20_tail_pick.py     # v20c 尾盘缩量选股
│   ├── backtest/            # 回测脚本
│   ├── tools/               # 工具脚本
│   │   ├── cli.py                # 数据库 CLI（账户/持仓/买卖）
│   │   └── update_daily_data_async.py
│   └── archive/             # 归档旧版本
│
└── docs/                    # 文档
    ├── DEPLOY.md            # 部署指南
    ├── USER_MANUAL.md       # 用户手册
    ├── ARCHITECTURE.md      # 架构文档
    └── STRATEGY_REGISTRY.md # 策略注册表
```

## 特性

- **零配置**：`pip install pandas numpy requests`，3 个依赖，5 分钟跑通
- **账户-策略解耦**：strategy_map 注册表 + account_runner 统一入口
- **三账户并行**：v11b(Ensemble) + v27(价量共振) + v20c(尾盘缩量)
- **共享交易逻辑**：模拟盘和回测共用 `core/`，杜绝不一致
- **51 个技术因子**：动量/反转/波动率/成交量/RSI/趋势/统计/短线
- **Walk-Forward 验证**：16 folds 样本外检测过拟合
- **完整 CLI**：账户管理、持仓调整、手动买卖，不需要写 SQL
- **中文文档齐全**：部署指南、用户手册、架构文档、策略注册表
- **MIT 协议**：商用友好

## 快速开始

```bash
# 1. 克隆 + 安装
git clone git@github.com:fkchaos/a-share-quant-sim.git
cd a-share-quant-sim
pip install pandas numpy requests

# 2. 初始化数据（首次运行，约 1 分钟）
export PYTHONPATH=$(pwd)
export BACKTEST_DATA_DIR=/root/data
mkdir -p $BACKTEST_DATA_DIR
python scripts/tools/update_daily_data_async.py

# 3. 跑回测
python scripts/backtest/run_backtest.py --strategy v27

# 4. 跑模拟盘
python scripts/sim/account_runner.py --strategy v27 intraday_signal
python scripts/sim/account_runner.py --strategy v27 intraday_execute
python scripts/sim/account_runner.py --strategy v27 report_only

# 5. 测试
python -m pytest tests/ -v -k "not slow"
```

## 当前策略

| 账户 | 策略 | 模式 | 资金 | 全量年化 | WF夏普 | 状态 |
|------|------|------|------|---------|--------|------|
| 账户1 | v11b | Ensemble 多组选股 | 20万 | ~30% | 0.52 | 继续运行 |
| 账户2 | v27 | 价量共振 | 10万 | 251% | 8.66 | WF最优 |
| 账户3 | v20c | 尾盘缩量 | 10万 | 78% | 5.74 | WF通过 |

> 见 [docs/STRATEGY_REGISTRY.md](docs/STRATEGY_REGISTRY.md) 完整策略列表

## License

MIT — use freely, modify as needed. Not financial advice. Simulation only.
