# 用户手册

> 最后更新：2026-07-15

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

### 1.1 一次性设置（每次新开终端都要执行）

```bash
export PYTHONPATH=/root/a-share-quant-sim
export BACKTEST_DATA_DIR=/root/data
```

验证：
```bash
echo $PYTHONPATH          # 应输出 /root/a-share-quant-sim
ls $BACKTEST_DATA_DIR/quant.db  # 应能看到 quant.db 文件
```

如果 `quant.db` 不存在，先初始化数据（见下一节）。

### 1.2 命令格式约定

本手册所有命令都假设上面两个环境变量已设置。如果报错 `ModuleNotFoundError` 或 `找不到数据`，先检查这两个变量。

---

## 二、数据管理

### 2.1 首次初始化（只需跑一次）

```bash
mkdir -p /root/data
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/update_daily_data_async.py
```

运行后输出：
```
正在更新日K线数据...
已更新 800 只股票，1120000 条记录
数据已写入 /root/data/quant.db
```

产物：`/root/data/quant.db`（140MB），包含中证 800 成分股 2020-01 至今的日 K 线。

### 2.2 日常更新（每天收盘后）

```bash
PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data \
  python scripts/tools/update_daily_data_async.py
```

数据直接 upsert 到数据库，不会重复。

### 2.3 查看数据库内容

```bash
# 查看账户
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/cli.py account

# 查看持仓
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/cli.py holdings

# 查看交易记录（最近 10 条）
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/cli.py trades --limit 10

# SQL 直连（高级用户）
sqlite3 /root/data/quant.db "SELECT * FROM account;"
sqlite3 /root/data/quant.db "SELECT COUNT(*) FROM daily_kline;"
sqlite3 /root/data/quant.db "SELECT * FROM holdings WHERE account_id=1;"
```

### 2.4 修改账户资金

```bash
sqlite3 /root/data/quant.db "UPDATE account SET initial_capital=500000 WHERE id=1;"
```

---

## 三、回测引擎

### 3.1 基本用法

```bash
# 跑单个策略
python scripts/backtest/run_backtest.py --strategy v27

# 跑所有策略
python scripts/backtest/run_backtest.py

# 指定回测区间
python scripts/backtest/run_backtest.py --strategy v27 --start 2023-01-01 --end 2025-12-31

# 用开盘价执行（更接近实盘）
python scripts/backtest/run_backtest.py --strategy v27 --exec-timing open
```

### 3.2 完整参数列表

| 参数 | 默认值 | 说明 | 示例 |
|------|--------|------|------|
| `--strategy` | `all` | 策略名，或 `all` 跑全部 | `--strategy v27` |
| `--start` | `2021-01-01` | 回测起始日期 | `--start 2023-01-01` |
| `--end` | 今天 | 回测结束日期 | `--end 2025-06-30` |
| `--exec-timing` | `close` | `close`=收盘价(理想) / `open`=开盘价(接近实盘) | `--exec-timing open` |
| `--walk-forward` | 关闭 | 启用 Walk-Forward 验证 | `--walk-forward` |
| `--log` | 关闭 | 自动追加结果到 RESULTS_LOG.md | `--log` |
| `--param` | 无 | 覆盖单个参数（可多次使用） | `--param top_n=15 rebalance_freq=10` |

### 3.3 有哪些策略？

```bash
# 查看帮助（列出所有可用策略）
python scripts/backtest/run_backtest.py --help
```

当前可用策略：

| 策略名 | 风格 | 全量年化 | 状态 |
|--------|------|---------|------|
| `v11b_zz800_union` | 多因子 Ensemble | ~30% | legacy 模式 |
| `v27` | 价量共振 | ~252% | 推荐，WF 最优 |
| `v20c` | 尾盘缩量 | ~59% | 推荐，WF 通过 |
| `v13_small_mid_short` | 小市值反转 | ~68% | 独立运行 |

### 3.4 输出在哪？

每次回测结果保存在 `data/backtest_results/` 下，按时间戳命名：

```bash
# 查看最新的回测结果目录
ls -lt /root/data/backtest_results/ | head -5

# 查看某个回测的绩效摘要
cat /root/data/backtest_results/20260715_120000/summary.json

# 查看回测报告（Markdown）
cat /root/data/backtest_results/20260715_120000/report.md
```

目录内容：
```
20260715_120000/
├── summary.json          # 全部策略绩效指标（JSON）
├── comparison.csv        # 策略对比表
├── nav_v27.csv           # 净值曲线（可用 Excel 打开画图）
├── trades_v27.csv        # 全部交易记录
├── monthly_returns_v27.csv  # 月度收益
├── walk_forward.csv      # WF 结果（如有 --walk-forward）
└── report.md             # Markdown 报告（人看的）
```

### 3.5 单次回测需要多久？

单策略全量回测（2020-2026，715 只股票）约 **50 秒**，内存占用约 **1GB**。

如果需要扫描参数范围（保存到 `data/backtest_results/` 的 summary.json）：
```bash
# 扫描 lookback 在 12~24 个月、threshold 在 0.8~1.2 之间的所有组合
python scripts/backtest/sweep_lookback_threshold.py
```

---

## 四、Walk-Forward 验证

### 4.1 什么是 Walk-Forward？

Walk-Forward（WF）是一种过拟合检测方法。把历史数据切成 N 段，用前一段训练参数，下一段验证，轮流滚动。

如果在样本外也能赚钱，说明策略不是过拟合。

### 4.2 运行 WF

```bash
PYTHONPATH=/root/a-share-quant-sim python scripts/backtest/v27_walk_forward.py
PYTHONPATH=/root/a-share-quant-sim python scripts/backtest/v20_walk_forward.py
```

### 4.3 怎么看结果

```bash
cat /root/data/backtest_results/wf_v27_latest.json
```

结果示例：
```json
{
  "n_folds": 15,
  "test_ann_return": "48.77%",
  "test_sharpe": "8.61",
  "test_max_dd": "-1.88%",
  "positive_folds": "15/15 (100%)",
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

---

## 五、模拟盘

### 5.1 运行模式

模拟盘分三步：**信号 → 执行 → 报告**，对应三个命令。每天按顺序跑。

### 5.2 账户1：v11b（independent 模式）

```bash
# 上午出信号（11:45 执行）
PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data \
  python scripts/sim/sim_account1.py intraday_signal

# 下午开盘执行（13:00 执行）
PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data \
  python scripts/sim/sim_account1.py intraday_execute

# 收盘报告（15:30 执行）
PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data \
  python scripts/sim/sim_account1.py report_only
```

### 5.3 账户2：v27（统一入口）

```bash
# 上午出信号
PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data \
  python scripts/sim/account_runner.py --strategy v27 intraday_signal

# 下午开盘执行
PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data \
  python scripts/sim/account_runner.py --strategy v27 intraday_execute

# 收盘报告
PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data \
  python scripts/sim/account_runner.py --strategy v27 report_only
```

### 5.4 账户3：v20c（尾盘模式）

```bash
# 尾盘出信号（14:45 执行）
PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data \
  python scripts/sim/account_runner.py --strategy v20c tail_signal

# 尾盘执行（14:55 执行）
PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data \
  python scripts/sim/account_runner.py --strategy v20c tail_execute

# 收盘报告（15:30 执行）
PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data \
  python scripts/sim/account_runner.py --strategy v20c report_only
```

### 5.5 执行后看结果

```bash
# 查看交易计划（执行后生成）
cat /root/data/portfolio/trade_plan_v27.json
cat /root/data/portfolio/trade_plan_v20c.json

# 查看运行日志
tail -50 /root/data/portfolio/sim_account1.log
tail -50 /root/data/portfolio/account_runner.log

# 查看账户状态
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/cli.py account
```

### 5.6 一个账户只用跑一次怎么办？

如果只想快速测试，不需要完整的三步流程，可以直接：
```bash
# 信号 + 执行一步完成
PYTHONPATH=/root/a-share-quant-sim python scripts/sim/account_runner.py --strategy v27 intraday_signal
PYTHONPATH=/root/a-share-quant-sim python scripts/sim/account_runner.py --strategy v27 intraday_execute
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

**第 3 步：回测验证**

```bash
# 先模拟盘试试能不能跑通
PYTHONPATH=/root/a-share-quant-sim python scripts/sim/account_runner.py \
  --strategy my_strategy intraday_signal

# 没问题再跑回测（需要在 run_backtest.py 或相关脚本中注册全量回测入口）
```

如果选股逻辑不依赖回测引擎，也可以先用独立脚本跑回测。参考 `scripts/strategies/v20_tail_pick.py` 的结构：`calc_tail_pick_factors()` + `select_stocks_tail_pick()` + 自己的回测引擎。

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

### 7.3 v20c 完整参数参考（strategy_map.py）

```python
# core/strategy_map.py → STRATEGY_MAP["v20c"]["params"]
STOP_LOSS      = -0.02    # 止损 -2%
TAKE_PROFIT   = 0.05     # 止盈 +5%
MAX_HOLDINGS   = 8        # 最大持仓 8 只
MAX_DAILY_BUY  = 4        # 每日最多买 4 只
MAX_POSITION   = 0.20     # 单只最大仓位 20%
HOLD_DAYS_MAX  = 5        # 最大持仓天数 5
HOLD_DAYS_MIN  = 1        # 最小持仓天数 1
REGIME_ENABLED   = True     # 市场状态识别开关
REGIME_MA_PERIOD = 20       # MA 周期
REGIME_SLOPE_DAYS = 5      # 斜率回看天数
REGIME_BULL_ALLOC = 1.0     # 牛市可用资金比例
REGIME_SIDEWAYS_ALLOC = 0.7 # 震荡市可用资金比例
REGIME_BEAR_ALLOC = 0.3     # 熊市可用资金比例
```

### 7.4 v27 完整参数参考（strategy_map.py）

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

### 8.1 使用系统 crontab

```bash
crontab -e
```

添加以下内容（根据你的交易时间调整）：

```cron
# 工作日 11:35 — 数据更新
35 11 * * 1-5 cd /root/a-share-quant-sim && PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data python scripts/tools/update_daily_data_async.py >> /root/data/portfolio/update.log 2>&1

# 工作日 11:45 — 上午信号（账户1 + 账户2）
45 11 * * 1-5 cd /root/a-share-quant-sim && PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data python scripts/sim/sim_account1.py intraday_signal >> /root/data/portfolio/sim_account1.log 2>&1
45 11 * * 1-5 cd /root/a-share-quant-sim && PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data python scripts/sim/account_runner.py --strategy v27 intraday_signal >> /root/data/portfolio/account_runner.log 2>&1

# 工作日 13:00 — 下午执行（账户1 + 账户2）
0 13 * * 1-5 cd /root/a-share-quant-sim && PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data python scripts/sim/sim_account1.py intraday_execute >> /root/data/portfolio/sim_account1.log 2>&1
0 13 * * 1-5 cd /root/a-share-quant-sim && PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data python scripts/sim/account_runner.py --strategy v27 intraday_execute >> /root/data/portfolio/account_runner.log 2>&1

# 工作日 14:45 — 尾盘信号（账户3）
45 14 * * 1-5 cd /root/a-share-quant-sim && PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data python scripts/sim/account_runner.py --strategy v20c tail_signal >> /root/data/portfolio/account_runner.log 2>&1

# 工作日 14:55 — 尾盘执行（账户3）
55 14 * * 1-5 cd /root/a-share-quant-sim && PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data python scripts/sim/account_runner.py --strategy v20c tail_execute >> /root/data/portfolio/account_runner.log 2>&1

# 工作日 15:30 — 收盘报告
30 15 * * 1-5 cd /root/a-share-quant-sim && PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data python scripts/sim/account_runner.py --strategy v27 report_only >> /root/data/portfolio/account_runner.log 2>&1
```

### 8.2 验证 crontab

```bash
crontab -l          # 查看当前 crontab
grep CRON /etc/log/syslog  # 查看 cron 日志（Ubuntu）
```

---

## 九、数据库操作（账户/持仓/买卖）

所有数据库操作通过 `scripts/tools/cli.py` 完成。**不需要写 SQL**，直接命令行操作。

先设置环境变量（只需一次）：
```bash
export PYTHONPATH=/root/a-share-quant-sim
```

然后所有命令都是 `python scripts/tools/cli.py <命令> [参数]` 的格式。

### 9.1 查看账户

```bash
python scripts/tools/cli.py account              # 查看账户1
python scripts/tools/cli.py account 2            # 查看账户2
python scripts/tools/cli.py account 3            # 查看账户3
```

输出：
```
=== 账户 2: v27 ===
  现金:     ¥100,384.20
  持仓市值: ¥12,456.80
  总资产:   ¥112,841.00
  初始资金: ¥100,000.00
  收益率:   +12.84%
  持仓数:   3 只
```

### 9.2 新建账户

```bash
# 新建账户4：名称 v28，资金 10 万，关联模拟盘脚本策略名 v28
python scripts/tools/cli.py new-account --id 4 --name v28 --cash 100000 --strategy v28

# 新建账户5：名称 test，资金 50 万
python scripts/tools/cli.py new-account --id 5 --name test --cash 500000
```

### 9.3 删除账户

```bash
# 删除账户（必须先清仓）
python scripts/tools/cli.py clear-holdings --account 4    # 先清仓
python scripts/tools/cli.py del-account --id 4           # 再删除
```

### 9.4 调整资金

```bash
# 把账户2的现金设为 5 万（直接覆盖，不增不减）
python scripts/tools/cli.py adjust --account 2 --cash 50000

# 把账户3的现金设为 20 万
python scripts/tools/cli.py adjust --account 3 --cash 200000
```

### 9.5 查看持仓

```bash
python scripts/tools/cli.py holdings              # 账户1持仓
python scripts/tools/cli.py holdings 2            # 账户2持仓
```

输出：
```
代码     名称       持仓    成本      现价      市值       盈亏
-----------------------------------------------------------------
600519   贵州茅台    100   1500.00   1680.00   ¥168,000   +12.00%
601318   中国平安    500     45.00     48.20   ¥24,100   +7.11%
```

### 9.6 手动加仓/减仓

```bash
# 给账户2加 100 股贵州茅台，成本价 1500
python scripts/tools/cli.py adjust --account 2 --add-stock 600519 100 1500

# 给账户2加 50 股中国平安，成本价 45
python scripts/tools/cli.py adjust --account 2 --add-stock 601318 50 45

# 清掉账户2的贵州茅台持仓
python scripts/tools/cli.py adjust --account 2 --del-stock 600519
```

### 9.7 全部清仓

```bash
python scripts/tools/cli.py clear-holdings --account 2
```

### 9.8 手动买卖

```bash
# 买入：账户1买入 100 股 600519，价格 1500
python scripts/tools/cli.py buy 600519 100 1500.0

# 买入到账户2
python scripts/tools/cli.py buy 600519 100 1500.0 2

# 卖出：账户1卖出 50 股 600519，价格 1600
python scripts/tools/cli.py sell 600519 50 1600.0

# 卖出（指定账户2 + 原因）
python scripts/tools/cli.py sell 600519 50 1600.0 2 "止盈"
```

### 9.9 查看交易记录

```bash
python scripts/tools/cli.py trades              # 账户1最近30条
python scripts/tools/cli.py trades 2            # 账户2最近30条
python scripts/tools/cli.py trades 1 50       # 账户1最近50条
```

### 9.10 查看股票行情

```bash
python scripts/tools/cli.py kline 600519         # 茅台最近20日K线
python scripts/tools/cli.py kline 601318 50     # 平安最近50日K线
```

### 9.11 数据库统计

```bash
python scripts/tools/cli.py stats
```

---

## 十、测试

```bash
# 快速测试（<1s，跳过慢的）
python -m pytest tests/ -v -k "not slow"

# 全部测试（约 10s）
python -m pytest tests/ -v

# 按模块跑
python -m pytest tests/test_golden.py -v      # 12 个 Golden 测试（核心逻辑）
python -m pytest tests/test_sim_trading.py -v  # 39 个模拟盘测试
python -m pytest tests/test_ensemble.py -v     # 19 个 Ensemble 测试
```

测试通过的标准输出：
```
========================= 70 passed in 2.34s =========================
```

如果有 FAILED，看失败信息排查。常见原因：数据库路径不对、PYTHONPATH 未设置。

---

## 十一、常见问题排查

### Q: ModuleNotFoundError: No module named 'scripts' 或 'core'

```bash
export PYTHONPATH=/root/a-share-quant-sim
```

### Q: 找不到 quant.db

```bash
# 初始化数据
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/update_daily_data_async.py
# 确认文件存在
ls -la /root/data/quant.db
```

### Q: 回测结果全是负数 / 和之前记录不一致

1. 检查选股池是否正确排除了科创板（688/689 前缀）：
```bash
sqlite3 /root/data/quant.db "SELECT COUNT(*) FROM stock_pool WHERE code LIKE '688%';"
# 应该返回 0
```

2. 检查数据是否最新：
```bash
sqlite3 /root/data/quant.db "SELECT MAX(trade_date) FROM daily_kline;"
```

3. 检查数据源差异：不同数据源的"前复权"算法不同，可能导致结果差异

### Q: 模拟盘没有交易（plan 为空）

1. 看日志：`tail -100 /root/data/portfolio/account_runner.log`
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
export PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data
python scripts/sim/account_runner.py --strategy my_strategy intraday_signal

# 4. 跑回测验证（需要写独立回测脚本或接入回测引擎）
# 5. 跑 Walk-Forward
# 6. 通过后接入 cron
```

### 场景 2：改个参数看效果

```bash
# 修改 v20c 的止盈从 15% 改为 10%
# 编辑 scripts/strategies/v20_tail_pick.py：
#   stop_profit = 0.15 → stop_profit = 0.10

# 跑回测
export PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data
python scripts/backtest/run_backtest.py --strategy v20c

# 对比结果
cat /root/data/backtest_results/$(ls -t /root/data/backtest_results/ | head -1)/summary.json
```

### 场景 3：日常运维

```bash
# 早上：更新数据
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/update_daily_data_async.py

# 查看账户状态
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/cli.py account

# 查看持仓
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/cli.py holdings

# 查看最近的回测记录
ls -lt /root/data/backtest_results/ | head -10

# 查看模拟盘日志
tail -20 /root/data/portfolio/account_runner.log
```
