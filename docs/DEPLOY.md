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
python scripts/update_daily_data.py
```

- 默认下载中证 800 成分股（~730 只），约 3-5 分钟
- 数据保存在 `data/daily/{code}.csv`
- 每天收盘后运行一次更新

## 验证安装

```bash
# 回测最优策略（close 模式，理想情况）
python scripts/run_backtest.py --strategy v11b_zz800_union

# 回测（open 模式，接近实盘）
python scripts/run_backtest.py --strategy v11b_zz800_union --exec-timing open

# Walk-Forward 过拟合检测
python scripts/run_backtest.py --strategy v11b_zz800_union --walk-forward
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

如果数据目录不在工程内，设 `BACKTEST_DATA_DIR`：

```bash
export BACKTEST_DATA_DIR=/path/to/data
# 或在 crontab 里：
35 11 * * 1-5 cd /path/to/project && BACKTEST_DATA_DIR=/path/to/data python scripts/sim_daily_v7.py intraday_signal
```

> 默认数据目录为工程内 `data/daily/`，无需额外配置。

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
├── ml_models/          # ML 模型（train_ml_model.py 生成）
│   ├── latest.json     # 最新模型元数据
│   ├── lgb_*.pkl       # LightGBM 模型
│   ├── xgb_*.pkl       # XGBoost 模型
│   ├── ridge_*.pkl     # Ridge 模型
│   └── scaler_*.pkl    # 标准化参数
└── logs/               # 运行日志
```

## ML 模型管理

### 策略模式切换

编辑 `data/portfolio/strategy_config.json`（或环境变量 `$DATA_DIR/strategy_config.json`）：

```json
{
  "mode": "ensemble",      // factor | ensemble
  "profile": "v11b_zz800_union"
}
```

- **factor**：纯因子加权（传统评分）
- **ensemble**：多组独立选股并集（v11b 当前使用，WF 验证最优）

修改后无需重启，下次 cron 自动生效。

### 手动训练

```bash
# 全量训练（约 60s）
python scripts/train_ml_model.py

# 输出到 /root/data/ml_models/
```

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
A: 数据区间和股票池不同会导致结果差异。当前基准用中证800（674只），2021-01 ~ 2026-06。详见 [docs/STRATEGY_REGISTRY.md](STRATEGY_REGISTRY.md)。

**Q: 模拟盘初始资金对不上？**
A: 初始资金在 `core/config.py` 的 `TradingCosts` dataclass 中定义（默认 200000）。如果已有 `data/portfolio/account.json`，删掉让它重新初始化。

**Q: cron 没执行？**
A: 检查 `crontab -l` 确认任务存在。查看 `data/logs/sim_daily_*.log` 找错误原因。

**Q: 策略参数改了但没生效？**
A: 策略参数在 `core/config.py` 的 `STRATEGY_PROFILES` 字典中定义。确认 `run_backtest.py` 和 `sim_daily_v7.py` 都通过 `StrategyEngine` 读取 `STRATEGY_PROFILES`。

## 回测记录规范

每次回测完成后，将结果追加到 `docs/RESULTS_LOG.md`：

- **Date 列必须精确到秒**：格式 `YYYY-MM-DD HH:MM:SS`（CST 时区）
- 用途：未来追溯时可通过 `git log --since="2026-06-01 14:17:00"` 精确定位回测前后的代码变更
- 之前无精确时间的条目标记为"时间不详"
