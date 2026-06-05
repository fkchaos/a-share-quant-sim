# A股量化模拟交易系统

> 基于多因子评分的 A 股量化模拟交易系统，使用腾讯行情接口获取数据。
> 回测引擎与模拟盘共享同一套交易逻辑（`core/`），策略参数集中在 `STRATEGY_PROFILES`。

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## 架构概览

```
┌──────────────────┐          ┌──────────────────┐
│  sim_daily_v7.py │          │  run_backtest.py │
│  (盘中三阶段)     │          │  (回测引擎)       │
│ 11:35信号13:00执行│          │ --exec-timing    │
│ 15:30收盘报告     │          │ --walk-forward   │
└────────┬─────────┘          └────────┬─────────┘
         │ 调用                         │ 调用
         ▼                             ▼
┌─────────────────────────────────────────────┐
│                  core/ (共享引擎)             │
│  config.py  ← STRATEGY_PROFILES + MarketFilter│
│  factors.py ← 40 技术因子计算                 │
│  scoring.py ← Z-score + Ensemble 多组选股     │
│  strategy.py← StrategyEngine (4种模式)        │
│  account.py ← PortfolioState + buy/sell/风控  │
│  data.py    ← load_and_build_panel            │
└─────────────────────────────────────────────┘
         ▲                             ▲
         │ 数据                         │ 数据
┌────────┴─────────┐          ┌────────┴─────────┐
│ update_daily_    │          │ data/daily/       │
│ data.py          │          │ (中证800, ~715只) │
│ (腾讯 API → CSV) │          └──────────────────┘
└──────────────────┘
```

## 特性

- **盘中三阶段模式**: 11:35 上午出信号 → 13:00 下午开盘执行 → 15:30 收盘报告（纯只读）
- **Ensemble 多组选股**: 3 个因子组独立选股并集构建组合，自适应不同市场状态
- **40 个技术因子**: 动量/反转/波动率/成交量/RSI/趋势/统计/短线
- **共享交易逻辑**: 模拟盘和回测共用 `core/`，杜绝回测/实盘不一致
- **回测执行时序**: `--exec-timing close`（理想）/ `--exec-timing open`（接近实盘）
- **Walk-Forward 验证**: 16 folds 样本外检测过拟合
- **风控**: 止损 -20% / 分级止盈(10%/20%/30%) / 持有期衰减 / 行业 ≤25% / 换手率 ≤30%
- **交易成本**: 佣金 0.03% / 印花税 0.1%(卖出) / 滑点 0.1% / 100 股整数倍
- **数据质量**: 过期/空值/异常涨跌/复权跳变四维检查

## 当前策略

### 模拟盘 A：v11b_zz800_union（中线）
- **模式**：Ensemble 多组选股（3组×4因子，并集）
- **选股池**：中证800（715只）
- **WF 验证**：✅ 63.7% / 1.70 / 69%
- **脚本**：`scripts/sim_daily_v7.py`

### 模拟盘 B：v13_small_mid_short（中短线）
- **模式**：规则化因子选股（5日反转 + 量价异动 + 振幅收窄）
- **选股池**：中证800 + 流动性过滤（300万-1亿）
- **WF 验证**：✅ 13.2% / 1.03 / 94%
- **脚本**：`scripts/sim_v13.py`
- **初始资金**：20万，独立账户 `account_v13.json`

> 数据：中证800 成分股 715 只，2021-01 ~ 2026-06

## 策略对比（2021-01 ~ 2026-06，中证800 选股池）

| 策略 | 全量年化 | 全量夏普 | WF年化 | WF夏普 | 正收益fold | 状态 |
|------|---------|---------|--------|--------|-----------|------|
| **v11b_zz800_union** | 26.25% | 1.05 | **63.7%** | **1.70** | **11/16 (69%)** | ⭐ 中线最优 |
| v10c_zz800_balanced | 37.33% | 1.30 | 32.9% | 0.61 | 7/16 (44%) | WF未通过 |
| v6b_hlr | 22.64% | 1.23 | 10.5% | 0.41 | 11/16 (69%) | 稳定基准 |
| **v13_small_mid_short** | — | — | **13.2%** | **1.03** | **15/16 (94%)** | ✅ 中短线 |

> 完整策略列表见 [docs/STRATEGY_REGISTRY.md](docs/STRATEGY_REGISTRY.md)

## 快速开始

```bash
# 安装
git clone git@github.com:fkchaos/a-share-quant-sim.git
cd a-share-quant-sim
pip install -r requirements.txt

# 初始化数据（首次运行，约 3-5 分钟）
BACKTEST_DATA_DIR=/root/data python scripts/update_daily_data.py

# 回测最优策略 + Walk-Forward 验证
BACKTEST_DATA_DIR=/root/data python scripts/run_backtest.py --strategy v11b_zz800_union --walk-forward

# 模拟盘三阶段（v11b 中线）
BACKTEST_DATA_DIR=/root/data python scripts/sim_daily_v7.py intraday_signal   # 上午信号
BACKTEST_DATA_DIR=/root/data python scripts/sim_daily_v7.py intraday_execute  # 下午执行
BACKTEST_DATA_DIR=/root/data python scripts/sim_daily_v7.py report_only       # 收盘报告

# 模拟盘三阶段（v13 中短线）
BACKTEST_DATA_DIR=/root/data python scripts/sim_v13.py intraday_signal   # 上午信号
BACKTEST_DATA_DIR=/root/data python scripts/sim_v13.py intraday_execute  # 下午执行
BACKTEST_DATA_DIR=/root/data python scripts/sim_v13.py report_only       # 收盘报告

# 测试
python -m pytest tests/test_sim_trading.py tests/test_ensemble.py -v  # 58 tests
```

## 文件结构

```
a-share-quant-sim/
├── core/                       # 共享引擎（回测+模拟盘共用）
│   ├── config.py               # STRATEGY_PROFILES + MarketFilter
│   ├── factors.py              # 40 因子计算
│   ├── scoring.py              # Z-score + Ensemble 多组选股
│   ├── strategy.py             # StrategyEngine (factor/ensemble/ml/hybrid)
│   ├── account.py              # PortfolioState + 交易/风控 API
│   ├── data.py                 # 数据加载 + 市场过滤
│   ├── position.py             # Position 领域模型
│   ├── ml.py                   # ML 训练/预测（Walk-Forward 回测用）
│   └── ml_predictor.py         # ML 离线训练 + 在线推理
├── scripts/
│   ├── sim_daily_v7.py         # ⭐ 每日模拟盘 A（v11b 中线，三阶段）
│   ├── sim_v13.py              # ⭐ 每日模拟盘 B（v13 中短线，三阶段）
│   ├── run_backtest.py         # 统一回测引擎（含 WF）
│   ├── update_daily_data.py    # 数据更新：腾讯 API → CSV
│   ├── v13_small_mid_short.py  # v13 回测脚本
│   ├── v13_walk_forward.py     # v13 WF 检测
│   ├── ic_analysis_zz800.py    # 中证800 IC/IR 分析
│   ├── init_zz800_data.py      # 数据初始化
│   ├── fill_daily_gaps.py      # 缺口填充
│   ├── data_fetcher.py         # 多源数据获取
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

## 分支策略

```
main             主分支 — 唯一开发分支，cron job 从这里拉取执行
release/default  发布分支 — 与 main 同步
```

## 注意事项

- **仅供学习研究，不构成投资建议**
- 数据源为腾讯行情接口，免费但可能有不稳定时段
- 因子策略基于历史数据，不代表未来收益
- open 模式回测更接近实盘，close 模式是理想上界
- 收盘报告（report_only）用本地已有价格，净值可能有 1 天误差

## License

MIT License — 详见 [LICENSE](LICENSE)
