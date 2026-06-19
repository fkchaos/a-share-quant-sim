# 部署指南

> 最后更新：2026-06-18（pip install -e . 统一路径管理，告别 sys.path 和 PYTHONPATH）

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
# 克隆仓库
git clone git@github.com:fkchaos/a-share-quant-sim.git
cd a-share-quant-sim

# 安装（自动安装 pandas/numpy/requests 依赖）
pip install -e .
```

验证：
```bash
python -c "import core; import scripts.tools.constraints; print('OK')"
```

> `pip install -e .` 会把 `core/` 和 `scripts/` 安装为可编辑包，之后所有脚本直接 `import core` 或 `from scripts.xxx import ...` 即可，**不需要设置 `PYTHONPATH`**。数据目录默认在项目内的 `data/`，**不需要设置 `BACKTEST_DATA_DIR`**。

---

## 3. 初始化数据

首次运行需要一键初始化（建表 + 股票池 + K线数据 + 账户）：

```bash
# 完整初始化（约 2-3 分钟）
python scripts/tools/init_project.py
```

分步执行：
```bash
python scripts/tools/init_project.py --db-only      # 只建表
python scripts/tools/init_project.py --pool-only    # 只获取股票池
python scripts/tools/init_project.py --kline-only  # 只下载K线
python scripts/tools/init_project.py --accounts    # 只初始化账户
```

数据存入两个 SQLite 数据库：
- `data/quant_stocks.db` — 股票池 + K线 + 技术指标
- `data/quant_accounts.db` — 账户 + 持仓 + 交易记录
- 中证 800 成分股（约 800 只）
- 近 30 日日 K 线
- 3 个模拟账户（v11b 暂停/v27 运行中/v20c 已退役）

> ⚠️ 不需要 CSV 文件，所有数据直接写入 SQLite。

---

## 4. 跑回测

> ✅ 所有策略（内置 + v27/v20c）都通过 `run_backtest.py` 统一入口。

```bash
# 跑内置策略（v4_baseline、ic_ir_weighted、markowitz 等）
python scripts/backtest/run_backtest.py --strategy v4_baseline

# 跑 v27 价量共振 — WF 回测
python scripts/backtest/run_backtest.py --strategy v27

# 跑 v20c 尾盘缩量 — WF 回测
python scripts/backtest/run_backtest.py --strategy v20c

# 指定回测区间
python scripts/backtest/run_backtest.py --strategy v27 --start 2023-01-01 --end 2025-12-31

# 跑模拟盘回测
python scripts/sim/account_runner.py --strategy all report_only
```

输出在 `data/backtest_results/` 目录下，包含 summary.json、NAV 曲线、交易记录。

> 旧独立 WF 脚本（v27_walk_forward.py、v20c_wf_sl_tp_scan.py 等）仍保留，但推荐使用统一入口。

---

## 5. 跑模拟盘

模拟盘 = 信号生成 + 执行 + 报告，三步。

```bash
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

### 方案一：Hermes cron（推荐）

所有任务通过 `hermes cron` 管理，自动重试、失败告警、QQ 推送。

**当前任务清单：**

| 任务 | 时间 | 命令 |
|------|------|------|
| 数据更新-上午 | 11:31 工作日 | `update_daily_data_async.py` |
| 数据更新-下午 | 14:40 工作日 | `update_daily_data_async.py` |
| 账户2-上午信号 | 11:45 工作日 | `--strategy v27 intraday_signal` | ✅ |
| 账户2-下午执行 | 13:00 工作日 | `--strategy v27 intraday_execute` | ✅ |
| 账户1-上午信号 | 11:45 工作日 | `--strategy v11b intraday_signal` | ⏸️ 暂停 |
| 账户1-下午执行 | 13:00 工作日 | `--strategy v11b intraday_execute` | ⏸️ 暂停 |
| 收盘报告 | 15:30 工作日 | `--strategy all report_only` |
| Cron监控-巡检 | */10 11-15 工作日 | `cron_monitor.py` |
| Cron监控-心跳 | 16:00 工作日 | `cron_monitor.py --heartbeat` |

```bash
hermes cron list          # 查看所有任务
hermes cron run <job_id>  # 手动触发
hermes cron pause <job_id> # 暂停
```

### 方案二：系统 crontab（备选）

```bash
crontab -e
```

```cron
# ⚠️ 请将 /root/a-share-quant-sim 替换为你的实际项目路径
# 数据更新
31 11 * * 1-5 cd /root/a-share-quant-sim && python3 scripts/tools/update_daily_data_async.py >> data/portfolio/update.log 2>&1
40 14 * * 1-5 cd /root/a-share-quant-sim && python3 scripts/tools/update_daily_data_async.py >> data/portfolio/update.log 2>&1

# 账户2
45 11 * * 1-5 cd /root/a-share-quant-sim && python3 scripts/sim/account_runner.py --strategy v27 intraday_signal >> data/portfolio/account_runner.log 2>&1
0 13 * * 1-5 cd /root/a-share-quant-sim && python3 scripts/sim/account_runner.py --strategy v27 intraday_execute >> data/portfolio/account_runner.log 2>&1

# 账户3
45 14 * * 1-5 cd /root/a-share-quant-sim && python3 scripts/sim/account_runner.py --strategy v20c tail_signal >> data/portfolio/account_runner.log 2>&1
55 14 * * 1-5 cd /root/a-share-quant-sim && python3 scripts/sim/account_runner.py --strategy v20c tail_execute >> data/portfolio/account_runner.log 2>&1

# 收盘报告（三账户）
30 15 * * 1-5 cd /root/a-share-quant-sim && python3 scripts/sim/account_runner.py --strategy all report_only >> data/portfolio/account_runner.log 2>&1
```

---

```
data/
├── quant_stocks.db       # 股票数据（K线、股票池、技术指标）
├── quant_accounts.db     # 账户数据（持仓、交易记录）
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
| v11b | 账户1 | STOP_LOSS, TAKE_PROFIT, MAX_HOLDINGS, MAX_DAILY_BUY, MAX_POSITION, HOLD_DAYS_MAX | ⏸️ 暂停 |
| v27 | 账户2 | STOP_LOSS, TAKE_PROFIT, MAX_HOLDINGS, HOLD_DAYS_MAX, MOM_THRESHOLD, REGIME_* | ✅ 运行中 |

改完后跑回测验证，再提交代码。旧脚本（`sim_account1/2/3.py`）保留作为备份，不再被 cron 调用。

---

## 10. 数据更新

日 K 线数据每个交易日更新。手动更新：

```bash
python scripts/tools/update_daily_data_async.py
```

建议加到 crontab 每天 11:35 自动更新。

---

## 11. 常见问题

**Q: ModuleNotFoundError？**
```bash
# 确认已执行 pip install -e .
pip install -e .
# 验证
python -c "import core; print('OK')"
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
sqlite3 data/quant_accounts.db "SELECT * FROM account;"
sqlite3 data/quant_accounts.db "UPDATE account SET initial_capital=200000 WHERE id=1;"
sqlite3 data/quant_stocks.db "SELECT COUNT(*) FROM daily_kline;"
```

**Q: 如何只跑单个账户？**
```bash
# 只跑 v27（v11b 暂停中，v20c 已退役）
python scripts/sim/account_runner.py --strategy v27 intraday_signal
python scripts/sim/account_runner.py --strategy v27 intraday_execute
```

**Q: 如何添加新策略？**
1. 在 `scripts/strategies/` 下新建 `xxx_select.py`
2. 在 `core/strategy_map.py` 的 `STRATEGY_MAP` 中注册
3. 写独立 WF 脚本（参考 `v20_walk_forward.py`）+ 跑回测验证
4. ⚠️ `run_backtest.py` 不支持自定义策略，必须写独立脚本

---

## 12. 备份

```bash
# 数据库备份
cp data/quant_stocks.db data/quant_stocks.db.bak.$(date +%Y%m%d)
cp data/quant_accounts.db data/quant_accounts.db.bak.$(date +%Y%m%d)

# 导出 SQL
sqlite3 data/quant_stocks.db .dump > data/quant_stocks_backup_$(date +%Y%m%d).sql
sqlite3 data/quant_accounts.db .dump > data/quant_accounts_backup_$(date +%Y%m%d).sql
```

---

## 13. 系统要求

- 磁盘：数据库约 140MB，回测结果约 50MB
- 内存：回测需要 ~1GB（715 只 × 1560 天面板数据）
- CPU：无特殊要求，回测单策略约 50 秒

---

## 14. ⚠️ 数据规范与已知问题（必读）

> 这些问题是实际踩坑总结，部署和维护时必须注意。

### 14.1 数据库字段单位（极其重要）

| 字段 | 单位 | 说明 |
|------|------|------|
| `close` / `open` / `high` / `low` | **元** | 股价，如 3.66 表示 3.66 元/股 |
| `volume` | **股** | 成交量，如 610370 表示 610,370 股 |
| `amount` | **元** | 成交额，如 2246161.6 表示约 224.6 万元 |

**验证方法**：`amount ≈ close × volume`（误差 <1%）。

**⚠️ 换数据源时必须重新确认单位**：
- 腾讯行情接口（`qt.gtimg.cn`）：amount 单位为**元**
- 部分数据源（如 akshare、东方财富）的 amount 单位可能是**万元**
- 如果 amount 单位是万元，需先 `× 10000` 转为元再写入 DB
- `V20Config.min_liquidity` 和 `max_liquidity` 阈值基于"元"设计，单位不一致会导致选股池错误

**历史教训**：早期 CSV 数据源 amount 单位为万元，迁移到 DB 时未统一单位，导致 v20c 流动性阈值比较错误（commit `45cd967` 修复）。

### 14.2 load_panel_from_db 面板顺序

`load_panel_from_db()` 返回的面板顺序为：

```
tpl[0] = close_panel
tpl[1] = volume_panel
tpl[2] = amount_panel
tpl[3] = open_panel    ← 注意：不是 high！
tpl[4] = high_panel    ← 注意：不是 low！
tpl[5] = low_panel     ← 注意：不是 open！
```

**⚠️ 历史 bug**：`v20_tail_pick.py` 的 `load_panel()` 曾把 `tpl[3]` 当 high、`tpl[4]` 当 low、`tpl[5]` 当 open（三者全错），导致 v20c 策略的 `daily_range` 因子计算错误。修复后 v20c 策略失效（WF 5/16，全量 -67%），旧版好结果完全源于错位假阳性。

**教训**：解包面板时务必对照 `load_panel_from_db` 的返回顺序，不能凭直觉。

### 14.3 v20c 策略状态：已失效

| 指标 | 数值 |
|------|------|
| WF 正收益 fold | 5/16 (31%) |
| 全量回测 | -67% |
| 核心因子 IC | -0.002（无预测能力） |

**原因**：面板顺序修复后，核心因子 `range_ratio`（振幅收窄）失去预测能力。旧版好结果完全源于面板错位假阳性。

**建议**：v20c 策略需重新设计因子或退役。当前模拟盘如仍在运行 v20c，建议停止。

### 14.4 v27 策略：正常

| 指标 | 数值 |
|------|------|
| WF 正收益 fold | 15/15 (100%) |
| 平均年化 | 25.92% |
| 夏普 | 5.96 |
| 回撤 | 3.29% |

v27 不依赖 high/low 面板，不受上述面板 bug 影响，结果可信。

### 14.5 数据源配置

| 数据 | 来源 | 接口 |
|------|------|------|
| 成分股 | `legulegu.com` 抓取 | 中证 800（约 800 只） |
| K线/指数 | 腾讯行情 `qt.gtimg.cn` | 免费，实时 |
| **已弃用** | akshare `index_stock_cons` | 只返回 688 只唯一值，不可靠 |

**⚠️ 换数据源注意事项**：
1. 确认 amount 单位（元 vs 万元）
2. 确认 volume 单位（股 vs 手，1手=100股）
3. 确认价格是否前复权
4. 确认停牌/退市股处理方式
5. 换源后必须重新跑 WF 验证

### 14.6 风控参数单位

| 参数 | 单位 | 示例 |
|------|------|------|
| `STOP_LOSS` | 比例（负数） | -0.05 = 止损 5% |
| `TAKE_PROFIT` | 比例（正数） | 0.15 = 止盈 15% |
| `MAX_POSITION` | 比例 | 0.30 = 单只最大 30% 仓位 |
| `HOLD_DAYS_MAX` | 天 | 2 = 最多持有 2 天 |
| `min_liquidity` / `max_liquidity` | **万元** | 注意：V20Config 里这两个阈值单位是万元，因为代码里做了 `/1e4` 转换 |

**注意**：`V20Config.min_liquidity` 和 `max_liquidity` 的单位是**万元**（代码中 `amount_panel.rolling(20).mean() / 1e4` 做了转换），而 DB 里 amount 单位是元。不要直接拿 DB 的 amount 值与阈值比较。
