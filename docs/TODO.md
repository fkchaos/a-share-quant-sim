# TODO — 技术债务与重构计划

> 记录待办事项，避免丢失。完成一项删一项。

---

## ~~1. 路径硬编码问题~~ ✅ 2026-06-18

**现状：** ~~多处脚本硬编码 `/root`~~ → 已改用 `pip install -e .` editable 安装，所有脚本可直接 `import core` 和 `from scripts.xxx`，无需 `PYTHONPATH`。

**完成内容：**
- 新增 `pyproject.toml`，`core/` 和 `scripts/` 子包安装为 editable
- 清理 104 个脚本中的 `sys.path.insert`
- `PROJECT_ROOT` 环境变量 fallback 改为 `__file__` 相对路径
- 更新 README.md、USER_MANUAL.md、ARCHITECTURE.md

---

## ~~2. 数据库分离~~ ✅ 2026-06-18

**完成内容：**
- `core/db.py` 重构为双库架构：`quant_stocks.db`（股票数据）+ `quant_accounts.db`（账户数据）
- 所有现有函数签名保持不变，向后兼容
- 新增 `scripts/tools/migrate_db.py` 一键迁移旧库
- 更新 `sentiment_cycle.py` 和 `news_sentiment_factor.py` 的 DB 路径
- `*.db` 加入 `.gitignore`

**新库结构：**
```
data/
├── quant_stocks.db    # stock_pool, daily_kline, indicators, industry_map
└── quant_accounts.db  # account, holdings, trade_log
```

---

## ~~3. 回测框架重构~~ ✅ 2026-06-18

**完成内容：**
- `scripts/backtest/strategy_adapter.py` — 统一策略适配器，注册 v27/v20c，提供 select/risk_check/calc_regime 统一接口
- `scripts/backtest/wf_runner.py` — 通用 Walk-Forward 运行器，使用 core/account.py 的 buy/sell（与模拟盘一致）
- `core/db.py` 新增 `load_panel_from_db()` — 从 SQLite 加载面板数据（替代 core/data.py 的 CSV 加载）
- `scripts/backtest/run_backtest.py` — 新增策略路由：`--strategy v27/v20c` 走 wf_runner
- `scripts/sim/account_runner.py` — 风控/选股/市场状态迁移到 strategy_adapter，删除内联 check_risk 和 calc_regime_multiplier
- 验证：v27 WF 4/4 正收益，夏普 4.16，回撤 8.16%（与 v27_walk_forward.py 一致）
- 验证：account_runner.py 通过 adapter 正常运行 v27 信号

**架构：**
```
run_backtest.py --strategy v27/v20c → wf_runner.py → strategy_adapter.py
                                                    → core/account.py (buy/sell)
scripts/sim/account_runner.py → strategy_adapter.py (select/risk_check/calc_regime)
                               → core/account.py (buy/sell)
```

**待优化：**
- v20c 在 2021-2022 年选股几乎全为科创板（策略特性），WF 结果全零
- run_backtest.py 内置策略的 load_industry_map import 路径有已有 bug

---

*最后更新: 2026-06-18*

---

## 4. 数据校验 — 高优先级

**问题：** `update_daily_data_async.py` 下载后直接 upsert，没有校验数据完整性。如果腾讯接口返回异常数据（停牌股返回 0、字段错位等），会污染 DB。

**现状：** 只有 `init_project.py` 的 `step_init_kline` 有简单的 amount/volume 比例检查，`update_daily_data_async.py` 完全没有。

**方案：**
- 在 `update_daily_data_async.py` 的写入循环中增加校验：
  - close > 0、volume > 0
  - amount ≈ close × volume（误差 < 50%）
  - high >= low、high >= close、low <= close
- 异常数据跳过并打印告警（不写入 DB）
- 单只股票异常数据超过 N 条时，标记该股票为"数据异常"并跳过全天数据

---

## 5. Error Handling — 高优先级

**问题：** `run_signal` 和 `run_execute` 内部如果选股/风控抛异常，会导致整个 cron 任务失败。没有 try-except 包裹，异常直接上抛到 cron 层。

**方案：**
- 在 `run_signal` 和 `run_execute` 外层加 try-except
- 捕获异常后：
  - 打印错误信息到 stdout（cron 捕获）
  - 写入 `[CRON_STATUS] status=error` 标记（cron_monitor 能检测到）
  - 不抛出异常，让 cron 任务正常结束

---

## 6. 持仓清理 — 高优先级

**问题：** 如果某只股票退市/长期停牌，持仓会一直留在 holdings 表里。`get_holdings` 读取时不会过滤，可能影响后续风控判断。

**方案：**
- 在 `load_account` 时检查持仓股票的最新交易日
- 超过 N 天（如 30 天）无数据的持仓，自动清理并打印告警
- 或者在 `run_signal` 的风控阶段，检查持仓股票是否仍在 stock_pool 中（is_active=1）

---

## 7. 参数统一 — 中优先级

**问题：** 策略参数分散在两个地方：
- `core/config.py` 的 `STRATEGY_PROFILES`（旧策略 v4~v10）
- `core/strategy_map.py` 的 `STRATEGY_MAP`（新策略 v11b~v31）

**方案：**
- 统一到 `core/strategy_map.py` 的 `STRATEGY_MAP`
- `core/config.py` 只保留 `TradingCosts`、`MarketFilter`、`RiskLimits` 等基础配置
- 删除 `STRATEGY_PROFILES` 字典

---

## 8. 选股池统一 — 中优先级

**问题：** 回测和模拟盘的选股池可能不一致：
- 回测用 `load_panel_from_db`（有 MarketFilter，排除 688/689/8/4/2）
- 模拟盘用 `get_all_codes()` + 手动排除

**方案：**
- 统一选股池函数，回测和模拟盘共用
- 在 `core/db.py` 中新增 `get_tradeable_codes()`，返回排除科创板/北交所后的代码列表
- 所有选股入口调用此函数

---

## 9. 表现监控 — 中优先级

**问题：** 目前只有 cron 监控任务是否执行，但没有监控模拟盘表现是否偏离回测预期。

**方案：**
- 每周五收盘后，跑一次最近 20 个交易日的回测 vs 模拟盘 NAV 对比
- 偏差超过阈值（如 5%）时告警
- 新增 `scripts/tools/nav_compare.py` 工具脚本

---

*最后更新: 2026-06-20*
