# Cron 任务配置指南

> 最后更新：2026-06-19

本文档说明如何从零重建所有 cron 任务。

---

## 一、快速恢复（从备份）

如果已有 `jobs.json` 备份（位于 `hermes-memery/cron_jobs_backup.json`）：

```bash
# 恢复到 Hermes cron
cp hermes-memery/cron_jobs_backup.json ~/.hermes/cron/jobs.json
hermes cron list  # 验证
```

---

## 二、手动创建所有任务

### 2.1 创建命令模板

每个任务通过 `hermes cron create` 创建，需要指定：
- `--name`：任务名称
- `--schedule`：cron 表达式
- `--prompt`：任务 prompt

### 2.2 数据更新（上午）

```bash
hermes cron create \
  --name "数据更新-上午收盘" \
  --schedule "31 11 * * 1-5" \
  --prompt "执行上午数据更新。

运行命令：
cd /root/a-share-quant-sim && python3 scripts/tools/update_daily_data_async.py

整理为报告。（更新结果无需代码名称）

[CRON_STATUS] job_id=REPLACE_ME status=ok duration=0 ts=REPLACE_TS"
```

### 2.3 数据更新（下午）

```bash
hermes cron create \
  --name "数据更新-下午" \
  --schedule "40 14 * * 1-5" \
  --prompt "执行下午数据更新。

运行命令：
cd /root/a-share-quant-sim && python3 scripts/tools/update_daily_data_async.py

整理为报告。（更新结果无需代码名称）

[CRON_STATUS] job_id=REPLACE_ME status=ok duration=0 ts=REPLACE_TS"
```

### 2.4 账户2-上午信号

```bash
hermes cron create \
  --name "账户2-上午信号" \
  --schedule "45 11 * * 1-5" \
  --prompt "执行账户2（v27）上午信号。

运行命令：
cd /root/a-share-quant-sim && python3 scripts/sim/account_runner.py run --account-id 2 intraday_signal

整理为报告，包含：市场状态、现金、持仓数、🔴卖出明细（代码+名称+股数+价格+原因）、🟢买入明细（代码+名称+股数+价格+目标金额）、➡️持有明细（代码+名称+股数+价格）。

[CRON_STATUS] job_id=REPLACE_ME status=ok duration=0 ts=REPLACE_TS"
```

### 2.5 账户2-下午执行

```bash
hermes cron create \
  --name "账户2-下午执行" \
  --schedule "0 13 * * 1-5" \
  --prompt "执行账户2（v27）下午操作。

运行命令：
cd /root/a-share-quant-sim && python3 scripts/sim/account_runner.py run --account-id 2 intraday_execute

整理为报告，包含股票代码和名称（卖出/买入/持仓明细都要有代码+名称）。

[CRON_STATUS] job_id=REPLACE_ME status=ok duration=0 ts=REPLACE_TS"
```

### 2.6 收盘报告

```bash
hermes cron create \
  --name "收盘报告" \
  --schedule "30 15 * * 1-5" \
  --prompt "执行收盘报告（所有活跃账户）。

运行命令：
cd /root/a-share-quant-sim && python3 scripts/sim/account_runner.py run --account-id 1 report_only
cd /root/a-share-quant-sim && python3 scripts/sim/account_runner.py run --account-id 2 report_only

整理为报告，包含股票代码和名称（持仓明细要有代码+名称）。

[CRON_STATUS] job_id=REPLACE_ME status=ok duration=0 ts=REPLACE_TS"
```

### 2.7 Cron监控-巡检

```bash
hermes cron create \
  --name "Cron监控-巡检" \
  --schedule "*/10 11-15 * * 1-5" \
  --prompt "执行 cron 监控巡检。

运行命令：
cd /root/a-share-quant-sim && python3 scripts/cron_monitor.py

将脚本输出整理为报告。

[CRON_STATUS] job_id=REPLACE_ME status=ok duration=0 ts=REPLACE_TS"
```

### 2.8 Cron监控-心跳

```bash
hermes cron create \
  --name "Cron监控-心跳" \
  --schedule "0 16 * * 1-5" \
  --prompt "执行 cron 心跳报告。

运行命令：
cd /root/a-share-quant-sim && python3 scripts/cron_monitor.py --heartbeat

将脚本输出整理为报告。

[CRON_STATUS] job_id=REPLACE_ME status=ok duration=0 ts=REPLACE_TS"
```

---

## 三、Cron Prompt 设计原则

### 3.1 固定结构

```
执行<任务名>。

运行命令：
cd /root/a-share-quant-sim && python3 <脚本> <参数>

整理为报告，包含股票代码和名称。

[CRON_STATUS] job_id=<id> status=ok duration=0 ts=<时间>
```

### 3.2 为什么这样设计

- **旧 prompt 问题**：3-5 步操作（git pull + 跑脚本 + 读文件 + 整理报告），每步都触发 API 调用
- **429 限流**：OpenRouter Stealth provider 有严格速率限制，下午密集时段（14:40-15:30）容易打满
- **新方案**：只需 1 次 API 调用（跑命令 + 格式化输出），大幅降低 429 风险

### 3.3 CRON_STATUS 标记

每个 cron 必须在输出末尾追加一行状态标记：

```
[CRON_STATUS] job_id=<id> status=ok duration=<秒数> ts=<YYYY-MM-DD HH:MM:SS>
```

- `status=ok`：执行成功
- `status=error`：执行失败（需在标记前输出错误详情）
- `duration`：脚本执行耗时（整数秒）
- `ts`：当前时间

---

## 四、任务清单

| 任务 | 时间 | 命令 | 状态 |
|------|------|------|------|
| 数据更新-上午 | 11:31 工作日 | `update_daily_data_async.py` | ✅ 活跃 |
| 数据更新-下午 | 14:40 工作日 | `update_daily_data_async.py` | ✅ 活跃 |
| 账户1-上午信号 | 11:45 工作日 | `run --account-id 1 intraday_signal` | ⏸️ 暂停 |
| 账户1-下午执行 | 13:00 工作日 | `run --account-id 1 intraday_execute` | ⏸️ 暂停 |
| 账户2-上午信号 | 11:45 工作日 | `run --account-id 2 intraday_signal` | ✅ 活跃 |
| 账户2-下午执行 | 13:00 工作日 | `run --account-id 2 intraday_execute` | ✅ 活跃 |
| 收盘报告 | 15:30 工作日 | `run --account-id 1+2 report_only` | ✅ 活跃 |
| Cron监控-巡检 | */10 11-15 工作日 | `cron_monitor.py` | ✅ 活跃 |
| Cron监控-心跳 | 16:00 工作日 | `cron_monitor.py --heartbeat` | ✅ 活跃 |
| 每日记忆整理 | 08:00 每日 | hermes-memery 备份 | ⏸️ 暂停 |

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
