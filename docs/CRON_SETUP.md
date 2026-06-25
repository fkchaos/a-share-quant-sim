# Cron 任务配置指南

> 最后更新：2026-06-25（修复 venv Python 路径问题，策略 v27 → v39i，精简 cron prompt）

本文档说明如何从零重建所有 cron 任务。

---

## 一、基础环境

项目通过 `pip install -e .` 管理依赖，安装后 `python3` 直接可用：

```bash
cd a-share-quant-sim
pip install -e .
python3 scripts/tools/run_and_send.py --task signal --account 2
```

> **当前服务器特殊说明**：项目安装在 Hermes Agent 的 venv 内，系统 Python 没装依赖。
> 所有 cron 命令需写完整 venv 路径：
> ```bash
> /root/.hermes/hermes-agent/venv/bin/python3 scripts/tools/run_and_send.py ...
> ```
> 普通部署不需要这个 hack，`python3` 直接能用。

---

## 二、活动任务一览

### 已启用（5个）

| 任务 | 时间 | 命令 |
|------|------|------|
| 🟢 数据更新-上午 | 11:31 工作日 | `run_and_send.py --task data_update` |
| 🟢 数据更新-下午 | 15:05 工作日 | `run_and_send.py --task data_update` |
| 🟢 账户2-上午信号 | 11:45 工作日 | `run_and_send.py --task signal --account 2` |
| 🟢 账户2-下午执行 | 13:00 工作日 | `run_and_send.py --task execute --account 2` |
| 🟢 收盘报告 | 15:30 工作日 | `run_and_send.py --task report --account 2` |

### 已暂停（7个）

| 任务 | 原因 |
|------|------|
| ⏸️ 账户1-信号/执行 | v11b 暂停 |
| ⏸️ 账户3-尾盘信号/执行 | v20c 退役 |
| ⏸️ Cron监控-巡检/心跳 | 不再需要 |
| ⏸️ 每日记忆整理 | 暂停 |

---

## 三、手动创建/重建任务

### 3.1 数据更新（上午）

```bash
hermes cron create \
  --name "数据更新-上午收盘" \
  --schedule "31 11 * * 1-5" \
  --prompt "执行上午数据更新。

运行命令：
cd /root/a-share-quant-sim && python3 scripts/tools/run_and_send.py --task data_update"
```

> ⚠️ 如果部署在 Hermes Agent venv 内（当前服务器），将 `python3` 替换为 `/root/.hermes/hermes-agent/venv/bin/python3`。以下所有创建命令同理。

### 3.2 数据更新（下午）

```bash
hermes cron create \
  --name "数据更新-下午" \
  --schedule "5 15 * * 1-5" \
  --prompt "执行下午数据更新。

运行命令：
cd /root/a-share-quant-sim && python3 scripts/tools/run_and_send.py --task data_update"
```

### 3.3 账户2-上午信号

```bash
hermes cron create \
  --name "账户2-上午信号" \
  --schedule "45 11 * * 1-5" \
  --prompt "执行账户2上午信号（策略 v39i）。

直接执行一条命令：
cd /root/a-share-quant-sim && python3 scripts/tools/run_and_send.py --task signal --account 2"
```

### 3.4 账户2-下午执行

```bash
hermes cron create \
  --name "账户2-下午执行" \
  --schedule "0 13 * * 1-5" \
  --prompt "执行账户2下午交易（策略 v39i）。

直接执行一条命令：
cd /root/a-share-quant-sim && python3 scripts/tools/run_and_send.py --task execute --account 2"
```

### 3.5 收盘报告

```bash
hermes cron create \
  --name "收盘报告" \
  --schedule "30 15 * * 1-5" \
  --prompt "执行收盘报告（所有活跃账户）。

运行命令：
cd /root/a-share-quant-sim && python3 scripts/tools/run_and_send.py --task report --account 2"
```

---

## 四、Cron Prompt 设计原则

### 4.1 固定结构

所有 cron 使用**极简 prompt**，一行命令跑完：

```
执行<任务名>。

运行命令：
cd /root/a-share-quant-sim && /root/.hermes/hermes-agent/venv/bin/python3 scripts/tools/run_and_send.py --task <type> [--account <id>]
```

**不要再包含多步操作**（git pull、数据更新、信号生成分步执行等），`run_and_send.py` 一站式处理所有。

### 4.2 为什么这样设计

| 旧方案问题 | 新方案解决方案 |
|-----------|---------------|
| 3-5 步操作，每步触发 API 调用 | `run_and_send.py` 一站式执行 |
| OpenRouter 429 限流 | 一条命令，一次 API 调用 |
| 多步间状态可能不一致 | 单一进程处理 |

### 4.3 run_and_send.py 内部流程

```
run_and_send.py --task signal --account 2
  └─ account_runner.py run --account-id 2 intraday_signal [--date <today>]
     └─ 从 DB 读取绑定的策略 (v39i)
     └─ 加载数据 → 计算因子 → 选股 → 风控检查
     └─ 输出 JSON (买卖持明细)
  └─ send_report.py 格式化 → 发送 QQ
```

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
```
