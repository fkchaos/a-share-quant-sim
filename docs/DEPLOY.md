# 部署指南

## 系统要求

- Python 3.10+
- Linux / macOS（Windows 需改路径分隔符）
- 网络访问 `gtimg.cn`（腾讯行情接口，免费）

## 安装

```bash
git clone git@github.com:fkchaos/a-share-quant-sim.git
cd a-share-quant-sim
pip install -r requirements.txt
```

依赖：`pandas`, `numpy`, `requests`

## 配置

策略参数在 `core/config.py` 的 `STRATEGY_PROFILES` 字典中定义。
交易成本（初始资金/佣金/印花税/滑点）在 `core/config.py` 的 `TradingCosts` dataclass 中定义。

数据目录通过环境变量或代码配置：

```bash
# 数据目录（默认 data/daily/，也可设环境变量 BACKTEST_DATA_DIR）
export BACKTEST_DATA_DIR=/root/data
```

## 初始化数据

首次运行需要下载日 K 线数据：

```bash
python scripts/update_daily_data_async.py
```

- 默认下载中证 800 成分股（~800 只），约 1 分钟
- 数据直接 upsert 到 `/root/data/quant.db`（SQLite）
- 每天收盘后运行两次更新（11:31 上午 + 14:30 下午）

## 验证安装

```bash
# 回测 v11b 中线策略（close 模式，理想情况）
python scripts/run_backtest.py --strategy v11b_zz800_union

# 回测 v13 中短线策略
python scripts/run_backtest.py --strategy v13_small_mid_short

# 回测（open 模式，接近实盘）
python scripts/run_backtest.py --strategy v11b_zz800_union --exec-timing open

# Walk-Forward 过拟合检测
python scripts/run_backtest.py --strategy v13_small_mid_short --walk-forward

# 运行测试
python -m pytest tests/ -v -k "not slow"
```

## 模拟盘（三账户）

三个账户对应三个独立脚本，共享同一个数据库（`/root/data/quant.db`）：

| 账户 | 脚本 | 策略 | account_id | 调度 |
|------|------|------|------------|------|
| 账户1 | `sim_account1.py` | v11b 截面因子 | 1 | 11:45信号/13:00执行 |
| 账户2 | `sim_account2.py` | v13 小市值反转 | 2 | 11:45信号/13:00执行 |
| 账户3 | `sim_account3.py` | v20 尾盘缩量企稳 | 3 | 14:40信号/14:55执行 |

### Cron 调度（Hermes）

```
11:31  数据更新-上午（update_daily_data_async.py，直接 upsert DB）
11:45  账户1-上午信号（sim_account1.py intraday_signal）
11:45  账户2-上午信号（sim_account2.py intraday_signal）
13:00  账户1-下午执行（sim_account1.py intraday_execute）
13:00  账户2-下午执行（sim_account2.py intraday_execute）
14:30  数据更新-下午
14:40  账户3-尾盘信号（sim_account3.py tail_signal）
14:55  账户3-尾盘执行（sim_account3.py tail_execute）
15:30  收盘报告（三账户 report_only）
```

### 方式一：crontab

```cron
# 数据更新
31 11 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/update_daily_data_async.py
30 14 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/update_daily_data_async.py

# 账户1（v11b）
45 11 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/sim_account1.py intraday_signal
0 13 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/sim_account1.py intraday_execute

# 账户2（v13）
45 11 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/sim_account2.py intraday_signal
0 13 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/sim_account2.py intraday_execute

# 账户3（v20）
40 14 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/sim_account3.py tail_signal
55 14 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/sim_account3.py tail_execute

# 收盘报告
30 15 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/sim_account1.py report_only
30 15 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/sim_account2.py report_only
30 15 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/root/data python scripts/sim_account3.py report_only
```

## 数据目录结构

```
data/
├── daily/              # 日K线 CSV（可选备份）
│   ├── 000001.csv
│   └── ...
├── quant.db            # SQLite 数据库（主数据源）
│   ├── stock_pool      # 股票池（800只中证800）
│   ├── daily_kline     # 日K线（112万条）
│   ├── account         # 账户（3个）
│   ├── holdings        # 持仓
│   ├── trade_log       # 交易记录
│   └── indicators      # 技术指标
├── portfolio/          # 交易计划（自动生成）
│   ├── trade_plan_v13.json
│   └── trade_plan_v20.json
├── signals/            # 因子缓存
├── ml_models/          # ML 模型
└── logs/               # 运行日志
```

## ML 模型管理

### 策略模式切换

`sim_account1.py`（v11b）通过 `StrategyEngine` 运行，模式固定为 `ensemble`。

策略配置文件（`s_config.json`）兼容保留，但不影响三账户体系：
- 账户1（v11b）：ensemble 模式，3组因子并集
- 账户2（v13）：独立脚本，评分排序选股
- 账户3（v20）：独立脚本，尾盘缩量企稳

### 手动训练

```bash
# 全量 ML 训练（约 60s，生成 /root/data/ml_models/）
python scripts/train_ml_model.py

# CLI 操作 DB 数据
python scripts/cli.py account    # 查看账户
python scripts/cli.py holdings   # 查看持仓
python scripts/cli.py trades     # 查看交易记录

### 自动训练（Hermes cron）

每周一 06:00 自动训练，确保在开盘前完成：
- 拉取最新代码
- 加载 2021-01 ~ 最新全量数据
- 训练 LGB + XGB + Ridge 三模型
- 验证模型可加载
- 备份旧模型到 `ml_models/archive/`

## 常见问题

**Q: 数据更新失败？**
A: 腾讯接口偶尔不稳定，重试即可。检查网络是否能访问 `http://web.ifzq.gtimg.cn`。

**Q: 回测结果跟 README 里不一样？**
A: 数据区间和股票池不同会导致结果差异。当前基准用中证800（800只），2021-01 ~ 2026-06。详见 [docs/STRATEGY_REGISTRY.md](STRATEGY_REGISTRY.md)。

**Q: 模拟盘初始资金对不上？**
A: 初始资金在 DB `account` 表的 `initial_capital` 字段中定义。账户1=200000，账户2/3=100000。修改：`UPDATE account SET initial_capital=xxx WHERE id=1;`

**Q: cron 没执行？**
A: 检查 cron job 状态。查看 `data/logs/` 下的日志找错误原因。

**Q: 策略参数改了但没生效？**
A: 策略参数在脚本中硬编码（STOP_LOSS、TAKE_PROFIT 等）。账户参数（initial_capital、cash）从 DB 读取。

## 回测记录规范

每次回测完成后，将结果追加到 `docs/RESULTS_LOG.md`：

- **Date 列必须精确到秒**：格式 `YYYY-MM-DD HH:MM:SS`（CST 时区）
- 用途：未来追溯时可通过 `git log --since="2026-06-01 14:17:00"` 精确定位回测前后的代码变更
- 之前无精确时间的条目标记为"时间不详"
