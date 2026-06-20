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

## ~~4. 数据校验~~ ✅ 2026-06-20

**完成内容：**
- `update_daily_data_async.py` 写入循环增加校验：close > 0、volume > 0、amount ≈ close × volume（误差 < 50%）
- 异常数据跳过并打印告警，不写入 DB
- `init_project.py` 的 `step_init_kline` 已有 amount/volume 比例检查

---

## ~~5. Error Handling~~ ✅ 2026-06-20

**完成内容：**
- `run_signal` 和 `run_execute` 外层加 try-except
- 捕获异常后打印错误信息、写入 `[CRON_STATUS] status=error` 标记，不抛出异常

---

## ~~6. 持仓清理~~ ✅ 2026-06-20

**完成内容：**
- `load_account` 时检查持仓股票最新交易日，超过 30 天无数据自动清理并打印告警

---

## ~~8. 选股池统一~~ ✅ 2026-06-20

**完成内容：**
- `core/db.py` 新增 `get_tradeable_codes()`，返回排除科创板/北交所后的代码列表
- `scripts/tools/account_runner.py` 和 `run_backtest.py` 统一调用此函数

---

## ~~9. 表现监控~~ ✅ 2026-06-20

**完成内容：**
- 新增 `scripts/tools/nav_compare.py` 工具脚本，回测 vs 模拟盘 NAV 对比框架

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

# 新策略因子调研 — 2026-06-20

> 基于全面搜索调研（SearXNG 20+ 查询、Exa 5+ 查询、Jina Reader 6 篇精读、GitHub 5 仓库），以下为尚未尝试的新方向。

## 优先级排序

| 优先级 | 策略编号 | 方向 | 预期投入 | 数据需求 | 预期收益 | 风险 |
|--------|---------|------|---------|---------|---------|------|
| **P0** | **v32** | 分析师预期因子 | 1-2天 | Tushare/akshare 一致预期接口 | IC 2-6%，与量价低相关 | 低 |
| **P0** | **v33** | 残差动量 | 半天 | 现有行情数据 | ICIR 转正（+0.15） | 低 |
| **P1** | **v34** | 日内高频因子 | 需分钟线数据 | 分钟K线/Tick | IC 6-9% 极高 | 中（数据源） |
| **P1** | **v35** | 行业轮动叠加 | 1天 | 行业指数数据 | 年化超额 6-19% | 中 |
| **P1** | **v36** | 事件驱动 | 1天 | 业绩预告/事件数据库 | 与主策略低相关 | 中 |
| **P2** | **v37** | 深度学习因子 | 需 GPU | 大量历史数据 | 多头年化 23%+ | 高 |

## 详细调研

### [P0] v32 — 分析师预期因子

**来源：** 广发金工/华泰证券/新浪研报（2024-2025）

**核心因子：**
1. **SUE（标准化超预期盈余）**：(实际净利润 - 一致预期) / 标准差
   - 超预期 20% 时，后 10 日超额 1.8%（小市值 3.9%）
2. **分析师异常覆盖**：回归取残差剔除市值/换手率影响
   - 月度 RankIC = 2.34%，Top 组合年化超额 6.59%
3. **盈利预测上调**：近 3 个月分析师调高 EPS 比例
   - "预测上调超两家"组合年化 27.2%
4. **研报标题超预期**：NLP 情感分析
   - "标题超两家"组合年化 23.3%
5. **分析师综合因子**：异常覆盖 + 评级 + 盈利修正合成
   - 选股效果优于单因子

**与 v27 相关性：** 极低（量价 vs 基本面/情绪信号），预期增量显著

**实现方式：**
- 数据源：Tushare `stock_comment_consensus()` 或 akshare 一致预期接口
- 构建 3-5 个因子加入评分系统
- 作为独立评分维度，与 v27 量价因子加权融合

---

### [P0] v33 — 残差动量

**来源：** BigQuant 对 2015-2025 年 5,487 只股票全量实证

**核心逻辑：**
- 价格动量因子包含市场 Beta 和行业 Beta 带来的超额收益
- 剥离系统性风险后：`个股收益 = α + β_mkt × 市场 + β_ind × 行业 + ε`
- 取残差的 6 月累积值作为残差动量因子

**实证结果：**
- 原始动量 IC = -0.032（反转），ICIR = -0.244
- 残差动量 ICIR 反转为 **+0.15**
- 市场状态依赖：震荡市 ICIR = +0.45，牛市 = -0.36，熊市 = -0.24

**与 v27 相关性：** 中（与 mom_5 同属动量域，但正交化后独立信息量大）

**实现方式：**
- 12 月滚动 OLS 回归提取残差
- 6 月累积残差作为因子
- 可与 v27 的 mom_5 对比或替代

---

### [P1] v34 — 日内高频因子

**来源：** 华泰金工 RPV/SRV 因子、民生证券 叶尔乐 偏锋涨跌幅/量涌波动率

**核心因子：**
1. **RPV 价量相关性**：日内 CCOIV + 隔夜 COV 合成
   - 多空年化 16.29%，IR = 2.41
2. **SRV 因子**：RPV 改进版
   - 多空年化 18.91%，IR = 3.07，月度胜率 80%
3. **偏锋涨跌幅**：日内超额动量反转
   - IC = 6.29%，RankIC = 9.20%，多空年化 29.13%
4. **尾盘反转**（第4小时）
   - RankIC = 6.99%，多空年化 27.35%
5. **量涌波动率**：刻画不同情绪投资者影响程度

**A 股独有微观结构：**
- 隔夜负收益之谜：A 股平均隔夜收益（C2O）显著为负
- 日内正向风险溢价，隔夜负向收益

**前置条件：** 需要分钟线数据（当前系统仅有日K）

---

### [P1] v35 — 行业轮动叠加

**来源：** 中银量化行业轮动系列、中信建投六因子行业配置

**核心因子：**
1. **行业动量**：5/20/60 日收益率加权（0.4/0.3/0.3）
2. **行业资金流**：ETF 净流入
3. **行业一致预期**：行业 EPS 调整
4. **行业估值**：行业 PB/PE 分位数

**实证：** 2025 年行业轮动模型跑赢 6 个主要基准 6%-19% 超额

**实现方式：**
- 在 v27 选股后增加行业过滤：只买行业动量前 50% 的股票
- 或作为独立评分维度（行业动量占 20% 权重）

---

### [P1] v36 — 事件驱动

**来源：** BigQuant/新浪研报

**有效事件：**
1. **业绩预告高增长**：公告日超额约 1%，超预期 20% 时延续性强
2. **业绩快报净利润大幅增长**：SUE combo 因子年化超额 23%
3. **大股东增减持**：增持后 60 日超额明显
4. **ST 摘帽**：摘帽后超额显著
5. **分红送转**：填权行情可捕捉

**与 v27 相关性：** 极低（事件驱动 vs 量价技术面）

---

### [P2] v37 — 深度学习因子

**来源：** 华福证券 LSTM 多因子、西南证券 DAFAT Transformer

**实证：**
- LSTM 多因子：80 分析师预期 + 134 资金流 + 43 高频聚合 + 深度学习
  - 多空年化 46%（夏普 2.37），多头年化 23%
- DAFAT（Transformer）：IC 均值从 9.42% 提升至 11.07%，年化 32.30%

**前置条件：** 需要 GPU、大量历史数据、端到端训练框架

---

## 新策略因子调研 — 2026-06-20

> 基于全面搜索调研，以下为尚未尝试的新方向。

## 已完成验证

| 优先级 | 策略编号 | 方向 | WF结果 | 结论 |
|--------|---------|------|--------|------|
| **P0** | **v32** | 分析师预期因子 | 13/13正收益，夏普7.20 | ✅ 精简版（剔除冗余） |
| **P0** | **v33** | 残差动量 | 11/13正收益，夏普6.14 | ⚠️ 双因子无效，单因子可 |
| **P1** | **v35** | 行业轮动 | 13/13正收益，夏普7.27 | ✅ 最优，参数不敏感 |

### v32 精简版改动
- 剔除 analyst_coverage_proxy（与mom_5相关性-0.95）
- 耗时 142s → 88s，夏普 6.53 → 7.20

### v33 双因子验证
- 加入 SMB 因子后夏普反而下降（5.88 → 6.14）
- 原因：SMB 与动量高度相关，残差被 SMB 吃掉的是 alpha

### v35 参数扫描
- SECTOR_MOM_WEIGHT 0.1~0.5：夏普无变化（7.268~7.270）
- 行业动量权重不敏感，个股动量是核心

---

*最后更新: 2026-06-20*
