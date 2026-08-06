---
name: strategy-integration
description: 新策略集成到模拟盘的标准流程。Use when adding new strategy to live trading.
---

# 新策略集成标准流程

## 概述
新策略从WF通过到模拟盘运行的完整检查清单。

## 步骤

### 1. 代码层
- [ ] 策略文件 `scripts/strategies/vXX_*.py` 包含 `calc_factors_*` 和 `select_stocks_*`
- [ ] `calc_factors_*` 必须接受 `extra_data=None` 参数（account_runner.py传7个参数）
- [ ] DEFAULT_PARAMS 包含所有风控参数

### 2. 注册层
- [ ] `core/strategy_map.py` 注册策略（mode/params/select_fn/calc_factors_fn）
- [ ] `scripts/backtest/strategy_adapter.py` 添加：
  - `_select_fns["vXX"]` 注册
  - `_risk_params["vXX"]` 风控参数
  - `_regime_params["vXX"]` regime参数（如有）
  - `_vXX_select()` 选股方法

### 3. 格式层
- [ ] `scripts/tools/format_report.py` 添加策略公式说明（在format_signal函数中）
  - 格式：`elif strategy in ("vXX",):` + 公式文本 + 因子解释
- [ ] format_report.py需要适配新策略的特殊字段（如广度/情绪/regime）
- [ ] select_stocks函数必须支持`return_all`参数，且从adapter层一路传到策略层
  - adapter._vXX_select → select_stocks_vXX → 返回Top10候选（非仅Top3）

### 4. 文档层
- [ ] CLAUDE.md 更新策略参数表
- [ ] docs/strategy/RESULTS_LOG.md 追加WF结果
- [ ] docs/experiments/ 设计文档

### 5. Cron层（实盘）
- [ ] 信号cron prompt更新策略名
- [ ] 执行cron prompt更新策略名
- [ ] 收盘报告cron更新策略说明

## 已知陷阱
- `calc_factors_*` 必须接受 `extra_data=None`——否则account_runner.py传7参数会报错
- format_report.py 必须有策略公式说明——否则信号报告只显示"综合评分"
- strategy_adapter._risk_params 是dict，直接修改即可（不是返回副本）
- 改参数必须同时改 strategy_map.py 和策略文件 DEFAULT_PARAMS
- select_stocks的`return_all`参数必须从adapter层一路传到策略层，否则top_scores只返回Top3
- 策略切换后必须验证信号cron格式——检查：持仓明细、现金、公式说明、Top10得分、市场情绪/广度
- 涨停过滤在打分阶段不排除（保留用于展示），在生成买入计划时排除
- 跨账户去重需在账户params_json中设置`CROSS_ACCOUNT_DEDUP: true`
- 卖出又买入同一股票时，应从卖出列表移除（避免白交手续费）
- 科创板过滤（688/689）必须在排序前执行，不能排完再过滤导致Top10数量不足
- 信号cron格式一致性：切换策略后，format_report.py必须同步更新新策略的公式说明，否则显示"综合评分"
- 广度/市场情绪显示：v75f等带广度过滤的策略，信号报告必须在Top10前显示广度公式和当前值
- 参数扫描：用全量回测（full=True）而非WF做参数扫描，快20倍；但最优组合必须用WF最终验证
- calc_factors_v75f之前缺extra_data参数导致account_runner传7参数报TypeError——信号cron返回error状态
- 全量回测run_wf(full=True)返回DataFrame不是dict——取值用result['test_sharpe'].iloc[0]而非result['sharpe']
- 广度值需要从策略层传到plan再到signal输出——在account_runner.py里单独调用_calc_breadth并加到plan dict