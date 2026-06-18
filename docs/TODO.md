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
