# 用户手册

> A股量化模拟交易系统 — 完整使用说明
> 最后更新：2026-07-13

---

## 一、系统概览

### 架构

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
└──────────┬──────────┘
           │
           ▼
┌──────────────────────────────────────────────┐
│                  core/ (共享引擎)              │
│  config.py   ← STRATEGY_PROFILES + MarketFilter│
│  account.py  ← PortfolioState + buy/sell       │
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

### 三账户体系

| 账户 | ID | 策略 | 资金 | 脚本 | 调仓时间 |
|------|-----|------|------|------|---------|
| 账户1 | 1 | v11b (legacy) | 20万 | scripts/sim/sim_account1.py | 11:45信号/13:00执行 |
| 账户2 | 2 | v27 (价量共振) | 10万 | scripts/sim/account_runner.py --strategy v27 | 11:45信号/13:00执行 |
| 账户3 | 3 | v20c (尾盘缩量) | 10万 | scripts/sim/account_runner.py --strategy v20c | 14:45信号/14:55执行 |

### 策略模式

| 模式 | 说明 | 使用策略 |
|------|------|---------|
| `legacy` | 独立脚本，不走 account_runner | v11b |
| `custom` | 通过 strategy_map 注册，走 account_runner | v27, v20c |

新增策略只需：1) 在 `scripts/strategies/` 写选股模块 2) 在 `core/strategy_map.py` 注册一行

---

## 二、环境配置

### 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BACKTEST_DATA_DIR` | `/root/data` | 数据目录（quant.db + portfolio） |
| `PYTHONPATH` | 需设置 | 项目根目录 `/root/a-share-quant-sim` |

```bash
export BACKTEST_DATA_DIR=/root/data
export PYTHONPATH=/root/a-share-quant-sim
```

### 数据目录结构

```
/root/data/
├── quant.db              # SQLite 数据库（主数据源）
│   ├── stock_pool        # 股票池（800只中证800）
│   ├── daily_kline       # 日K线（112万条）
│   ├── account           # 账户（3个，id=1/2/3）
│   ├── holdings          # 持仓
│   ├── trade_log         # 交易记录
│   └── indicators        # 技术指标
└── portfolio/            # 交易计划 + 报告
    ├── trade_plan_v27.json
    ├── trade_plan_v20c.json
    └── ...
```

---

## 三、数据管理

### 初始化数据

首次运行需要下载日 K 线数据（中证 800 成分股，约 1 分钟）：

```bash
cd /root/a-share-quant-sim
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/update_daily_data_async.py
```

数据直接 upsert 到 `/root/data/quant.db`（SQLite），CSV 为可选备份。

### 日常更新

每天收盘后运行一次（由 cron 自动执行）：

```bash
PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data \
  python scripts/tools/update_daily_data_async.py
```

### CLI 操作数据库

```bash
# 查看账户
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/cli.py account

# 查看持仓
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/cli.py holdings

# 查看交易记录
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/cli.py trades

# 手动买入/卖出
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/cli.py buy --code 600519 --shares 100 --price 1500
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/cli.py sell --code 600519 --shares 100 --price 1600
```

---

## 四、回测引擎

### 基本用法

```bash
cd /root/a-share-quant-sim
export PYTHONPATH=/root/a-share-quant-sim
export BACKTEST_DATA_DIR=/root/data

# 回测单个策略
python scripts/backtest/run_backtest.py --strategy v11b_zz800_union

# 指定回测区间
python scripts/backtest/run_backtest.py --strategy v11b_zz800_union --start 2023-01-01 --end 2024-12-31

# Walk-Forward 验证
python scripts/backtest/run_backtest.py --strategy v11b_zz800_union --walk-forward
```

### 完整参数列表

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--strategy` | `all` | 策略名（或 `all` 跑全部） |
| `--start` | `2021-01-01` | 回测起始日期 |
| `--end` | 今天 | 回测结束日期 |
| `--exec-timing` | `close` | `close`=收盘价(理想) / `open`=开盘价(接近实盘) |
| `--walk-forward` | off | Walk-Forward 过拟合检测 |
| `--log` | off | 自动追加结果到 docs/RESULTS_LOG.md |

### 输出结果

回测结果保存在 `data/backtest_results/YYYYMMDD_HHMMSS/`：

```
data/backtest_results/20260713_120000/
├── summary.json          # 全部策略绩效指标
├── comparison.csv        # 策略对比表
├── nav_v11b.csv          # 净值曲线
├── trades_v11b.csv       # 交易记录
├── monthly_returns_v11b.csv  # 月度收益
├── walk_forward.csv      # WF 结果
└── report.md             # Markdown 报告
```

---

## 五、模拟盘

### 账户1：v11b 中线策略（legacy）

```bash
export PYTHONPATH=/root/a-share-quant-sim
export BACKTEST_DATA_DIR=/root/data

# 上午信号（11:45）
python scripts/sim/sim_account1.py intraday_signal

# 下午执行（13:00）
python scripts/sim/sim_account1.py intraday_execute

# 收盘报告（15:30）
python scripts/sim/sim_account1.py report_only
```

### 账户2：v27 价量共振（统一入口）

```bash
export PYTHONPATH=/root/a-share-quant-sim
export BACKTEST_DATA_DIR=/root/data

# 上午信号（11:45）
python scripts/sim/account_runner.py --strategy v27 intraday_signal

# 下午执行（13:00）
python scripts/sim/account_runner.py --strategy v27 intraday_execute

# 收盘报告（15:30）
python scripts/sim/account_runner.py --strategy v27 report_only
```

### 账户3：v20c 尾盘缩量（统一入口）

```bash
export PYTHONPATH=/root/a-share-quant-sim
export BACKTEST_DATA_DIR=/root/data

# 尾盘信号（14:45）
python scripts/sim/account_runner.py --strategy v20c tail_signal

# 尾盘执行（14:55）
python scripts/sim/account_runner.py --strategy v20c tail_execute

# 收盘报告（15:30）
python scripts/sim/account_runner.py --strategy v20c report_only
```

### 新增策略

1. 在 `scripts/strategies/` 创建选股模块，实现 `calc_factors()` + `select_stocks()`
2. 在 `core/strategy_map.py` 注册：
```python
"v27": {
    "mode": "custom",
    "select_fn": "scripts.strategies.v27_select.select_stocks_v27",
    "calc_factors_fn": "scripts.strategies.v27_select.calc_factors",
    "account_id": 2,
    "timing": "intraday",
}
```
3. 运行：`python scripts/sim/account_runner.py --strategy v27 intraday_signal`

---

## 六、配置参数

### 策略参数

策略参数在各选股模块的 Config 类中定义（如 `scripts/strategies/v27_select.py` 中的 `V27Config`）。

### 账户参数

账户资金在 DB `account` 表中：

```sql
-- 查看
SELECT id, name, cash, initial_capital, strategy FROM account;

-- 修改初始资金
UPDATE account SET initial_capital=200000 WHERE id=1;
```

### 交易成本

在 `core/config.py` 的 `TradingCosts` dataclass 中定义：

```python
@dataclass
class TradingCosts:
    commission_rate: float = 0.0003   # 佣金万3
    stamp_tax_rate: float = 0.001     # 印花税千1（卖出）
    slippage_rate: float = 0.001      # 滑点千1
```

---

## 七、测试

```bash
cd /root/a-share-quant-sim

# 快速测试（<1s）
python -m pytest tests/ -v -k "not slow"

# 全部测试
python -m pytest tests/ -v

# 按模块
python -m pytest tests/test_golden.py -v      # 12 个 Golden 测试
python -m pytest tests/test_sim_trading.py -v  # 39 个模拟盘测试
python -m pytest tests/test_ensemble.py -v     # 19 个 Ensemble 测试
```

---

## 八、Cron 调度

| 时间 | 命令 | 说明 |
|------|------|------|
| 11:45 | `python scripts/sim/sim_account1.py intraday_signal` | 账户1 v11b 上午信号 |
| 11:45 | `python scripts/sim/account_runner.py --strategy v27 intraday_signal` | 账户2 v27 上午信号 |
| 13:00 | `python scripts/sim/sim_account1.py intraday_execute` | 账户1 v11b 下午执行 |
| 13:00 | `python scripts/sim/account_runner.py --strategy v27 intraday_execute` | 账户2 v27 下午执行 |
| 14:45 | `python scripts/sim/account_runner.py --strategy v20c tail_signal` | 账户3 v20c 尾盘信号 |
| 14:55 | `python scripts/sim/account_runner.py --strategy v20c tail_execute` | 账户3 v20c 尾盘执行 |
| 15:30 | 三个账户 report_only | 收盘报告 |

所有 cron 命令需加 `PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data`。

---

## 九、常用工作流

### 新策略开发

```bash
# 1. 在 scripts/strategies/ 写选股模块
# 2. 在 core/strategy_map.py 注册
# 3. 回测验证
PYTHONPATH=/root/a-share-quant-sim python scripts/backtest/run_backtest.py --strategy v27 --log

# 4. Walk-Forward 验证
PYTHONPATH=/root/a-share-quant-sim python scripts/backtest/run_backtest.py --strategy v27 --walk-forward

# 5. 通过后接入模拟盘
PYTHONPATH=/root/a-share-quant-sim python scripts/sim/account_runner.py --strategy v27 intraday_signal
```

### 日常运维

```bash
# 查看账户状态
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/cli.py account

# 查看持仓
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/cli.py holdings

# 查看最新回测结果
ls -lt /root/data/backtest_results/ | head -5

# 查看运行日志
tail -100 /root/data/portfolio/sim_account1.log
```

### 问题排查

```bash
# 数据问题
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/fill_daily_gaps.py

# 回测结果异常
# 检查 data/backtest_results/最新目录/summary.json

# 模拟盘执行失败
# 检查 trade_plan_v27.json 是否存在
# 检查 /root/data/portfolio/ 下的日志
```

---

## 十、文档索引

| 文档 | 内容 |
|------|------|
| [README.md](../README.md) | 项目概览、架构图、快速开始 |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 完整架构文档（解耦后） |
| [USER_MANUAL.md](USER_MANUAL.md) | 本文件 — 完整使用说明 |
| [DEPLOY.md](DEPLOY.md) | 部署指南 |
| [CONFIG_REFERENCE.md](CONFIG_REFERENCE.md) | 配置参数详解 |
| [STRATEGY_REGISTRY.md](STRATEGY_REGISTRY.md) | 策略注册表（参数+绩效） |
| [STRATEGIES_DISCARDED.md](STRATEGIES_DISCARDED.md) | 已证伪策略记录 |
| [RESULTS_LOG.md](RESULTS_LOG.md) | 回测结果记录 |
| [BACKLOG.md](BACKLOG.md) | 待办事项 |
