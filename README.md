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
- **多因子策略**: 29 个技术因子，支持等权 / IC_IR 加权 / Markowitz 优化
- **8 个预置策略**: v4_baseline / v5_tp_decay / v6a_12f_icir / v6b_8f_pos_ic（默认）/ v7a/b/c / v8_all_icir
- **共享交易逻辑**: 模拟盘和回测共用 `core/account.py`，杜绝回测 / 实盘不一致
- **回测执行时序**: `--exec-timing close`（收盘价，理想情况）/ `--exec-timing open`（开盘价，接近实盘）
- **风控**: 止损 -20% / 分级止盈(10%/20%/30%) / 持有期衰减 / 行业 ≤25% / 换手率 ≤30%
- **交易成本**: 佣金 0.03% / 印花税 0.1%(卖出) / 滑点 0.1% / 100 股整数倍
- **数据质量**: 过期 / 空值 / 异常涨跌 / 复权跳变四维检查

## 当前最优策略

**v6b_hlr** — 9 因子（v6b_8f + high_low_range），日内振幅因子

| 指标 | close 执行（理想） |
|------|-------------------|
| 年化收益 | 23.81% |
| 夏普比率 | 1.34 |
| 最大回撤 | -21.14% |
| Calmar | 1.13 |

> 数据：沪深 300 成分股 ~280 只，2021-01 ~ 2026-05，初始资金 20 万
> 模拟盘默认策略（sim_daily_v7）

## 快速开始

详见 [docs/DEPLOY.md](docs/DEPLOY.md) 部署文档。

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 初始化日K线数据（首次运行，需要网络）
python scripts/update_daily_data.py

# 3. 验证：回测最优策略
python scripts/run_backtest.py --strategy v6b_8f_pos_ic

# 4. 模拟盘（盘中双阶段需要配置 cron）
python scripts/sim_daily_v7.py intraday_signal   # 上午信号
python scripts/sim_daily_v7.py intraday_execute  # 下午执行
python scripts/sim_daily_v7.py day_end           # 收盘报告
```

## 回测命令速查

```bash
python scripts/run_backtest.py                                    # 全策略回测（close执行）
python scripts/run_backtest.py --strategy v6b_8f_pos_ic          # 指定策略
python scripts/run_backtest.py --strategy v6b --exec-timing open # 开盘执行回测
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
│   ├── daily/                 # 日K线 CSV (~280只)
│   ├── portfolio/             # 账户状态 (account.json)
│   └── signals/               # 信号缓存
├── docs/
│   ├── architecture.md        # 架构详解
│   ├── DEPLOY.md              # 部署文档
│   ├── RESULTS_LOG.md         # 回测结果记录
│   └── BACKLOG.md             # 待办事项
└── requirements.txt
```

## 策略对比（2021-01 ~ 2026-05，取整修复后）

| 策略 | close年化 | close夏普 | open年化 | open夏普 |
|------|---------|---------|---------|---------|
| **v6b_hlr** ⚡ | **23.81%** | **1.34** | — | — |
| v6b_8f_pos_ic | 23.81% | 1.33 | 17.52% | 1.05 |
| v8_all_icir | 21.49% | 1.25 | 16.98% | 0.99 |
| v4_baseline | 22.19% | 1.06 | 10.58% | 0.57 |
| v5_tp_decay | 18.55% | 1.14 | 11.57% | 0.79 |
| v4_industry_cap | 21.64% | 1.06 | 8.45% | 0.49 |

> v6b_hlr = v6b_8f + high_low_range（日内振幅因子，IC +0.02~0.06）
> open 模式 = T-1 日信号 → T 日开盘执行，更接近实盘
> v9_short_term (freq=5): RETIRED — A股 T+1 + 交易成本下短线不可行

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
