# 用户手册

> A股量化模拟交易系统 — 完整使用说明
> 涵盖所有命令、参数、配置和示例。

---

## 一、系统概览

### 架构

```
sim_daily_v7.py (中线模拟盘) ──┐
                                ├──▶ core/ 共享引擎
run_backtest.py (回测引擎) ────┘
```

- **回测引擎**：`scripts/run_backtest.py` — 历史数据回测 + Walk-Forward 验证
- **模拟盘 A**：`scripts/sim_daily_v7.py` — v11b 中线策略，三阶段（信号/执行/报告）
- **模拟盘 B**：`scripts/sim_v13.py` — v13 中短线策略，三阶段（信号/执行/报告）
- **共享引擎**：`core/` — 回测和模拟盘共用同一套交易逻辑

### 策略模式

| 模式 | 说明 | 使用场景 |
|------|------|---------|
| `factor` | 单组因子加权评分 | 大多数策略 |
| `ensemble` | 多组独立选股并集 | v11b |
| `multi` | 多策略并行加权 | v12（已证伪） |
| `ml` | ML 模型预测 | 已证伪 |
| `hybrid` | ML + 因子混合 | 已证伪 |

---

## 二、环境配置

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BACKTEST_DATA_DIR` | `data/` | 数据目录（日 K 线 CSV） |
| `PORTFOLIO_DIR` | `data/portfolio/` | 账户状态目录 |

```bash
# 示例：使用外部数据目录
export BACKTEST_DATA_DIR=/root/data
export PORTFOLIO_DIR=/root/data/portfolio
```

### 数据目录结构

```
data/
├── daily/              # 日K线 CSV（update_daily_data.py 维护）
│   ├── 600000.csv
│   └── ...
├── portfolio/          # 账户状态（自动生成）
│   ├── account.json           # v11b 中线账户
│   ├── account_v13.json       # v13 中短线账户
│   └── trade_plan.json        # 上午信号 → 下午执行的计划
├── signals/            # 因子缓存
├── backtest_results/   # 回测结果（自动生成）
└── ml_models/          # ML 模型（已弃用）
```

---

## 三、数据管理

### 初始化数据

首次运行需要下载日 K 线数据（中证 800 成分股，约 3-5 分钟）：

```bash
python scripts/update_daily_data.py
```

### 日常更新

每天收盘后运行一次：

```bash
# 手动更新
python scripts/update_daily_data.py

# 或设 cron（每天 16:00 工作日）
0 16 * * 1-5 cd /path/to/project && python scripts/update_daily_data.py
```

### 数据质量检查

```bash
# 检查数据完整性
python scripts/fill_daily_gaps.py
```

---

## 四、回测引擎

### 基本用法

```bash
# 回测单个策略（默认 close 模式）
python scripts/run_backtest.py --strategy v11b_zz800_union

# 回测多个策略
python scripts/run_backtest.py --strategy v11b_zz800_union v10c_zz800_balanced v6b_hlr

# 回测全部策略
python scripts/run_backtest.py --strategy all

# 指定回测区间
python scripts/run_backtest.py --strategy v11b_zz800_union --start 2023-01-01 --end 2024-12-31
```

### 完整参数列表

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--strategy` | `all` | 策略名（或 `all` 跑全部）。可用：见下方策略列表 |
| `--start` | `2021-01-01` | 回测起始日期 (YYYY-MM-DD) |
| `--end` | 今天 | 回测结束日期 (YYYY-MM-DD) |
| `--top-n` | (策略预设) | 覆盖：持仓数量 |
| `--rebalance-freq` | (策略预设) | 覆盖：调仓频率（交易日） |
| `--stop-loss` | (策略预设) | 覆盖：止损比例 |
| `--max-position` | (策略预设) | 覆盖：单只最大仓位 |
| `--exec-timing` | `close` | 执行时序：`close`=收盘价(理想) / `open`=开盘价(接近实盘) |
| `--scan` | off | 启用参数网格扫描 |
| `--walk-forward` | off | 启用 Walk-Forward 过拟合检测 |
| `--ic-analysis` | off | 运行 IC 因子分析 |
| `--report-markdown` | off | 输出 Markdown 报告到 stdout |
| `--log` | off | 自动追加结果到 docs/RESULTS_LOG.md |
| `--output-dir` | (自动生成) | 结果输出目录（默认 `data/backtest_results/YYYYMMDD_HHMMSS/`） |

### 策略列表

| 策略名 | 模式 | 说明 | 状态 |
|--------|------|------|------|
| `v11b_zz800_union` | ensemble | 3组×4因子并集，中线 | ⭐ 全量最优 |
| `v13_small_mid_short` | factor | 反转+量价，中短线 | ✅ WF 通过 |
| `v10c_zz800_balanced` | factor | 13因子均衡加权 | WF 未通过 |
| `v6b_hlr` | factor | 9因子稳定基准 | 基准 |
| `v6b_8f_pos_ic` | factor | 8因子正IC等权 | 旧基准 |
| `v12_multi` | multi | 三策略并行 | ❌ 已证伪 |
| `v14_resid_mom` | factor | 残差动量 | ❌ 已证伪 |
| `v15_quality` | factor | 质量因子 | ❌ 已证伪 |
| `v16_mom_rev_hybrid` | factor | 动量+反转混合 | ❌ 已证伪 |

> 完整策略参数见 [STRATEGY_REGISTRY.md](STRATEGY_REGISTRY.md)

### Walk-Forward 验证

```bash
# WF 验证（16 folds，252天训练/63天测试）
python scripts/run_backtest.py --strategy v11b_zz800_union --walk-forward

# WF + 参数扫描
python scripts/run_backtest.py --strategy v11b_zz800_union --walk-forward --scan
```

### 参数扫描

```bash
# 扫描 top_n / rebalance_freq / stop_loss 的组合
python scripts/run_backtest.py --strategy v11b_zz800_union --scan

# 命令行覆盖部分参数后扫描
python scripts/run_backtest.py --strategy v11b_zz800_union --top-n 15 --scan
```

### 输出结果

回测结果保存在 `data/backtest_results/YYYYMMDD_HHMMSS/`：

```
data/backtest_results/20260606_210100/
├── summary.json          # 全部策略绩效指标
├── comparison.csv        # 策略对比表
├── nav_v11b_zz800_union.csv            # 净值曲线
├── trades_v11b_zz800_union.csv         # 交易记录
├── monthly_returns_v11b_zz800_union.csv # 月度收益透视表
├── param_scan.json       # 参数扫描结果（如有）
├── walk_forward.csv      # Walk-Forward 结果（如有）
└── report.md             # Markdown 报告
```

---

## 五、模拟盘

### v11b 中线策略（sim_daily_v7.py）

三阶段模式：上午信号 → 下午执行 → 收盘报告。

```bash
# 阶段 1：上午信号（12:00，上午收盘后 + 数据更新完成）
python scripts/sim_daily_v7.py intraday_signal

# 阶段 2：下午执行（13:00）
python scripts/sim_daily_v7.py intraday_execute

# 阶段 3：收盘报告（15:30）
python scripts/sim_daily_v7.py report_only
```

### v13 中短线策略（sim_v13.py）

```bash
# 阶段 1：上午信号（12:00）
python scripts/sim_v13.py intraday_signal

# 阶段 2：下午执行（13:00）
python scripts/sim_v13.py intraday_execute

# 阶段 3：收盘报告（15:30）
python scripts/sim_v13.py report_only
```

### Cron 配置

数据更新独立运行，信号/执行/报告不更新数据，直接用本地 CSV。

```bash
# 数据更新（上午收盘后 + 下午收盘后）
31 11 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/update_daily_data.py
1  15 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/update_daily_data.py

# v11b 中线策略（工作日）
0 12 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/sim_daily_v7.py intraday_signal
0 13 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/sim_daily_v7.py intraday_execute
30 15 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/sim_daily_v7.py report_only

# v13 中短线策略（工作日）
0 12 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/sim_v13.py intraday_signal
0 13 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/sim_v13.py intraday_execute
30 15 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/sim_v13.py report_only
```

---

## 六、配置参数

### 修改配置

所有可调参数统一在 `core/config.py` 顶部 `CONFIG` 字典中：

```python
CONFIG = dict(
    initial_capital   = 200000,    # 初始资金
    commission_rate   = 0.0003,    # 佣金率（万3）
    stamp_tax_rate    = 0.001,     # 印花税（千1，卖出收）
    slippage_rate     = 0.001,     # 滑点（千1）
    stop_loss         = 0.20,      # 止损比例
    top_n             = 12,        # 持仓数量
    rebalance_freq    = 20,        # 调仓频率（交易日）
    max_single_weight = 0.15,      # 单只最大仓位
    max_industry_weight = 0.25,    # 行业仓位上限
    vol_target        = 0.20,      # 目标年化波动率
)
```

修改 CONFIG 一处，所有使用默认值的策略自动生效。

### 策略级覆盖

在 `STRATEGY_PROFILES` 中定义策略时直接指定参数：

```python
PROFILE_MY_STRATEGY = StrategyConfig(
    label="my_strategy",
    top_n=15,                    # 覆盖 CONFIG 默认值
    stop_loss=0.15,
    max_industry_weight=0.30,
)
```

### 命令行覆盖（仅回测）

```bash
python scripts/run_backtest.py --strategy v11b_zz800_union \
    --top-n 15 --stop-loss 0.15 --rebalance-freq 10
```

### 完整配置参考

详见 [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md)

---

## 七、测试

```bash
# 运行全部测试（58 tests，< 1s）
python -m pytest tests/ -v

# 仅运行快速测试（排除 slow）
python -m pytest tests/ -v -k "not slow"

# 按模块运行
python -m pytest tests/test_sim_trading.py -v      # 39 个模拟盘测试
python -m pytest tests/test_ensemble.py -v          # 19 个 Ensemble 测试
python -m pytest tests/test_golden.py -v            # 12 个 Golden 测试
```

---

## 八、IC 因子分析

```bash
# 中证800 IC/IR 分析
python scripts/ic_analysis_zz800.py
```

输出各因子的 IC 均值、IC 标准差、IC_IR、正 IC 比例。

---

## 九、常用工作流

### 新策略开发流程

**标准策略（run_backtest.py）：**
```bash
# 1. 在 core/config.py 的 STRATEGY_PROFILES 中添加新策略
# 2. 全量回测
python scripts/run_backtest.py --strategy my_new_strategy --log

# 3. Walk-Forward 验证
python scripts/run_backtest.py --strategy my_new_strategy --walk-forward

# 4. 参数扫描（可选）
python scripts/run_backtest.py --strategy my_new_strategy --scan

# 5. 对比已有策略
python scripts/run_backtest.py --strategy v11b_zz800_union my_new_strategy v13_small_mid_short

# 6. 通过后切换模拟盘
```

**v13 评分排序策略（独立脚本）：**
```bash
# v13 有独立回测脚本，修改 V13Config 参数后直接跑
python scripts/v13_small_mid_short.py          # 全量回测
python scripts/v13_walk_forward.py             # WF 验证

# Bonus 因子扩展（不需要新建脚本）：
# 在 v13_small_mid_short.py 的 V13Config.bonus_factors 列表中添加：
# {'factor': 'my_factor', 'calc': lambda c,v,a,h,l: ..., 'condition': lambda v: v > 0, 'score': 0.3}
```

> ⚠️ **只有评分排序选股等独立策略才需要新建回测脚本。**
> 大多数因子策略只需在 config.py 注册 profile，用 run_backtest.py 即可。

### 日常运维

```bash
# 每天收盘后更新数据
python scripts/update_daily_data.py

# 检查回测结果
ls -lt data/backtest_results/ | head -5
cat data/backtest_results/最新目录/report.md

# 查看模拟盘状态
cat data/portfolio/account.json | python3 -m json.tool | head -30
cat data/portfolio/account_v13.json | python3 -m json.tool | head -30
```

### 问题排查

```bash
# 数据问题
python scripts/fill_daily_gaps.py

# 回测结果异常
# 检查 data/backtest_results/最新目录/summary.json
# 查看是否有 ⚠️ 警告

# 模拟盘执行失败
# 检查 data/portfolio/trade_plan.json 是否存在
# 检查 data/logs/ 下的日志
```

---

## 十、文档索引

| 文档 | 内容 |
|------|------|
| [README.md](../README.md) | 项目概览、快速开始 |
| [USER_MANUAL.md](USER_MANUAL.md) | 本文件 — 完整使用说明 |
| [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) | 配置参数详解 |
| [STRATEGY_REGISTRY.md](STRATEGY_REGISTRY.md) | 策略注册表（参数+绩效） |
| [STRATEGIES_DISCARDED.md](STRATEGIES_DISCARDED.md) | 已证伪策略详细记录 |
| [architecture.md](architecture.md) | 代码架构详解（面向开发者） |
| [DEPLOY.md](DEPLOY.md) | 部署指南（cron/环境配置） |
| [RESULTS_LOG.md](RESULTS_LOG.md) | 回测结果记录 |
| [HISTORY.md](HISTORY.md) | 已解决问题记录 |
| [BACKLOG.md](BACKLOG.md) | 待办事项 |
