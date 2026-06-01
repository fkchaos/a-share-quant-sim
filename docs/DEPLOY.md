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

依赖：`pandas`, `numpy`, `pyyaml`, `scipy`, `requests`

## 配置

编辑 `config.yaml`：

```yaml
# 初始资金（模拟盘用 200000）
costs:
  initial_capital: 200000

# 数据目录（默认 data/daily/，也可设环境变量 BACKTEST_DATA_DIR）
data:
  daily_dir: "data/daily"

# 回测区间
backtest:
  start_date: "2021-01-01"
  end_date: ""          # 空 = 到今天
```

## 初始化数据

首次运行需要下载日 K 线数据：

```bash
python scripts/update_daily_data.py
```

- 默认下载沪深 300 成分股（~280 只），约 2-3 分钟
- 数据保存在 `data/daily/{code}.csv`
- 每天收盘后运行一次更新

## 验证安装

```bash
# 回测最优策略（close 模式，理想情况）
python scripts/run_backtest.py --strategy v6b_8f_pos_ic

# 回测（open 模式，接近实盘）
python scripts/run_backtest.py --strategy v6b_8f_pos_ic --exec-timing open
```

## 模拟盘（盘中双阶段）

v7 脚本支持三阶段模式，需要配置 3 个定时任务：

| 时间 | 命令 | 说明 |
|------|------|------|
| 11:35 | `python scripts/sim_daily_v7.py intraday_signal` | 上午收盘出信号 |
| 13:00 | `python scripts/sim_daily_v7.py intraday_execute` | 下午开盘执行 |
| 15:30 | `python scripts/sim_daily_v7.py day_end` | 收盘报告 |

### 方式一：crontab

```bash
crontab -e
```

添加（替换 `/path/to/project` 为实际路径）：

```cron
# A股模拟盘 — 盘中双阶段
35 11 * * 1-5 cd /path/to/project && python scripts/sim_daily_v7.py intraday_signal
0  13 * * 1-5 cd /path/to/project && python scripts/sim_daily_v7.py intraday_execute
30 15 * * 1-5 cd /path/to/project && python scripts/sim_daily_v7.py day_end
```

### 方式二：Hermes cron

如果部署在 Hermes 环境，用 `cronjob` 工具创建 3 个 job，schedule 同上。

### 环境变量

如果数据目录不在项目下，设 `BACKTEST_DATA_DIR`：

```bash
export BACKTEST_DATA_DIR=/path/to/data
# 或在 crontab 里：
35 11 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/path/to/data python scripts/sim_daily_v7.py intraday_signal
```

## 数据目录结构

```
data/
├── daily/              # 日K线 CSV（update_daily_data.py 维护）
│   ├── 000001.csv
│   └── ...
├── portfolio/          # 账户状态（自动生成）
│   ├── account.json
│   └── trade_plan.json # 上午信号 → 下午执行的计划
├── signals/            # 因子缓存
└── logs/               # 运行日志
```

## 常见问题

**Q: 数据更新失败？**
A: 腾讯接口偶尔不稳定，重试即可。检查网络是否能访问 `http://web.ifzq.gtimg.cn`。

**Q: 回测结果跟 README 里不一样？**
A: 数据区间和股票池不同会导致结果差异。README 用的是 2021-01 ~ 2026-05，255~280 只股票。

**Q: 模拟盘初始资金对不上？**
A: 检查 `config.yaml` 的 `costs.initial_capital`。如果已有 `data/portfolio/account.json`，删掉让它重新初始化。

**Q: cron 没执行？**
A: 检查 `crontab -l` 确认任务存在。查看 `data/logs/sim_daily_*.log` 找错误原因。

**Q: 策略参数改了但没生效？**
A: 策略参数在 `config.yaml` 的 `strategies` 段。确认 `run_backtest.py` 和 `sim_daily_v7.py` 都从 `core.config.STRATEGY_PROFILES` 读取。

## 回测记录规范

每次回测完成后，将结果追加到 `docs/RESULTS_LOG.md`：

- **Date 列必须精确到秒**：格式 `YYYY-MM-DD HH:MM:SS`（CST 时区）
- 用途：未来追溯时可通过 `git log --since="2026-06-01 14:17:00"` 精确定位回测前后的代码变更
- 之前无精确时间的条目标记为"时间不详"
