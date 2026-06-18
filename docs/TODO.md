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

## 3. 回测框架重构

**现状：**
- `run_backtest.py` 只支持内置策略（v4_baseline 等因子加权框架）
- v27/v20c/v11b 各自有独立 WF 脚本，选股逻辑和交易逻辑分散
- 模拟盘（`account_runner.py`）和回测用的是**不同的代码路径**，结果可能不一致

**目标：**
- 一个通用回测入口（类似 `account_runner.py` 的定位）
- 每个策略注册：选股函数 + 交易逻辑（TP/SL/持仓天数/动态仓位）
- 模拟盘和回测调用**同一套代码**，确保表现一致
- 中间层映射表：策略名 → 实现函数，避免 if-else 满天飞

**参考结构：**
```
scripts/backtest/
├── run_backtest.py          # 通用入口（兼容内置策略）
├── strategy_adapter.py      # 中间层映射表
├── v27_engine.py            # v27 选股+交易引擎
├── v20c_engine.py           # v20c 选股+交易引擎
├── v11b_engine.py           # v11b 选股+交易引擎
└── wf_runner.py             # Walk-Forward 通用运行器
```

**原则：**
- 模拟盘用什么选股，回测就用什么选股
- 模拟盘用什么止盈止损，回测就用什么止盈止损
- 差异只在：回测用历史数据撮合，模拟盘用实时数据

---

*最后更新: 2026-06-18*
