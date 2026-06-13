# 部署指南

> 最后更新：2026-07-13

---

## 系统要求

- Python 3.10+
- Linux
- 网络访问 `gtimg.cn`（腾讯行情接口，免费）
- SQLite 3（Python 内置）

---

## 安装

```bash
# 1. 克隆仓库
git clone git@github.com:fkchaos/a-share-quant-sim.git /root/a-share-quant-sim
cd /root/a-share-quant-sim

# 2. 安装依赖
pip install pandas numpy requests

# 3. 创建数据目录
mkdir -p /root/data/portfolio
```

依赖：`pandas`, `numpy, `requests`（无其他第三方库）

---

## 配置

### 环境变量

```bash
export BACKTEST_DATA_DIR=/root/data
export PYTHONPATH=/root/a-share-quant-sim
```

### 数据库

数据库文件：`/root/data/quant.db`（首次运行自动创建）

```bash
# 查看账户
PYTHONPATH=/root/a-share-quant-sim python scripts/tools/cli.py account

# 修改初始资金
sqlite3 /root/data/quant.db "UPDATE account SET initial_capital=200000 WHERE id=1;"
```

### 策略参数

- 策略参数：各选股模块的 Config 类（如 `scripts/strategies/v27_select.py` 中的 `V27Config`）
- 交易成本：`core/config.py` 中的 `TradingCosts` dataclass
- 风控参数：`core/config.py` 中的 `RiskLimits` dataclass

---

## 初始化数据

首次运行需要下载日 K 线数据（中证 800 成分股，约 1 分钟）：

```bash
PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data \
  python scripts/tools/update_daily_data_async.py
```

数据直接 upsert 到 `/root/data/quant.db`。

---

## 验证安装

```bash
cd /root/a-share-quant-sim
export PYTHONPATH=/root/a-share-quant-sim
export BACKTEST_DATA_DIR=/root/data

# 回测 v11b
python scripts/backtest/run_backtest.py --strategy v11b_zz800_union

# 快速测试
python -m pytest tests/ -v -k "not slow"
```

---

## Cron 调度（Hermes）

系统使用 Hermes cron 管理 7 个定时任务：

| 时间 | 任务 | 命令 |
|------|------|------|
| 11:45 工作日 | 账户1-上午信号 | `python scripts/sim/sim_account1.py intraday_signal` |
| 11:45 工作日 | 账户2-上午信号 | `python scripts/sim/account_runner.py --strategy v27 intraday_signal` |
| 13:00 工作日 | 账户1-下午执行 | `python scripts/sim/sim_account1.py intraday_execute` |
| 13:00 工作日 | 账户2-下午执行 | `python scripts/sim/account_runner.py --strategy v27 intraday_execute` |
| 14:45 工作日 | 账户3-尾盘信号 | `python scripts/sim/account_runner.py --strategy v20c tail_signal` |
| 14:55 工作日 | 账户3-尾盘执行 | `python scripts/sim/account_runner.py --strategy v20c tail_execute` |
| 15:30 工作日 | 收盘报告 | 三个账户 report_only |

所有命令需加环境变量：
```
PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data
```

### 查看 cron 状态

```bash
hermes cron list
```

---

## 数据目录结构

```
/root/data/
├── quant.db              # SQLite 数据库（主数据源）
│   ├── stock_pool        # 股票池（800只中证800）
│   ├── daily_kline       # 日K线（112万条，2020-01~2026-06）
│   ├── account           # 账户（3个）
│   │   ├── id=1: v11b, 20万
│   │   ├── id=2: v27, 10万
│   │   └── id=3: v20c, 10万
│   ├── holdings          # 持仓（按 account_id 区分）
│   ├── trade_log         # 交易记录
│   └── indicators        # 技术指标
└── portfolio/            # 交易计划 + 日志
    ├── trade_plan_v27.json
    ├── trade_plan_v20c.json
    ├── sim_account1.log
    └── account_runner.log
```

---

## 常见问题

**Q: 数据更新失败？**
A: 腾讯接口偶尔不稳定，重试即可。检查网络：`curl -s "http://qt.gtimg.cn/q=sh600000" | iconv -f GBK -t UTF-8 | head -1`

**Q: 回测结果跟预期不一样？**
A: 检查选股池是否正确排除了科创板/北交所（688/689/8/4/2）。当前选股池 715 只。

**Q: 模拟盘初始资金对不上？**
A: 初始资金在 DB `account` 表中。查看：`SELECT * FROM account;`

**Q: cron 没执行？**
A: `hermes cron list` 查看状态。检查 `/root/data/portfolio/` 下的日志。

**Q: ModuleNotFoundError？**
A: 确认设置了 `PYTHONPATH=/root/a-share-quant-sim`。

**Q: 策略参数改了但没生效？**
A: 策略参数在脚本中硬编码。修改后需重新跑回测验证，再提交代码。

---

## 回测记录规范

每次回测完成后，将结果追加到 `docs/RESULTS_LOG.md`：

- **Date 列必须精确到秒**：格式 `YYYY-MM-DD HH:MM:SS`（CST 时区）
- 用途：`git log --since="2026-07-13 12:00:00"` 精确定位代码变更

---

## 备份

### 数据库备份

```bash
# 手动备份
cp /root/data/quant.db /root/data/quant.db.bak.$(date +%Y%m%d)

# 导出 SQL
sqlite3 /root/data/quant.db .dump > /root/data/quant_backup_$(date +%Y%m%d).sql
```

### 记忆备份

Hermes 配置和 skill 备份在 `/root/hermes-memery/`（GitHub: fkchaos/hermes-memery）。
