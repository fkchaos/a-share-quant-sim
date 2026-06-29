# Cron 任务配置指南

> 最后更新：2026-06-29（区分 Agent / 非 Agent 执行路径，策略标注更新为 v39g）

本文档说明 cron 任务的两种执行路径，以及如何从零重建所有任务。

---

## 一、两种执行路径（Case by Case）

### 路径 A：非 Agent 用户（crontab 直接运行）

**适用场景**：用户从 GitHub clone 到本地，用系统 crontab 定时执行，**没有 Hermes Agent 环境**。

**执行入口**：`scripts/sim/account_runner.py` + `scripts/tools/format_report.py`（管道）

**报告输出**：格式化文本直接打印到终端 stdout，用户看终端即可。

**通知推送**：不需要额外推送（用户看终端输出）。

```bash
# crontab 示例（管道：执行 → 格式化 → 终端输出）
# 注意：每次执行前先 switch 绑定策略，防止 DB strategy 字段丢失导致失败
45 11 * * 1-5 cd /path/to/a-share-quant-sim && python3 scripts/sim/account_runner.py switch --account-id 2 --strategy v39g && python3 scripts/sim/account_runner.py run --account-id 2 intraday_signal 2>/dev/null | python3 scripts/tools/format_report.py --type signal --account 2
0 13 * * 1-5 cd /path/to/a-share-quant-sim && python3 scripts/sim/account_runner.py switch --account-id 2 --strategy v39g && python3 scripts/sim/account_runner.py run --account-id 2 intraday_execute 2>/dev/null | python3 scripts/tools/format_report.py --type execute --account 2
30 15 * * 1-5 cd /path/to/a-share-quant-sim && python3 scripts/sim/account_runner.py switch --account-id 2 --strategy v39g && python3 scripts/sim/account_runner.py run --account-id 2 report_only 2>/dev/null | python3 scripts/tools/format_report.py --type report --account 2
31 11 * * 1-5 cd /path/to/a-share-quant-sim && python3 scripts/tools/update_daily_data_async.py 2>/dev/null | python3 scripts/tools/format_report.py --type data_update
5 15 * * 1-5 cd /path/to/a-share-quant-sim && python3 scripts/tools/update_daily_data_async.py 2>/dev/null | python3 scripts/tools/format_report.py --type data_update
```

**流程**：
```
account_runner.py run --account-id 2 intraday_signal
  └─ 从 DB 读取绑定的策略 (v39g)
  └─ 加载数据 → 计算因子 → 选股 → 风控检查
  └─ 输出 JSON 到 stdout
  │
  ▼
format_report.py --type signal --account 2
  └─ 解析 JSON → 格式化报告文本 → 打印到 stdout（用户看终端）
```

### 路径 B：Agent 用户（Hermes cron）

**适用场景**：用户部署在服务器上，通过 Hermes Agent 的 cron 调度执行。

**执行入口**：cron job 直接调用 `account_runner.py`

**报告输出**：Agent 解析脚本输出，生成报告文本作为 final response。

**通知推送**：Hermes deliver 机制自动推送到 origin 对话（或配置的 channel）。

```bash
# Hermes cron 创建示例（见第三节）
```

**流程**：
```
Hermes cron → agent 执行 account_runner.py → agent 输出报告文本 → Hermes deliver 自动推送
```

### 对比

| | 路径 A（非 Agent） | 路径 B（Agent） |
|---|---|---|
| **调度器** | 系统 crontab | Hermes cron |
| **执行入口** | `run_and_send.py`（已废弃） | `account_runner.py` + `format_report.py` |
| **报告可见性** | 终端 stdout | Agent 推送通知 |
| **依赖** | 只需 Python + 项目依赖 | Hermes Agent 环境 |
| **通知方式** | 无需（看终端） | Hermes deliver |

---

## 二、当前活跃任务

### 路径 B（Hermes cron，当前服务器使用）

| 任务 | Job ID | 时间 | 策略 |
|------|--------|------|------|
| 🟢 数据更新-上午 | `8ebcb1e20cf1` | 11:31 工作日 | — |
| 🟢 数据更新-下午 | `b530aff8cbb4` | 15:05 工作日 | — |
| 🟢 账户2-上午信号 | `6ef77c65f34c` | 11:45 工作日 | v39g |
| 🟢 账户2-下午执行 | `b0ba5f428eb5` | 13:00 工作日 | v39g |
| 🟢 收盘报告 | `b6e0ef652f31` | 15:30 工作日 | — |

### 已暂停

| 任务 | 原因 |
|------|------|
| ⏸️ 账户1-信号/执行 | v11b 暂停 |
| ⏸️ 账户3-尾盘信号/执行 | v20c 退役 |
| ⏸️ Cron监控-巡检/心跳 | 不再需要 |
| ⏸️ 每日记忆整理 | 暂停 |

---

## 三、手动创建/重建任务

### 3.1 路径 B（Hermes cron）— Agent 用户

> ⚠️ 以下 prompt 由 Hermes Agent 执行。Agent 输出报告文本后，Hermes deliver 自动推送。

#### 数据更新（上午）

```bash
hermes cron create \
  --name "数据更新-上午收盘" \
  --schedule "31 11 * * 1-5" \
  --prompt "执行上午数据更新。

运行命令：
cd /root/a-share-quant-sim && /root/.hermes/hermes-agent/venv/bin/python3 scripts/tools/update_daily_data_async.py 2>&1

用 python 解析输出 JSON，报告必须包含：更新股票数、失败数、K线数、耗时。"
```

#### 数据更新（下午）

```bash
hermes cron create \
  --name "数据更新-下午" \
  --schedule "5 15 * * 1-5" \
  --prompt "执行下午数据更新。

运行命令：
cd /root/a-share-quant-sim && /root/.hermes/hermes-agent/venv/bin/python3 scripts/tools/update_daily_data_async.py 2>&1

用 python 解析输出 JSON，报告必须包含：更新股票数、失败数、K线数、耗时。"
```

#### 账户2-上午信号

```bash
hermes cron create \
  --name "账户2-上午信号" \
  --schedule "45 11 * * 1-5" \
  --prompt "执行账户2上午信号（策略 v39g）。

直接执行一条命令：
cd /root/a-share-quant-sim && /root/.hermes/hermes-agent/venv/bin/python3 scripts/sim/account_runner.py switch --account-id 2 --strategy v39g && /root/.hermes/hermes-agent/venv/bin/python3 scripts/sim/account_runner.py run --account-id 2 intraday_signal 2>/dev/null | /root/.hermes/hermes-agent/venv/bin/python3 scripts/tools/format_report.py --type signal --account 2

用 python 解析输出 JSON，报告中必须包含：
1. 信号摘要（现金/持仓数/买卖计划，含每只股的价格和数量）
2. 当日市场状态
3. 如果执行失败用一句话说明原因

注意：不要使用 hermes message 子命令，直接输出报告文本即可，Hermes 会自动推送。switch 确保策略绑定不会因 DB 状态丢失而失败。"
```

#### 账户2-下午执行

```bash
hermes cron create \
  --name "账户2-下午执行" \
  --schedule "0 13 * * 1-5" \
  --prompt "执行账户2下午交易（策略 v39g）。

直接执行一条命令：
cd /root/a-share-quant-sim && /root/.hermes/hermes-agent/venv/bin/python3 scripts/sim/account_runner.py switch --account-id 2 --strategy v39g && /root/.hermes/hermes-agent/venv/bin/python3 scripts/sim/account_runner.py run --account-id 2 intraday_execute 2>/dev/null | /root/.hermes/hermes-agent/venv/bin/python3 scripts/tools/format_report.py --type execute --account 2

用 python 解析输出，报告中必须包含：
1. 执行摘要（买入/卖出各多少只，含价格数量）
2. 执行后现金和持仓状态
3. 如果执行失败用一句话说明原因

注意：不要使用 hermes message 子命令，直接输出报告文本即可，Hermes 会自动推送。switch 确保策略绑定不会因 DB 状态丢失而失败。"
```

#### 收盘报告

```bash
hermes cron create \
  --name "收盘报告" \
  --schedule "30 15 * * 1-5" \
  --prompt "执行收盘报告（所有活跃账户）。

运行命令：
cd /root/a-share-quant-sim && /root/.hermes/hermes-agent/venv/bin/python3 scripts/sim/account_runner.py run --account-id 2 report_only 2>&1

用 python 解析输出，报告必须包含：现金、持仓明细（含市值和盈亏%）、总收益率。"
```

### 3.2 路径 A（系统 crontab）— 非 Agent 用户

> ⚠️ 以下命令直接由系统 crontab 执行，无 Agent 环境。报告输出到终端 stdout。
> 管道：`account_runner.py` 输出 JSON → `format_report.py` 格式化 → 终端输出。

```bash
# 数据更新（上午）
31 11 * * 1-5 cd /path/to/a-share-quant-sim && python3 scripts/tools/update_daily_data_async.py 2>/dev/null | python3 scripts/tools/format_report.py --type data_update

# 数据更新（下午）
5 15 * * 1-5 cd /path/to/a-share-quant-sim && python3 scripts/tools/update_daily_data_async.py 2>/dev/null | python3 scripts/tools/format_report.py --type data_update

# 账户2-上午信号
45 11 * * 1-5 cd /path/to/a-share-quant-sim && python3 scripts/sim/account_runner.py switch --account-id 2 --strategy v39g && python3 scripts/sim/account_runner.py run --account-id 2 intraday_signal 2>/dev/null | python3 scripts/tools/format_report.py --type signal --account 2

# 账户2-下午执行
0 13 * * 1-5 cd /path/to/a-share-quant-sim && python3 scripts/sim/account_runner.py switch --account-id 2 --strategy v39g && python3 scripts/sim/account_runner.py run --account-id 2 intraday_execute 2>/dev/null | python3 scripts/tools/format_report.py --type execute --account 2

# 收盘报告
30 15 * * 1-5 cd /path/to/a-share-quant-sim && python3 scripts/sim/account_runner.py switch --account-id 2 --strategy v39g && python3 scripts/sim/account_runner.py run --account-id 2 report_only 2>/dev/null | python3 scripts/tools/format_report.py --type report --account 2
```

> 注意：所有脚本依赖 `pip install -e .` 安装的项目依赖。普通部署 `python3` 直接可用，无需 venv 完整路径。

---

## 四、设计原则

### 4.1 为什么分两条路径

| 问题 | 说明 |
|------|------|
| 非 Agent 用户没有 Hermes deliver | 必须把报告打印到终端，用户直接看 stdout |
| Agent 用户看不到终端 | 必须通过 Hermes deliver 推送到对话 |
| 两条路径的脚本执行逻辑相同 | 都调用 `account_runner.py`，区别仅在通知方式 |

### 4.2 两个独立脚本各自的职责

| 脚本 | 职责 | 使用场景 |
|------|------|---------|
| `account_runner.py` | 执行交易逻辑（信号/执行/报告），输出 JSON | 路径 A + 路径 B 都用 |
| `format_report.py` | 解析 JSON → 格式化文本 → 输出到 stdout | 路径 A 管道到终端；路径 B 由 agent 读取后推送 |
| **send 部分** | 推送报告给用户 | 路径 A 用户看终端；路径 B agent 通过 Hermes deliver 自动推送 |

**关键：send 不在 Python 脚本中，由 cron 的 agent prompt 负责。**

### 4.3 策略标注

- 账户2当前绑定策略：**v39g**（非 v39i）
- 切换策略时只需改 DB `account.strategy` 字段，代码无需改动

---

## 五、验证

```bash
# 查看所有任务
hermes cron list

# 手动触发测试
hermes cron run <job_id>

# 查看输出
ls ~/.hermes/cron/output/<job_id>/
cat ~/.hermes/cron/output/<job_id>/*.md | tail -20

# 非 Agent 用户：直接跑 account_runner.py | format_report.py 看终端输出
python3 scripts/sim/account_runner.py switch --account-id 2 --strategy v39g && python3 scripts/sim/account_runner.py run --account-id 2 intraday_signal 2>/dev/null | python3 scripts/tools/format_report.py --type signal --account 2
```
