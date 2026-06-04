# A股量化模拟交易系统

> 基于多因子评分的 A 股量化模拟交易系统，使用腾讯行情接口获取数据。
> 回测引擎与模拟盘共享同一套交易逻辑（`core/`），策略参数集中在 `config.yaml`。

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## 架构概览

```
┌──────────────────┐          ┌──────────────────┐
│  sim_daily_v7.py │          │  run_backtest.py │
│  (盘中双阶段)     │          │  (回测引擎)       │
│ 11:35信号13:00执行│          │ --exec-timing    │
└────────┬─────────┘          └────────┬─────────┘
         │ 调用                         │ 调用
         ▼                             ▼
┌─────────────────────────────────────────────┐
│                  core/ (共享引擎)             │
│  config.py  ← 加载 config.yaml + STRATEGY_PROFILES │
│  factors.py ← calc_factors_single / _panel  │
│  account.py ← PortfolioState + buy/sell/    │
│               check_stop_loss / portfolio_value │
│  scoring.py ← Z-score + IC_IR 加权评分       │
└─────────────────────────────────────────────┘
         ▲                             ▲
         │ 数据                         │ 数据
┌────────┴─────────┐          ┌────────┴─────────┐
│ update_daily_    │          │ data/daily/       │
│ data.py          │          │ (本地 CSV 面板)    │
│ (腾讯 API → CSV) │          └──────────────────┘
└──────────────────┘
```

## 特性

- **盘中双阶段模式**: 11:35 上午出信号 → 13:00 下午开盘执行 → 15:30 收盘报告
- **多因子策略**: 40 个技术因子，支持等权 / IC_IR 加权 / Markowitz 优化
- **10+ 预置策略**: v4~v10 系列，详见 [docs/STRATEGY_REGISTRY.md](docs/STRATEGY_REGISTRY.md)
- **共享交易逻辑**: 模拟盘和回测共用 `core/account.py`，杜绝回测 / 实盘不一致
- **回测执行时序**: `--exec-timing close`（收盘价，理想情况）/ `--exec-timing open`（开盘价，接近实盘）
- **风控**: 止损 -20% / 分级止盈(10%/20%/30%) / 持有期衰减 / 行业 ≤25% / 换手率 ≤30%
- **交易成本**: 佣金 0.03% / 印花税 0.1%(卖出) / 滑点 0.1% / 100 股整数倍
- **数据质量**: 过期 / 空值 / 异常涨跌 / 复权跳变四维检查

## 当前最优策略

**v10c_zz800_balanced** — 中证800 IC 最优因子策略（2026-06-04 更新）

| 指标 | close 执行（理想） | Walk-Forward（样本外） |
|------|-------------------|----------------------|
| 年化收益 | 38.59% | 32.9%（16 folds 平均） |
| 夏普比率 | 1.34 | 0.61 |
| 最大回撤 | -27.86% | — |
| Calmar | 1.39 | — |
| 正收益 fold | — | 7/16 (44%) |

> 数据：中证800 成分股 674 只，2021-01 ~ 2026-06，初始资金 20 万
> 选股池从沪深300（280只）扩大到中证800（730只）
> 模拟盘默认策略：ml_hybrid80（80%ML+20%v6b）

## 快速开始

详见 [docs/DEPLOY.md](docs/DEPLOY.md) 部署文档。

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 初始化日K线数据（首次运行，需要网络）
python scripts/update_daily_data.py

# 3. 验证：回测最优策略
python scripts/run_backtest.py --strategy v10c_zz800_balanced

# 4. Walk-Forward 过拟合检测
python scripts/run_backtest.py --strategy v10c_zz800_balanced --walk-forward
```

## 回测命令速查

```bash
python scripts/run_backtest.py                                    # 全策略回测（close执行）
python scripts/run_backtest.py --strategy v10c_zz800_balanced         # 当前最优策略
python scripts/run_backtest.py --strategy v10c --exec-timing open    # 开盘执行回测
python scripts/run_backtest.py --scan                            # 参数网格扫描
python scripts/run_backtest.py --walk-forward                    # Walk-Forward 过拟合检测
python scripts/run_backtest.py --ic-analysis                     # IC 因子分析
python scripts/run_backtest.py --report-markdown > report.md     # Markdown 报告
```

## 项目结构

```
a-share-quant-sim/
├── core/                      # ⭐ 共享引擎 (回测 + 模拟盘共用)
│   ├── config.py              # Config + STRATEGY_PROFILES + config.yaml loader
│   ├── factors.py             # 因子计算 (单股 + 面板)
│   ├── account.py             # PortfolioState + buy/sell/止损/止盈
│   └── scoring.py             # Z-score + 加权评分
├── scripts/
│   ├── sim_daily_v7.py        # ⭐ 每日模拟盘 (v7, 盘中双阶段)
│   ├── run_backtest.py        # 统一回测引擎
│   ├── update_daily_data.py   # 数据更新: 腾讯 API → CSV (支持 BACKTEST_DATA_DIR)
│   ├── constraints.py         # A股交易约束 (涨跌停/T+1/停牌)
│   ├── data_quality.py        # 数据质量门禁
│   ├── portfolio_controls.py  # 换手率上限
│   ├── industry.py            # 行业分类 + 仓位上限
│   ├── indices.py             # 指数趋势
│   └── sim_logging.py         # 日志配置
├── config.yaml                # ⭐ 所有可调参数
├── data/
│   ├── daily/                 # 日K线 CSV (中证800, ~730只)
│   ├── portfolio/             # 账户状态 (account.json)
│   └── signals/               # 信号缓存
├── docs/
│   ├── architecture.md        # 架构详解
│   ├── DEPLOY.md              # 部署文档
│   ├── RESULTS_LOG.md         # 回测结果记录
│   ├── STRATEGY_REGISTRY.md   # 策略注册表（所有策略参数+回测）
│   ├── BACKLOG.md             # 待办事项
│   └── HISTORY.md             # 已解决问题
└── requirements.txt
```

## 策略对比（2021-01 ~ 2026-06，中证800 选股池）

| 策略 | close年化 | close夏普 | WF年化 | WF夏普 | 状态 |
|------|---------|---------|--------|--------|------|
| **v10c_zz800_balanced** ⚡ | **38.59%** | **1.34** | **32.9%** | **0.61** | ✅ 当前最优 |
| v6b_hlr | 22.64% | 1.23 | 10.5% | 0.41 | ✅ 基准 |
| ml_hybrid80 | 25.87% | 1.19 | — | — | ✅ 模拟盘 |
| v10_zz800_top_ir | 35.45% | 1.26 | — | — | 回撤大 |

> v10c = 中证800 IC 最优因子（13因子），WF = 16 folds 样本外
> 完整策略列表见 [docs/STRATEGY_REGISTRY.md](docs/STRATEGY_REGISTRY.md)

## 配置

所有参数在 `config.yaml` 中，优先级：`命令行 > config.yaml > 内置默认`。

关键配置项：

```yaml
costs:
  initial_capital: 200000    # 初始资金（模拟盘改这里）
data:
  daily_dir: "data/daily"   # 数据目录（也可设 BACKTEST_DATA_DIR 环境变量）
strategies:
  v6b_8f_pos_ic:            # 默认策略参数
    top_n: 12
    rebalance_freq: 20
    stop_loss: 0.20
```

## 分支策略

```
main             主分支 — 唯一开发分支，cron job 从这里拉取执行
release/default  发布分支 — 稳定版本标记

开功能:  git checkout -b feature/xxx → 开发 → merge main → merge release
```

> 2026-06-01 起用 main 替代 dev/default 作为唯一开发分支

## 注意事项

- **仅供学习研究，不构成投资建议**
- 数据源为腾讯行情接口，免费但可能有不稳定时段
- 因子策略基于历史数据，不代表未来收益
- open 模式回测更接近实盘，close 模式是理想上界

## License

MIT License — 详见 [LICENSE](LICENSE)
