# CLAUDE.md — A股量化模拟盘项目

> 本文件在每次会话开始时自动注入，必须遵守。

---

## 项目概况

- A股量化模拟盘，GitHub: fkchaos/a-share-quant-sim
- 账户2 = v68 (v67优化版, zz1800)，10万，运行中
- 账户1 = v61b，10万，运行中
- DB: data/quant_stocks.db + data/quant_accounts.db

---

## � 工作原则（主公明确要求，违反即错）

### 1. 先设计后实施
非 trivial 任务必须先写设计文档（`docs/experiments/YYYY-MM-DD_<topic>_design.md`），包含：背景、方案对比、实验步骤、回测条件。

### 2. 回测条件标准化（极其重要！）
所有 WF 对比必须在**完全相同**的条件下进行：
- train=252, test=126, step=63
- start=2021-01-01, end=2026-06-24
- pool=zz1800（除非特别说明）
- 标杆策略: v39g（全策略 Sharpe 1.297, 16 folds）

**禁止**在不同时间区间/不同 folds 数/不同 pool 的结果之间做比较。

### 3. IC 优先验证原则
新因子必须先做 IC/IR 分析：
- |IC Mean| > 0.03 且 |IR| > 0.3 → 有效，可进入 WF
- |IC Mean| < 0.01 或 |IR| < 0.1 → 证伪，不进入 WF
- 微弱信号（0.01-0.03）→ 不值得投入 WF 时间

### 4. 架构解耦与可扩展
- **任何修改都要考虑架构解耦和可扩展**
- 因子是纯计算单元，策略是组合层，两者解耦
- 新因子放 `core/strategy_map.py` 注册，不要硬编码
- 选股/风控/执行/组合各层独立

### 5. 搜索调研交叉进行
每个关键步骤前搜索最新资料（web_search / ddgr / firecrawl），不要闭门造车。外部获取的信息要存档到 `alpha-research/reports/`。

### 6. 文档同步更新
代码改动后**立即**同步以下文件（不等提醒）：
- `docs/TODO.md`
- `docs/strategy/RESULTS_LOG.md`
- `docs/strategy/STRATEGIES_DISCARDED.md`（证伪策略）
- `docs/experiments/YYYY-MM-DD_<topic>_results.md`

### 7. 长任务用 stream 模式
超过 30s 的任务必须用 `terminal(background=true)` + `process(action='poll')`，不要用 execute_code 或前台长命令。

### 8. 科学严谨 / 实验完整性
- 单个实验必须有完整记录（设计→IC→WF→结论）
- 不能因"感觉没希望"就跳过记录，失败实验同样有价值
- 每次实验的因子/参数/结果/教训都要落文档

---

## 🔴 已知陷阱（已踩坑，不要再踩）

1. **改参数必须同时改 strategy_map.py 和策略文件 DEFAULT_PARAMS**
2. **DB amount 单位是元，换数据源必须确认单位**
3. **load_panel_from_db 返回顺序**: [close, vol,amt, open, high, low]
4. **SQLite 多线程写入要每线程独立连接**
5. **SQLite WAL 模式**: executemany + 前后 COUNT 差值，不用 total_changes
6. **account.strategy 字段可能静默丢失**：每次手动 DB 操作后必须验证
7. **cron prompt 必须用 switch && run**，不依赖 DB 已有状态
8. **execute_code 在 cron 模式下被禁止**
9. **wf_runner.py 默认 test=252**，需要手动指定 test=126 step=63 才是标准 16 folds
10. **strategy_adapter.get_risk_params() 返回副本**，直接改 adapter._risk_params['v39g']
11. **⚠️ 腾讯K线API volume 单位是手（1手=100股），不是股！** 计算换手率需 `volume * 100 / float_shares`，否则偏差100倍

---

## 项目结构

```
core/           — 共享引擎（account, db, strategy_map, factors）
scripts/strategies/ — 每个策略一个文件（v39g_optimized.py, v58a_breakout.py, ...）
scripts/backtest/   — WF框架（wf_runner.py, strategy_adapter.py）
scripts/sim/        — 模拟盘执行（account_runner.py）
scripts/tools/      — 工具（format_daily_data.py, IC分析脚本）
docs/               — 正式文档
data/               — SQLite 数据库
alpha-research/     — 因子研究（独立目录，外部研究工具，不移植代码）
```

## 实验编号规则

- v39x: 基线策略系列
- v40-55x: 历史实验
- v58x: 外部策略验证（BigQuant/短线策略）
- v59x: Alpha158/Alpha191 新算子验证
- v60x: 中性化/优化方向
- 所有有价值的实验方向对应一个 todo 项

## 汇报风格

- 加 "主公，有事向您 直接给结论，不要绕弯子
- 涉及策略结论时同时给出数字和判断
