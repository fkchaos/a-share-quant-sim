# 部署指南

> 最后更新：2026-07-15

零基础部署，5 分钟跑通。

---

## 1. 环境要求

- **Linux**（Ubuntu 20+ / CentOS 8+ / Debian 11+）
- **Python 3.10+**（`python3 --version` 检查）
- **网络**（需要访问 `qt.gtimg.cn` 腾讯行情接口，免费）
- **SQLite 3**（Python 内置，无需安装）
- **git**（`apt install git` 或 `yum install git`）

不需要 Docker、不需要 Agent、不需要任何付费服务。

---

## 2. 安装

```bash
# 克隆仓库（替换为你的路径）
git clone git@github.com:fkchaos/a-share-quant-sim.git
cd a-share-quant-sim

# 安装依赖（仅 3 个包）
pip install pandas numpy requests
```

验证：
```bash
python -c "import pandas, numpy, requests; print('OK')"
```

---

## 3. 初始化数据

首次运行需要下载中证 800 成分股的日 K 线数据（约 1 分钟）：

```bash
export PYTHONPATH=$(pwd)
export BACKTEST_DATA_DIR=/root/data
mkdir -p $BACKTEST_DATA_DIR

python scripts/tools/update_daily_data_async.py
```

数据存入 `/root/data/quant.db`（SQLite），包含：
- 800 只中证 800 成分股
- 2020-01 至今的日 K 线（112 万条）
- 3 个模拟账户（v11b/v27/v20c）

---

## 4. 跑回测

```bash
export PYTHONPATH=$(pwd)
export BACKTEST_DATA_DIR=/root/data

# 跑单个策略
python scripts/backtest/run_backtest.py --strategy v27

# 跑 Walk-Forward 验证（16 folds 样本外检测）
python scripts/backtest/run_backtest.py --strategy v27 --walk-forward

# 跑 v20c（尾盘策略）
python scripts/backtest/run_backtest.py --strategy v20c

# 跑 v11b（legacy）
python scripts/backtest/run_backtest.py --strategy v11b_zz800_union
```

输出在 `data/backtest_results/` 目录下，包含 summary.json、NAV 曲线、交易记录。

---

## 5. 跑模拟盘

模拟盘 = 信号生成 + 执行 + 报告，三步。

```bash
export PYTHONPATH=$(pwd)
export BACKTEST_DATA_DIR=/root/data

# 账户1（v11b legacy）
python scripts/sim/sim_account1.py intraday_signal   # 上午出信号
python scripts/sim/sim_account1.py intraday_execute  # 下午开盘执行
python scripts/sim/sim_account1.py report_only       # 收盘报告

# 账户2（v27 价量共振）
python scripts/sim/account_runner.py --strategy v27 intraday_signal
python scripts/sim/account_runner.py --strategy v27 intraday_execute
python scripts/sim/account_runner.py --strategy v27 report_only

# 账户3（v20c 尾盘缩量）
python scripts/sim/account_runner.py --strategy v20c tail_signal
python scripts/sim/account_runner.py --strategy v20c tail_execute
python scripts/sim/account_runner.py --strategy v20c report_only
```

---

## 6. 定时调度

### 使用系统 crontab（推荐）

```bash
crontab -e
```

添加以下内容（根据你的交易时间调整）：

```cron
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

### 使用 Hermes cron（如果你用 Hermes）

```bash
hermes cron list    # 查看状态
```

---

## 7. 数据目录结构

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

## 8. 策略选择

| 策略 | 风格 | 资金 | 特点 |
|------|------|------|------|
| v11b | 多因子 Ensemble | 20万 | 最保守，多组选股并集 |
| v27 | 价量共振 | 10万 | 动量最强，WF 夏普 8.66 |
| v20c | 尾盘缩量 | 10万 | 尾盘选股，次日开盘买 |

新手建议先用 v27 跑回测看效果。

---

## 9. 修改策略参数

策略参数统一在 `core/strategy_map.py` 的 `STRATEGY_MAP` 中管理，修改 `params` 字典即可：

| 策略 | 账户 | 关键参数（strategy_map.py 中的 params） |
|------|------|----------------------------------------|
| v11b | 账户1 | STOP_LOSS, TAKE_PROFIT, MAX_HOLDINGS, MAX_DAILY_BUY, MAX_POSITION, HOLD_DAYS_MAX |
| v27 | 账户2 | STOP_LOSS, TAKE_PROFIT, MAX_HOLDINGS, HOLD_DAYS_MAX, MOM_THRESHOLD |
| v20c | 账户3 | STOP_LOSS, TAKE_PROFIT, MAX_HOLDINGS, HOLD_DAYS_MAX |

改完后跑回测验证，再提交代码。旧脚本（`sim_account1/2/3.py`）保留作为备份，不再被 cron 调用。

---

## 10. 数据更新

日 K 线数据每个交易日更新。手动更新：

```bash
PYTHONPATH=/root/a-share-quant-sim BACKTEST_DATA_DIR=/root/data \
  python scripts/tools/update_daily_data_async.py
```

建议加到 crontab 每天 11:35 自动更新。

---

## 11. 常见问题

**Q: ModuleNotFoundError？**
```bash
# 确认 PYTHONPATH 设置正确
export PYTHONPATH=/root/a-share-quant-sim
```

**Q: 数据更新失败？**
腾讯接口偶尔不稳定，重试即可。检查网络：
```bash
curl -s "http://qt.gtimg.cn/q=sh600000" | iconv -f GBK -t UTF-8 | head -1
```

**Q: 回测结果跟预期不一样？**
检查选股池是否正确排除了科创板/北交所（688/689/8/4/2 前缀）。当前选股池 715 只。

**Q: 模拟盘初始资金对不上？**
初始资金在 DB `account` 表中：
```bash
sqlite3 /root/data/quant.db "SELECT * FROM account;"
sqlite3 /root/data/quant.db "UPDATE account SET initial_capital=200000 WHERE id=1;"
```

**Q: 如何只跑单个账户？**
```bash
# 只跑 v27
python scripts/sim/account_runner.py --strategy v27 intraday_signal
python scripts/sim/account_runner.py --strategy v27 intraday_execute
```

**Q: 如何添加新策略？**
1. 在 `scripts/strategies/` 下新建 `xxx_select.py`
2. 在 `core/strategy_map.py` 的 `STRATEGY_MAP` 中注册
3. 跑回测验证

---

## 12. 备份

```bash
# 数据库备份
cp /root/data/quant.db /root/data/quant.db.bak.$(date +%Y%m%d)

# 导出 SQL
sqlite3 /root/data/quant.db .dump > /root/data/quant_backup_$(date +%Y%m%d).sql
```

---

## 13. 系统要求

- 磁盘：数据库约 140MB，回测结果约 50MB
- 内存：回测需要 ~1GB（715 只 × 1560 天面板数据）
- CPU：无特殊要求，回测单策略约 50 秒
