# 社区宣传文案

## 1. GitHub 项目页优化

### Description
```
A股量化模拟交易系统 — 零配置部署，回测/模拟盘共享同一套代码，5分钟跑通。支持 v27(价量共振)、v20c(尾盘缩量)、v11b(Ensemble) 三策略并行，Walk-Forward 验证通过。MIT 开源。
```

### Topics
```
quantitative-trading, a-share, python, finance, backtest, simulation, algorithmic-trading, chinese-market
```

---

## 2. V2EX（分享创造节点）

### 标题
```
分享创造：A股量化模拟交易系统，零配置部署，5分钟跑通
```

### 正文
```
大家好，最近做了一个 A 股量化模拟交易系统，开源到 GitHub 了。

【是什么】
一个基于 Python 的 A 股量化模拟交易系统。回测引擎和模拟盘共享同一套代码（core/），杜绝回测/实盘不一致的问题。

【为什么做】
市面上的量化平台要么太复杂（聚宽、掘金需要注册、配置环境），要么太简单（只是单个策略脚本）。我想做一个「中间态」——开箱即用，但又足够灵活可以自己改策略。

【核心特点】
- 零配置：pip install pandas numpy requests，3 个依赖，5 分钟跑通
- 三策略并行：v27 价量共振（WF 夏普 8.66）、v20c 尾盘缩量、v11b Ensemble
- 完整 CLI：账户管理、持仓调整、手动买卖，不需要写 SQL
- 中文文档齐全：部署指南、用户手册、架构文档、策略注册表
- MIT 协议

【回测结果】
- v27 价量共振：全量年化 251%，WF 15/15 正收益，夏普 8.66
- v20c 尾盘缩量：全量年化 59%，WF 15/16 正收益，夏普 5.74
- v11b Ensemble：全量年化 30%，WF 11/16 正收益

【GitHub】
https://github.com/fkchaos/a-share-quant-sim

还在早期阶段，欢迎大家提建议、提 PR、报 bug。特别是：
- 有没有更好的 A 股数据源推荐？
- 策略参数还有哪些可以优化的方向？
- 文档有哪些地方看不懂的？

谢谢！
```

---

## 3. 知乎

### 标题
```
从零搭建 A 股量化模拟交易系统：开源、零配置、5 分钟跑通
```

### 正文
```
最近几个月，业余时间做了一个 A 股量化模拟交易系统，开源到 GitHub 了。

【项目定位】
不是聚宽/掘金那种在线平台，而是一个本地部署的 Python 项目。回测和模拟盘用同一套代码，避免「回测赚钱、实盘亏钱」的问题。

【技术栈】
- Python 3.10+，只有 3 个依赖：pandas、numpy、requests
- SQLite 数据库，不需要安装数据库服务器
- 腾讯行情接口（免费）获取数据

【架构设计】
- core/ 共享引擎：回测和模拟盘共用
- scripts/strategies/ 选股策略：可插拔，新增策略只需注册一行
- scripts/sim/ 模拟盘执行层：信号→执行→报告三阶段
- strategy_map.py 策略注册表：动态加载选股函数

【三个策略】
1. v27 价量共振：动量+价量相关性，WF 夏普 8.66
2. v20c 尾盘缩量：尾盘缩量企稳信号，次日开盘买，持有 2 天
3. v11b Ensemble：三组因子（动量/波动率/反转）并集选股

【WF 验证标准】
- 16 folds 滚动验证
- 正收益 fold ≥ 60%
- WF 平均夏普 ≥ 0.5

【GitHub】
https://github.com/fkchaos/a-share-quant-sim

欢迎交流，提 issue 或 PR 更好。
```

---

## 4. 掘金

### 标题
```
开源：A 股量化模拟交易系统，回测/实盘共享同一套代码
```

### 正文
```
做了一个 A 股量化模拟系统，开源了。

【痛点】
1. 聚宽/掘金等平台需要注册、配置环境，数据源受限
2. 自己写策略脚本，回测和实盘代码不一致，回测赚钱实盘亏
3. 很多开源项目依赖太多，部署复杂

【解决方案】
- 零配置：pip install pandas numpy requests
- 回测/模拟盘共享 core/ 引擎
- 策略可插拔：新增策略只需在 strategy_map.py 注册一行
- 完整 CLI：不需要写 SQL

【技术架构】
- 数据层：SQLite + 腾讯行情接口
- 因子层：51 个技术因子（动量/反转/波动率/成交量/RSI/趋势）
- 策略层：3 个策略并行（v27/v20c/v11b）
- 执行层：信号→执行→报告三阶段

【GitHub】
https://github.com/fkchaos/a-share-quant-sim

欢迎提建议、提 PR。
```

---

## 5. Reddit r/algotrading（Text post，不贴链接）

### 标题
```
I built an A-share quantitative simulation trading system — open source, zero-config, runs in 5 minutes. Looking for feedback.
```

### 正文
```
Hi r/algotrading,

I've been building an open-source A-share (Chinese stock market) quantitative simulation trading system over the past few months. I'd love to get feedback from this community.

**What it is:**
A local-deployed Python project for backtesting and live simulation. The backtest engine and simulation engine share the same codebase (core/), so there's no discrepancy between backtest and live results.

**Key features:**
- Zero config: `pip install pandas numpy requests` — 3 dependencies, runs in 5 minutes
- 3 strategies running in parallel: v27 (price-volume resonance, WF Sharpe 8.66), v20c (tail-session volume contraction), v11b (Ensemble multi-group)
- Complete CLI: account management, position adjustment, manual trading — no SQL needed
- 51 technical factors: momentum, reversal, volatility, volume, RSI, trend
- Walk-Forward validation: 16 folds, rolling sample-out testing
- Chinese documentation: deploy guide, user manual, architecture, strategy registry
- MIT license

**Backtest results (2020-2026, CSI 800 universe, 715 stocks):**
- v27: annualized 251%, WF 15/15 positive folds, Sharpe 8.66
- v20c: annualized 59%, WF 15/16 positive folds, Sharpe 5.74
- v11b: annualized 30%, WF 11/16 positive folds

**What I'm looking for:**
1. Better data source recommendations for A-shares?
2. Strategy parameter optimization suggestions?
3. Is the WF validation methodology sound?
4. Any interest in collaborating?

GitHub: https://github.com/fkchaos/a-share-quant-sim

Thanks for reading!
```

---

## 6. Hacker News（Show HN）

### 标题
```
Show HN: A-share quantitative simulation system — zero-config, 5-minute setup
```

### 正文 (first comment)
```
I built an open-source quantitative simulation trading system for the Chinese A-share market.

Key design decisions:
- Backtest and live simulation share the exact same codebase (core/) — no backtest/live discrepancy
- Zero config: 3 Python dependencies, SQLite (no server needed), free Tencent market data API
- Strategy registry pattern: add a new strategy by registering one line in strategy_map.py
- Walk-Forward validation with 16 folds to detect overfitting

3 strategies: v27 (price-volume resonance, WF Sharpe 8.66), v20c (tail-session volume contraction), v11b (Ensemble).

GitHub: https://github.com/fkchaos/a-share-quant-sim

Would love feedback on the architecture and WF methodology.
```

---

## 7. Product Hunt

### 名称
```
A-Share Quant Sim
```

### 一句话描述
```
Zero-config A-share quantitative simulation system — backtest and live trading share the same codebase
```

### 详细描述
```
A quantitative simulation trading system for the Chinese A-share market.

**Why it exists:**
Most quant platforms are either too complex (require registration, cloud setup) or too simple (single-strategy scripts). This project aims for the middle ground — production-ready out of the box, yet flexible enough to customize.

**What's inside:**
- 3 strategies: price-volume resonance, tail-session volume contraction, Ensemble multi-group
- 51 technical factors with Z-score normalization
- Walk-Forward validation (16 folds)
- Complete CLI for account/position/trade management
- 70+ unit tests

**Tech stack:** Python 3.10+, SQLite, pandas, numpy, requests. MIT license.

**GitHub:** https://github.com/fkchaos/a-share-quant-sim
```

### 标签
```
Open Source, Python, Finance, Trading, Data Science
```

---

## 8. Twitter/X

### 推文
```
Built an A-share quantitative simulation system. 

🔹 Zero config: pip install pandas numpy requests
🔹 Backtest/live share same codebase
🔹 3 strategies, Walk-Forward validated
🔹 51 technical factors
🔹 MIT open source

GitHub: https://github.com/fkchaos/a-share-quant-sim

Feedback welcome! #OpenSource #QuantTrading #Python #Finance
```

---

## 9. 少数派

### 标题
```
A 股量化模拟交易系统：零配置部署，回测/实盘代码一致
```

### 正文
```
【项目信息】
- GitHub：https://github.com/fkchaos/a-share-quant-sim
- 协议：MIT
- 语言：Python 3.10+
- 依赖：pandas、numpy、requests（仅 3 个）

【适合谁用】
- 对 A 股量化感兴趣的程序员
- 想验证自己策略但不想用聚宽/掘金的人
- 想学习量化交易系统架构的开发者

【核心卖点】
1. 零配置部署：pip install 之后直接跑
2. 回测/模拟盘共享代码：杜绝回测赚钱实盘亏
3. 三个内置策略，Walk-Forward 验证通过
4. 完整 CLI：不需要写 SQL
5. 中文文档齐全

【个人评价】
还在早期，但架构设计比较清晰。策略参数可能有过拟合风险，建议自己跑 WF 验证后再用。数据源用的腾讯接口，免费但偶尔不稳定。

推荐指数：⭐⭐⭐⭐（4/5）
扣 1 分因为数据源单一，且 A 股量化策略的实盘表现还需要更长时间验证。
```
