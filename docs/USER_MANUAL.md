# 用户手册

> 最后更新：2026-06-25（重写第九章：适配 cmd.py 工具 + 新增 switch/status/signals 命令）

零基础也能看懂。每条命令都可以直接复制粘贴。

先看 [DEPLOY.md](DEPLOY.md) 完成安装，再读这本手册。

---

## 目录

- [一、环境准备](#一环境准备)
- [二、数据管理](#二数据管理)
- [三、回测引擎](#三回测引擎)
- [四、Walk-Forward 验证](#四walk-forward-验证)
- [五、模拟盘](#五模拟盘)
- [六、添加新策略](#六添加新策略)
- [七、参数配置](#七参数配置)
- [八、定时调度](#八定时调度)
- [九、数据库操作（账户/持仓/买卖）](#九数据库操作账户持仓买卖)
- [十、测试](#十测试)
- [十一、常见问题排查](#十一常见问题排查)
- [十二、完整工作流示例](#十二完整工作流示例)

---

## 一、环境准备

### 1.1 安装

```bash
cd a-share-quant-sim
pip install -e .
```

这一步会把 `core` 和 `scripts` 安装为可编辑包，之后所有脚本直接 `import core` 或 `from scripts.tools.xxx import ...` 即可，**不需要设置 `PYTHONPATH`**。

数据目录默认在项目内的 `data/`，**不需要设置 `BACKTEST_DATA_DIR`**（除非你想把数据放在别处）。

### 1.2 命令格式约定

本手册所有命令都假设你已经在项目根目录下。如果报错 `ModuleNotFoundError`，先确认是否执行了 `pip install -e .`。

---

## 二、数据管理

### 2.1 首次初始化（只需跑一次）

```bash
mkdir -p data

# 一键初始化（建表 + 股票池 + K线数据 + 账户，约 2-3 分钟）
python scripts/tools/init_project.py
```

产物：两个 SQLite 数据库：
- `data/quant_stocks.db` — 中证 800 成分股 + 股票K线 + 指数K线（上证/深证/创业板）
- `data/quant_accounts.db` — 3 个模拟账户 + 持仓 + 交易记录

> ⚠️ 不需要 CSV 文件，所有数据直接写入 SQLite。
>
> `init_project.py` 还支持 `--indices` 参数可单独更新指数K线，`--start-year` 指定起始年份。

### 2.2 日常更新（每天收盘后）

```bash
python scripts/tools/update_daily_data_async.py
```

数据直接 upsert 到数据库，不会重复。

### 2.3 查看数据库内容

```bash
# 查看账户
python scripts/tools/cli.py account

# 查看持仓
python scripts/tools/cli.py holdings

# 查看交易记录（最近 10 条）
python scripts/tools/cli.py trades --limit 10

```bash
# 查看账户
sqlite3 data/quant_accounts.db "SELECT * FROM account;"

# 查看持仓
sqlite3 data/quant_accounts.db "SELECT * FROM holdings WHERE account_id=1;"

# 查看 K线 条数
sqlite3 data/quant_stocks.db "SELECT COUNT(*) FROM daily_kline;"

# 修改账户资金
sqlite3 data/quant_accounts.db "UPDATE account SET initial_capital=500000 WHERE id=1;"
```

---

## 三、回测引擎

> **v27 回测入口**：`python scripts/backtest/wf_runner.py --strategy v27`
> 旧入口 `run_backtest.py` 已废弃（依赖已删除的 core/scoring.py），统一使用 wf_runner。

### 3.1 快速开始

```bash
# 全量回测（不做 WF 切分，直接跑全部历史数据）
python scripts/backtest/wf_runner.py --strategy v27 --full

# WF 回测（默认 4 folds，约 50 秒）
python scripts/backtest/wf_runner.py --strategy v27

# WF 回测（更多 folds）
python scripts/backtest/wf_runner.py --strategy v27 --step 63

# 指定回测区间
python scripts/backtest/wf_runner.py --strategy v27 --start 2023-01-01 --end 2025-12-31

# 参数扫描（调参用，很慢，日常回测不要用）
python scripts/backtest/sweep_v27_final.py          # 全组合扫描（48组，约40分钟）
python scripts/backtest/sweep_v27_mom_threshold.py  # 动量阈值扫描
python scripts/backtest/sweep_v27_sltp_hold.py      # 止损止盈持仓扫描
```

### 3.2 回测架构

```
wf_runner.py
├── strategy_adapter.py (统一选股+风控+市场状态)
│   └── v27: v27_select.py (价量共振)
└── core/account.py (buy/sell — 与模拟盘完全一致)
```

**关键设计**：回测和模拟盘使用**同一套交易逻辑**（`core/account.py` 的 `buy()`/`sell()`），确保结果一致。

### 3.3 wf_runner 参数列表

| 参数 | 默认值 | 说明 | 示例 |
|------|--------|------|------|
| `--strategy` | 必填 | 策略名（v27） | `--strategy v27` |
| `--train` | 252 | 训练期天数 | `--train 252` |
| `--test` | 252 | 测试期天数 | `--test 126` |
| `--step` | 252 | 滑动步长 | `--step 63` |
| `--start` | 2021-01-01 | 回测起始日期 | `--start 2023-01-01` |
| `--end` | 2026-05-31 | 回测结束日期 | `--end 2025-12-31` |

### 3.4 输出在哪？

每次回测结果保存在 `data/backtest_results/` 下：

```bash
# 查看最新的 WF 结果
cat data/backtest_results/wf_v27_latest.json

# 查看所有回测结果目录
ls -lt data/backtest_results/ | head -5
```

### 3.5 单次回测需要多久？

- v27 WF（4 folds, step=252）：约 **50 秒**
- v27 参数扫描（sweep_v27_final，48组）：约 **40 分钟**

### 3.6 模拟盘回测（account_runner.py）

直接跑模拟盘交易逻辑，验证策略在实盘数据上的表现：

```bash
# 三账户统一回测
python scripts/sim/account_runner.py --strategy all report_only

# 单账户回测
python scripts/sim/account_runner.py --strategy v27 report_only
python scripts/sim/account_runner.py --strategy v11b report_only
```

---

## 四、Walk-Forward 验证

### 4.1 什么是 Walk-Forward？

Walk-Forward（WF）是一种过拟合检测方法。把历史数据切成 N 段，用前一段训练参数，下一段验证，轮流滚动。

如果在样本外也能赚钱，说明策略不是过拟合。

### 4.2 运行 WF

```bash
# v27 价量共振（默认 step=126，5 folds）
python scripts/backtest/run_backtest.py --strategy v27

# v27 快速扫描（step=252，更少 fold）
python scripts/backtest/wf_runner.py --strategy v27 --step 252
```

### 4.3 怎么看结果

```bash
cat data/backtest_results/wf_v27_latest.json
```

结果示例：
```json
{
  "n_folds": 4,
  "test_ann_return": "121.31%",
  "test_sharpe": "4.16",
  "test_max_dd": "-8.16%",
  "positive_folds": "4/4 (100%)",
  "pass": true
}
```

### 4.4 判断标准

| 指标 | 通过阈值 | 含义 |
|------|---------|------|
| 正收益 fold 比例 | ≥ 60% | 至少 10/16 folds 正收益 |
| WF 平均 Sharpe | ≥ 0.5 | 样本外风险调整收益 |
| 最差 fold | > -30% | 不能有一个 fold 亏太多 |

全部满足 = **WF 通过**，策略可以上线模拟盘。

### 4.5 各策略 WF 结果参考（2026-06 数据）

| 策略 | 平均收益率 | 夏普 | 回撤 | 正收益fold | 状态 |
|------|-----------|------|------|-----------|------|
| v27 | 121.3% | 4.16 | -8.2% | 4/4 (100%) | ✅ WF通过 |
| v32 | — | — | — | — | 🔬 精简版运行中 |
| v33 | — | — | — | — | ⚠️ 双因子验证无效 |
| v35 | — | — | — | — | ⚠️ 相对 v27 无实质提升 |

---

## 五、模拟盘

### 5.1 运行模式

模拟盘分三步：**信号 → 执行 → 报告**，对应三个命令。每天按顺序跑。

### 5.2 账户1：v11b（⏸️ 已暂停，统一走 account_runner）

```bash
# 与账户2 共用同一入口，只需切换 --strategy
python scripts/sim/account_runner.py --strategy v11b intraday_signal
python scripts/sim/account_runner.py --strategy v11b intraday_execute
python scripts/sim/account_runner.py --strategy v11b report_only
```

### 5.3 账户2：v27（✅ 运行中，统一入口）

```bash
# 上午出信号

  python scripts/sim/account_runner.py --strategy v27 intraday_signal

# 下午开盘执行

  python scripts/sim/account_runner.py --strategy v27 intraday_execute

# 收盘报告

  python scripts/sim/account_runner.py --strategy v27 report_only
```

### 5.4 账户3：v20c（❌ 已退役，保留供参考）

```bash
# 尾盘出信号（14:45 执行）

  python scripts/sim/account_runner.py --strategy v20c tail_signal

# 尾盘执行（14:55 执行）

  python scripts/sim/account_runner.py --strategy v20c tail_execute

# 收盘报告（15:30 执行）

  python scripts/sim/account_runner.py --strategy v20c report_only
```

### 5.5 执行后看结果

```bash
# 查看交易计划（执行后生成）
cat data/portfolio/trade_plan_v27.json

# 查看运行日志
tail -50 data/portfolio/account_runner.log

# 查看账户状态
python scripts/tools/cli.py account
```

### 5.6 一个账户只用跑一次怎么办？

如果只想快速测试，不需要完整的三步流程，可以直接：
```bash
# 信号 + 执行一步完成
python scripts/sim/account_runner.py --strategy v27 intraday_signal
python scripts/sim/account_runner.py --strategy v27 intraday_execute
```

---

## 六、添加新策略

### 6.1 三步走

**第 1 步：写选股模块**

在 `scripts/strategies/` 下创建 `xxx_select.py`，实现两个函数：

```python
# scripts/strategies/my_strategy.py

def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel):
    """计算因子"""
    import pandas as pd
    factors = {}
    factors['my_factor'] = close_panel.pct_change(5)  # 示例：5日动量
    return factors

def select_stocks_my(factors, date, close_panel, volume_panel, amount_panel,
                     high_panel, low_panel, current_holdings=None):
    """选股：返回股票代码列表"""
    if date not in factors['my_factor'].index:
        return []

    # 获取当日因子
    f = factors['my_factor'].loc[date].dropna()
    # 排除当前持仓
    if current_holdings:
        f = f.drop(index=current_holdings.keys(), errors='ignore')
    # 取 top 8
    return f.nlargest(8).index.tolist()
```

**第 2 步：在 strategy_map.py 注册**

```python
# core/strategy_map.py
STRATEGY_MAP = {
    # ... 已有条目 ...
    "my_strategy": {
        "mode": "custom",
        "description": "我的策略",
        "account_id": 2,
        "timing": "intraday",
        "select_fn": "scripts.strategies.my_strategy.select_stocks_my",
        "calc_factors_fn": "scripts.strategies.my_strategy.calc_factors",
        "params": {
            "STOP_LOSS": -0.05,
            "TAKE_PROFIT": 0.15,
            "MAX_HOLDINGS": 8,
            "MAX_DAILY_BUY": 6,
            "MAX_POSITION": 0.25,
        },
    },
}
```

**第 3 步：在 strategy_adapter.py 注册（回测用）**

```python
# scripts/backtest/strategy_adapter.py 的 _register_builtin_strategies() 中
self._select_fns["my_strategy"] = self._my_strategy_select
self._risk_params["my_strategy"] = {
    "STOP_LOSS": -0.05, "TAKE_PROFIT": 0.15,
    "MAX_HOLDINGS": 8, "MAX_DAILY_BUY": 4, "MAX_POSITION": 0.25,
}

def _my_strategy_select(self, factors, date, close_panel, volume_panel, amount_panel,
                         high_panel, low_panel, open_panel, current_holdings, params):
    from scripts.strategies.my_strategy import calc_factors, select_stocks_my
    if factors is None or "my_factor" not in factors:
        factors = calc_factors(close_panel, volume_panel, amount_panel,
                               high_panel, low_panel, open_panel, params)
    merged_params = dict(self._risk_params["my_strategy"])
    if params:
        merged_params.update(params)
    return select_stocks_my(factors, date, close_panel, volume_panel, amount_panel,
                            high_panel, low_panel, open_panel, current_holdings, merged_params)
```

**第 4 步：回测验证**

```bash
python scripts/backtest/run_backtest.py --strategy my_strategy
```

### 6.2 常见踩坑

1. **Config 类属性 vs 实例属性**：如果选股函数从 `XxxConfig` 类读取参数，修改参数时直接改类属性 `XxxConfig.xxx = value`，不要创建实例再修改。
2. **factor 必须是 DataFrame**：索引是日期，列是股票代码。
3. **select_stocks 返回 list[str]**：股票代码字符串列表。
4. **`current_holdings` 参数**：是 dict `{code: {...}}` 或 None，选股时要排除。

---

## 七、参数配置

### 7.1 参数在哪改？

**策略参数统一在 `core/strategy_map.py` 的 `STRATEGY_MAP` 中管理。** 修改 `params` 字典即可，无需去各脚本里改。

| 策略 | 账户 | 参数位置 |
|------|------|---------|
| v11b | 账户1 | `strategy_map.py` → `STRATEGY_MAP["v11b"]["params"]` |
| v27 | 账户2 | `strategy_map.py` → `STRATEGY_MAP["v27"]["params"]` |
| v20c | 账户3 | `strategy_map.py` → `STRATEGY_MAP["v20c"]["params"]` |
| 回测通用 | — | `core/config.py` → `CONFIG` 字典（因子权重、交易成本等） |

### 7.2 常用参数说明

| 参数 | 含义 | 建议范围 | 改哪个文件 |
|------|------|---------|-----------|
| `stop_loss` | 止损线 | -0.01 ~ -0.10 | 各策略 Config |
| `stop_profit` | 止盈线 | 0.05 ~ 0.30 | 各策略 Config |
| `hold_days_max` | 最大持仓天数 | 2 ~ 8 | 各策略 Config |
| `max_holdings` | 最大同时持仓数 | 4 ~ 15 | 各策略 Config |
| `max_position` | 单只最大仓位 | 0.10 ~ 0.30 | 各策略 Config |
| `min_liquidity` | 最小日均成交额（万） | 100 ~ 1000 | 各策略 Config |
| `max_liquidity` | 最大日均成交额（万） | 5000 ~ 50000 | 各策略 Config |
| `initial_capital` | 初始资金（元） | 100000 ~ 1000000 | DB account 表 |
| `regime_enabled` | 市场状态识别开关 | True / False | strategy_map params |
| `regime_bull_alloc` | 牛市可用资金比例 | 0.8 ~ 1.0 | strategy_map params |
| `regime_sideways_alloc` | 震荡市可用资金比例 | 0.5 ~ 0.8 | strategy_map params |
| `regime_bear_alloc` | 熊市可用资金比例 | 0.1 ~ 0.5 | strategy_map params |

### 7.3 v27 完整参数参考（strategy_map.py）

```python
# core/strategy_map.py → STRATEGY_MAP["v27"]["params"]
STOP_LOSS        = -0.02    # 止损 -2%
TAKE_PROFIT     = 0.05     # 止盈 +5%
MAX_HOLDINGS     = 8        # 最大持仓 8 只
MAX_DAILY_BUY    = 4        # 每日最多买 4 只
MAX_POSITION     = 0.20     # 单只最大仓位 20%
HOLD_DAYS_MAX    = 5        # 最大持仓天数 5
HOLD_DAYS_MIN    = 1        # 最小持仓天数 1
HOLD_DAYS_EXTEND = 7        # 浮盈延长最大天数
HOLD_DAYS_EXTEND_PNL = 0.03 # 浮盈延长触发阈值 3%
MOM_THRESHOLD    = 0.02     # 动量阈值 2%
REGIME_ENABLED   = True     # 市场状态识别开关
REGIME_MA_PERIOD = 20       # MA 周期
REGIME_SLOPE_DAYS = 5      # 斜率回看天数
REGIME_BULL_ALLOC = 1.0     # 牛市可用资金比例
REGIME_SIDEWAYS_ALLOC = 0.7 # 震荡市可用资金比例
REGIME_BEAR_ALLOC = 0.3     # 熊市可用资金比例
```

---

## 八、定时调度

### 8.1 方案选择

| 方案 | 适用场景 | 优点 | 缺点 |
|------|---------|------|------|
| **Hermes cron**（推荐） | 已部署 Hermes Agent | 自动重试、失败告警、QQ 推送、集中管理 | 依赖 Hermes 服务 |
| **系统 crontab** | 纯 Linux 环境 | 零依赖、稳定 | 无告警、无重试、需手动查日志 |

### 8.2 Hermes cron 方案（推荐）

所有 cron 任务通过 `hermes cron` 管理，每个任务只需一条命令 + 格式化输出：

```bash
# 查看所有 cron 任务
hermes cron list

# 手动触发某个任务（测试用）
hermes cron run <job_id>

# 暂停/恢复
hermes cron pause <job_id>
hermes cron resume <job_id>
```

**当前任务清单（已启用）：**

| 任务 | 时间 | 命令 | 备注 |
|------|------|------|------|
| 数据更新-上午 | 11:31 工作日 | `run_and_send.py --task data_update` | 含上证指数更新 |
| 数据更新-下午 | 15:05 工作日 | `run_and_send.py --task data_update` | 含上证指数更新 |
| 账户2-上午信号 | 11:45 工作日 | `run_and_send.py --task signal --account 2` | v27 |
| 账户2-下午执行 | 13:00 工作日 | `run_and_send.py --task execute --account 2` | v27 |
| 收盘报告 | 15:30 工作日 | `run_and_send.py --task report --account 2` | |

**已暂停任务：** 账户1 信号/执行、账户3 尾盘信号/执行、Cron监控-巡检/心跳

**输出格式：** 所有任务通过 send_report.py 自动格式化并发送到 QQ，日期后带 📅（交易日）/ 🚫 非交易日 标识，信号含买卖持明细，执行含持仓明细

**Cron Prompt 设计原则：**
- 脚本做所有工作，agent 只负责格式化输出
- 固定结构：任务说明 → 运行命令 → 整理为报告（含代码+名称）→ CRON_STATUS 标记
- 极简 prompt 避免多轮 API 调用触发 429 限流

### 8.3 系统 crontab 方案（备选）

```bash
crontab -e
```

```cron
# ⚠️ 请将 /root/a-share-quant-sim 替换为你的实际项目路径
# 数据更新（上午+下午）
31 11 * * 1-5 cd /root/a-share-quant-sim && python3 scripts/tools/update_daily_data_async.py >> data/portfolio/update.log 2>&1
40 14 * * 1-5 cd /root/a-share-quant-sim && python3 scripts/tools/update_daily_data_async.py >> data/portfolio/update.log 2>&1

# 账户2 信号+执行
45 11 * * 1-5 cd /root/a-share-quant-sim && python3 scripts/sim/account_runner.py --strategy v27 intraday_signal >> data/portfolio/account_runner.log 2>&1
0 13 * * 1-5 cd /root/a-share-quant-sim && python3 scripts/sim/account_runner.py --strategy v27 intraday_execute >> data/portfolio/account_runner.log 2>&1

# 收盘报告
30 15 * * 1-5 cd /root/a-share-quant-sim && python3 scripts/sim/account_runner.py --strategy v27 report_only >> data/portfolio/account_runner.log 2>&1
```

### 8.4 验证

```bash
# crontab 方案
crontab -l
grep CRON /var/log/syslog  # Ubuntu/Debian
grep CRON /var/log/cron    # CentOS

# Hermes cron 方案
hermes cron list
```

---

## 九、数据库操作（账户/持仓/买卖）
## 九、数据库操作（账户/持仓/买卖）

所有数据库操作通过项目根目录的 `cmd.py` 完成。**不需要写 SQL**，直接命令行操作。

默认操作 `main` 账户（当前唯一账户），其他账户用 `--account <name>` 指定。

### 9.1 全局状态一眼览

```bash
python cmd.py status                        # 看当前账户+现金+持仓+收益率+最新信号
python cmd.py status --account test         # 看指定账户
```

输出示例：
```
┌─────────────────────────────────────────────┐
│ 账户 2: main            策略:     v39i      │
├─────────────────────────────────────────────┤
│ 💰 现金     ¥    197,637.90                │
│ 📊 市值     ¥          0                   │
│ 📈 总资产   ¥    197,637.90                │
│ 📉 收益率           -1.18%                  │
│ 💰 盈亏             +0.00%                  │
└─────────────────────────────────────────────┘

📡 无交易信号文件
```

> 💡 **日常运维只需跑 `status`**：一眼看到现金、持仓、收益率和是否有待执行信号。

### 9.2 查看账户

```bash
python cmd.py account                   # 列出所有账户
python cmd.py account main              # 查看指定账户
python cmd.py account 2                 # 或用 id
```

### 9.3 查看持仓

```bash
python cmd.py holdings                  # main 账户持仓
python cmd.py holdings --account test   # 指定账户
```

输出包含：代码、名称、数量、成本、现价、市值、盈亏百分比。

### 9.4 手动买卖

```bash
# 买入 100 股茅台 @ 1500
python cmd.py buy --code 600519 --shares 100 --price 1500

# 指定账户买入
python cmd.py buy --code 600519 --shares 100 --price 1500 --account test

# 卖出 50 股 @ 1600，带原因
python cmd.py sell --code 600519 --shares 50 --price 1600 --reason 止盈
```

**风控**：
- 现金不足时拒绝买入（提示可用金额）
- 持仓不足时拒绝卖出（提示实际持有）
- 所有写操作需要 `[yes/no]` 确认

### 9.5 调整现金

```bash
# 把 main 账户现金设为 20 万（直接覆盖）
python cmd.py set-cash --amount 200000

# 指定账户
python cmd.py set-cash --amount 100000 --account test
```

### 9.6 切换策略

```bash
# 把 main 账户切换到 v44（下次信号生成时生效）
python cmd.py switch --strategy v44

# 切换到 v46（行业ETF轮动）
python cmd.py switch --strategy v46 --account test
```

> 策略名必须是 `strategies` 中注册的合法名称。切换后**不立即执行**，下次信号生成时自动用新策略。

### 9.7 查看策略列表

```bash
python cmd.py strategies
```

输出所有已注册策略，当前正在运行的标 ✅，并显示各账户绑定情况。

### 9.8 查看交易记录

```bash
python cmd.py trades                              # 最近30条
python cmd.py trades --limit 50                   # 最近50条
python cmd.py trades --code 600519                 # 只看某只股票
python cmd.py trades --action BUY                 # 只看买入记录
python cmd.py trades --date-from 2026-06-01       # 从某天开始
python cmd.py trades --account test --limit 10    # 指定账户
```

### 9.9 查看交易信号

```bash
python cmd.py signals                   # 最新交易计划
python cmd.py signals --account test    # 指定账户
```

输出买入/卖出/持有明细，标注是否是今日信号。

### 9.10 查看股票行情

```bash
python cmd.py kline 600519              # 茅台最近20日K线
python cmd.py kline 601318 50          # 平安最近50日K线
```

### 9.11 数据库统计

```bash
python cmd.py stats
```

### 9.12 命令速查

| 场景 | 命令 |
|------|------|
| 看全局状态 | `python cmd.py status` |
| 看持仓 | `python cmd.py holdings` |
| 买入 | `python cmd.py buy --code 600519 --shares 100 --price 1500` |
| 卖出 | `python cmd.py sell --code 600519 --shares 100 --price 1600` |
| 调现金 | `python cmd.py set-cash --amount 200000` |
| 切策略 | `python cmd.py switch --strategy v44` |
| 看信号 | `python cmd.py signals` |
| 看交易 | `python cmd.py trades` |
| 看K线 | `python cmd.py kline 600519` |

---

## 十、测试

```bash
# 快速测试（<1s，跳过慢的）
python -m pytest tests/standard/ -v -k "not slow"

# 全部测试（约 5s）
python -m pytest tests/standard/ -v

# 按模块跑
python -m pytest tests/standard/test_account.py -v      # 19 个账户逻辑测试
python -m pytest tests/standard/test_sim.py -v          # 18 个模拟盘测试
python -m pytest tests/standard/test_strategies.py -v   # 14 个策略因子测试
python -m pytest tests/standard/test_backtest.py -v     # 13 个回测引擎测试
python -m pytest tests/standard/test_integration.py -v  # 12 个集成测试
```

测试通过的标准输出：
```
========================= 69 passed in 0.21s =========================
```

如果有 FAILED，看失败信息排查。

---

## 十一、常见问题排查

### Q: ModuleNotFoundError: No module named 'scripts' 或 'core'

```bash

```

### Q: 找不到数据库文件

```bash
# 确认两个 DB 文件存在
ls -la data/quant_stocks.db data/quant_accounts.db

# 如果不存在，重新初始化
python scripts/tools/init_project.py --db-only
```

### Q: 回测结果全是负数 / 和之前记录不一致

1. 检查选股池是否正确排除了科创板（688/689 前缀）：
```bash
sqlite3 data/quant_stocks.db "SELECT COUNT(*) FROM stock_pool WHERE code LIKE '688%';"
# 应该返回 0
```

2. 检查数据是否最新：
```bash
sqlite3 data/quant_stocks.db "SELECT MAX(date) FROM daily_kline;"
```

3. 检查数据源差异：不同数据源的"前复权"算法不同，可能导致结果差异

### Q: 模拟盘没有交易（plan 为空）

1. 看日志：`tail -100 data/portfolio/account_runner.log`
2. 确认上午信号先跑了：`account_runner.py intraday_signal`
3. 确认市场是交易日（非节假日）

### Q: cron 没执行

```bash
# 查看 cron 日志
grep CRON /var/log/syslog | tail -20
# 或
journalctl -u cron -n 20
```

### Q: 编程修改参数后没生效

- `select_stocks_xxx` 读取 `XxxConfig` 类属性，不是实例属性
- 正确改法：`XxxConfig.param = value`
- 错误改法：`cfg = XxxConfig(); cfg.param = value`（不影响类属性）

### Q: MemoryError

回测需要约 1GB 内存（715 只 × 1560 天）。如果内存不足：
1. 缩短回测区间：`--start 2023-01-01`
2. 减少股票数量

---

## 十二、完整工作流示例

### 场景 1：新策略从零开发到上线

```bash
# 1. 写选股模块
cat > scripts/strategies/my_strategy.py << 'EOF'
def calc_factors(close_panel, volume_panel, amount_panel, high_panel, low_panel):
    factors = {}
    factors['mom_5'] = close_panel.pct_change(5)
    return factors

def select_stocks_my(factors, date, close_panel, volume_panel, amount_panel,
                     high_panel, low_panel, current_holdings=None):
    if date not in factors['mom_5'].index: return []
    f = factors['mom_5'].loc[date].dropna()
    if current_holdings:
        f = f.drop(index=current_holdings.keys(), errors='ignore')
    return f.nlargest(8).index.tolist()
EOF

# 2. 注册到 strategy_map.py（编辑 core/strategy_map.py）

# 3. 模拟盘试试
python scripts/sim/account_runner.py --strategy my_strategy intraday_signal

# 4. 跑回测验证（需要写独立回测脚本或接入回测引擎）
# 5. 跑 Walk-Forward
# 6. 通过后接入 cron
```

### 场景 2：改个参数看效果

```bash
# 修改 v27 的止盈从 5% 改为 8%
# 编辑 core/strategy_map.py → STRATEGY_MAP["v27"]["params"]["TAKE_PROFIT"] = 0.08

# 跑回测
python scripts/backtest/run_backtest.py --strategy v27

# 对比结果
cat data/backtest_results/$(ls -t data/backtest_results/ | head -1)/summary.json
```

### 场景 3：日常运维

```bash
# 早上：更新数据
python scripts/tools/update_daily_data_async.py

# 查看账户状态
python scripts/tools/cli.py account

# 查看持仓
python scripts/tools/cli.py holdings

# 查看最近的回测记录
ls -lt data/backtest_results/ | head -10

# 查看模拟盘日志
tail -20 data/portfolio/account_runner.log
```
