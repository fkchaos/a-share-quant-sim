# A股量化模拟交易系统

> 基于多因子评分的 A 股量化模拟交易系统，使用腾讯行情接口获取数据。
> 回测引擎与模拟盘共享同一套交易逻辑（`core/`），策略参数集中在 `STRATEGY_PROFILES`。

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
│  account.py  ← PortfolioState + buy/sell/风控  │
│  db.py       ← SQLite 数据库层                 │
│  factors.py  ← 40 技术因子计算                 │
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
│   ├── factors.py           # 40个技术因子计算
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
│   ├── tools/               # 工具脚本
│   └── archive/             # 归档
│
└── docs/                    # 文档
    ├── ARCHITECTURE.md      # 架构文档
    ├── STRATEGY_REGISTRY.md # 策略注册表
    ├── RESULTS_LOG.md       # 回测结果记录
    ├── CONFIG_REFERENCE.md  # 参数参考
    ├── DEPLOY.md            # 部署文档
    ├── USER_MANUAL.md       # 用户手册
    └── archive/             # 归档文档
```

## 特性

- **盘中三阶段模式**: 11:45 上午出信号 → 13:00 下午开盘执行 → 15:30 收盘报告（纯只读）
- **Ensemble 多组选股**: 3 个因子组独立选股并集构建组合，自适应不同市场状态
- **40 个技术因子**: 动量/反转/波动率/成交量/RSI/趋势/统计/短线
- **共享交易逻辑**: 模拟盘和回测共用 `core/`，杜绝回测/实盘不一致
- **回测执行时序**: `--exec-timing close`（理想）/ `--exec-timing open`（接近实盘）
- **Walk-Forward 验证**: 16 folds 样本外检测过拟合
- **风控**: 止损 -20% / 分级止盈(10%/20%/30%) / 持有期衰减 / 行业 ≤25% / 换手率 ≤30%
- **交易成本**: 佣金 0.03% / 印花税 0.1%(卖出) / 滑点 0.1% / 100 股整数倍
- **数据质量**: 过期/空值/异常涨跌/复权跳变四维检查

## 当前策略

### 账户1：v11b_zz800_union（中线）
- **模式**：Ensemble 多组选股（3组×4因子，并集）
- **选股池**：中证800（800只）
- **全量回测**：30.43% / 1.16 / -27.49%（2021-01 ~ 2026-06）
- **WF 验证**：12.4% / 0.52 / 37.5%（6/16 正收益 fold）— WF 不通过，但全量最优
- **脚本**：`scripts/sim_account1.py`
- **状态**：⭐ 继续运行模拟盘

### 账户2：v13_small_mid_short（中短线）
- **模式**：评分排序选股（5日反转 + 量价因子）
- **选股池**：中证800 + 流动性过滤
- **全量回测**：49.87% / 2.48 / -13.46%
- **WF 验证**：14.9% / 1.05 / 94%（15/16 正收益 fold）— ✅ WF 通过
- **脚本**：`scripts/sim_account2.py`
- **状态**：✅ 模拟盘运行中

### 账户3：v20_tail_pick（尾盘缩量企稳）
- **模式**：尾盘缩量企稳选股
- **选股池**：中证800 + 流动性过滤
- **全量回测**：51.22% / 4.94 / -10.13%
- **WF 验证**：21.7% / 2.23 / 94%（15/16 正收益 fold）— ✅ WF 通过，全面优于账户2
- **脚本**：`scripts/sim_account3.py`
- **状态**：✅ 模拟盘运行中

## 策略对比（2021-01 ~ 2026-06，中证800 选股池）

| 策略 | 全量年化 | 全量夏普 | 全量回撤 | WF年化 | WF夏普 | 正收益fold | 状态 |
|------|---------|---------|---------|--------|--------|-----------|------|
| **v20_tail_pick** | **51.22%** | **4.94** | **-10.13%** | **21.7%** | **2.23** | **15/16 (94%)** | ✅ 尾盘，WF通过，全面最优 |
| **v13_small_mid_short** | **49.87%** | **2.48** | **-13.46%** | **14.9%** | **1.05** | **15/16 (94%)** | ✅ 中短线，WF通过 |
| **v11b_zz800_union** | 30.43% | 1.16 | -27.49% | 12.4% | 0.52 | 6/16 (37.5%) | ⭐ 中线全量最优 |
| v10c_zz800_balanced | 37.33% | 1.30 | -27.86% | 32.9% | 0.61 | 7/16 (44%) | WF未通过 |
| v6b_hlr | 22.64% | 1.23 | -19.63% | 10.5% | 0.41 | 11/16 (69%) | 稳定基准 |
| v14_resid_mom | 28.54% | 1.28 | -26.18% | 6.4% | 0.17 | 8/16 (50%) | ❌ 熊市fold -69% |
| v15_quality | 13.20% | 0.64 | -24.87% | — | — | — | ❌ 全量太差 |
| v16_mom_rev_hybrid | 12.98% | 0.56 | -30.93% | — | — | — | ❌ 信号抵消 |

> 完整策略列表见 [docs/STRATEGY_REGISTRY.md](docs/STRATEGY_REGISTRY.md)
> 已证伪策略详细记录见 [docs/STRATEGIES_DISCARDED.md](docs/STRATEGIES_DISCARDED.md)

## 快速开始

```bash
# 安装
git clone git@github.com:fkchaos/a-share-quant-sim.git
cd a-share-quant-sim
pip install -r requirements.txt

# 初始化数据（首次运行，约 3-5 分钟）
BACKTEST_DATA_DIR=/root/data python scripts/update_daily_data_async.py

# 回测最优策略 + Walk-Forward 验证
BACKTEST_DATA_DIR=/root/data python scripts/run_backtest.py --strategy v11b_zz800_union --walk-forward

# 模拟盘 — 账户1（v11b 中线，11:45信号/13:00执行/15:30报告）
BACKTEST_DATA_DIR=/root/data python scripts/sim_account1.py intraday_signal
BACKTEST_DATA_DIR=/root/data python scripts/sim_account1.py intraday_execute
BACKTEST_DATA_DIR=/root/data python scripts/sim_account1.py report_only

# 模拟盘 — 账户2（v13 中短线，11:45信号/13:00执行/15:30报告）
BACKTEST_DATA_DIR=/root/data python scripts/sim_account2.py intraday_signal
BACKTEST_DATA_DIR=/root/data python scripts/sim_account2.py intraday_execute
BACKTEST_DATA_DIR=/root/data python scripts/sim_account2.py report_only

# 模拟盘 — 账户3（v20 尾盘，14:40信号/14:55执行/15:30报告）
BACKTEST_DATA_DIR=/root/data python scripts/sim_account3.py tail_signal
BACKTEST_DATA_DIR=/root/data python scripts/sim_account3.py tail_execute
BACKTEST_DATA_DIR=/root/data python scripts/sim_account3.py report_only

# 测试
python -m pytest tests/test_sim_trading.py tests/test_ensemble.py -v  # 58 tests
```

## 文件结构

```
a-share-quant-sim/
├── core/                       # 共享引擎（回测+模拟盘共用）
│   ├── config.py               # STRATEGY_PROFILES + MarketFilter
│   ├── factors.py              # 40+ 因子计算
│   ├── scoring.py              # Z-score + Ensemble 多组选股
│   ├── strategy.py             # StrategyEngine (factor/ensemble/ml/hybrid/multi)
│   ├── account.py              # PortfolioState + 交易/风控 API
│   ├── data.py                 # 数据加载 + 市场过滤
│   ├── position.py             # Position 领域模型
│   ├── industry_rotation.py    # 行业轮动因子（待数据源就绪）
│   ├── quality_data.py         # 质量因子数据获取
│   ├── hmm_timing.py           # HMM 市场状态识别（已弃用）
│   ├── ml.py                   # ML 训练/预测（已弃用）
│   └── ml_predictor.py         # ML 离线训练 + 在线推理（已弃用）
├── scripts/
│   ├── sim_account1.py        # ⭐ 账户1 模拟盘（v11b 中线，三阶段）
│   ├── sim_account2.py        # ⭐ 账户2 模拟盘（v13 中短线，三阶段）
│   ├── sim_account3.py        # ⭐ 账户3 模拟盘（v20 尾盘）
│   ├── cli.py                 # DB CLI（buy/sell/account/holdings/trades/kline/stats）
│   ├── run_backtest.py        # 统一回测引擎（含 WF）
│   ├── update_daily_data_async.py # 数据更新：腾讯 API → DB
│   ├── v13_small_mid_short.py  # v13 回测脚本
│   ├── v13_walk_forward.py     # v13 WF 检测
│   ├── ic_analysis_zz800.py    # 中证800 IC/IR 分析
│   ├── init_zz800_data.py      # 数据初始化
│   ├── fill_daily_gaps.py      # 缺口填充
│   ├── industry_data_v2.py      # 行业分类数据获取（国证，多线程）
│   ├── quality_data.py          # 质量因子数据获取（AKShare THS）
│   ├── train_ml_model.py       # ML 训练
│   ├── ml_rolling_train.py     # ML Walk-Forward 回测
│   ├── run_signal_skip_update.py # 应急信号脚本
│   ├── constraints.py          # A股交易约束
│   ├── data_quality.py         # 数据质量门禁
│   ├── portfolio_controls.py   # 换手率上限
│   ├── industry.py             # 行业分类
│   ├── indices.py              # 指数趋势
│   └── sim_logging.py          # 日志配置
├── tests/
│   ├── test_sim_trading.py     # 39 个模拟盘执行测试
│   ├── test_ensemble.py        # 19 个 Ensemble 评分测试
│   └── test_golden.py          # 12 个 Golden 测试
├── docs/
│   ├── architecture.md         # 架构详解
│   ├── DEPLOY.md               # 部署文档
│   ├── RESULTS_LOG.md          # 回测结果记录
│   ├── STRATEGY_REGISTRY.md    # 策略注册表
│   ├── BACKLOG.md              # 待办事项
│   └── HISTORY.md              # 已解决问题
└── core/config.py             # 策略参数 + 交易成本（唯一配置源）
```

## 文档索引

| 文档 | 内容 |
|------|------|
| [docs/USER_MANUAL.md](docs/USER_MANUAL.md) | 📖 完整使用说明（命令/参数/配置/工作流） |
| [docs/CONFIG_REFERENCE.md](docs/CONFIG_REFERENCE.md) | 配置参数详解 |
| [docs/STRATEGY_REGISTRY.md](docs/STRATEGY_REGISTRY.md) | 策略注册表（参数+绩效+WF） |
| [docs/STRATEGIES_DISCARDED.md](docs/STRATEGIES_DISCARDED.md) | 已证伪策略详细记录 |
| [docs/architecture.md](docs/architecture.md) | 代码架构详解（面向开发者） |
| [docs/DEPLOY.md](docs/DEPLOY.md) | 部署指南（cron/环境配置） |
| [docs/RESULTS_LOG.md](docs/RESULTS_LOG.md) | 回测结果记录 |
| [docs/HISTORY.md](docs/HISTORY.md) | 已解决问题记录 |
| [docs/BACKLOG.md](docs/BACKLOG.md) | 待办事项 |
