# 策略注册表

> 回测入口：`python scripts/backtest/wf_runner.py --strategy <name>`
> 模拟盘入口：`scripts/sim/account_runner.py --strategy <name>`
> 配置管理：`core/strategy_map.py`（所有策略参数集中在此）
> 最后更新：2026-06-23

---

## 一、活跃策略

### v39c — 多因子评分（当前 baseline）

| 维度 | 值 |
|------|-----|
| **选股池** | 中证800（707只，排除科创板/北交所） |
| **评分模式** | 7因子加权评分（v27门槛 + v39评分逻辑） |
| **核心因子** | mom_5, pv_corr_20, turnover, size_factor, fund_flow, gap_ratio, illiq |
| **选股门槛** | mom_5 > 3% + pv_corr_10 > -0.5 + delist_risk 排除 |
| **评分权重** | W_MOM=0.20, W_PV_CORR=0.05, W_TURNOVER=0.10, W_SIZE=0.10, W_FUND_FLOW=0.15, W_GAP=0.10, W_ILLIQ=0.10 |
| **调仓频率** | 每日调仓 |
| **止损/止盈** | -1.5% / +3% |
| **持有期** | max 5天，浮盈≥3%延长到10天 |
| **最大持仓** | 8只，单只上限20% |
| **初始资金** | 20万（账户2） |

**WF 结果（2023-2025, 1 fold×252天）：**

| 指标 | v39c |
|------|------|
| 收益 | 46.01% |
| Sharpe | 1.18 |
| 回撤 | 31.9% |

**状态**：⚠️ 作为 v40 对比基准保留，不作为模拟盘候选（v27 更优）

---

### v40 — 因子恶化卖出（框架验证）

| 维度 | 值 |
|------|-----|
| **选股逻辑** | 同 v39c（7因子评分） |
| **卖出增强** | 每日持仓重评分，评分恶化 → 卖出候选 |
| **因子退出函数** | `scripts/strategies/v40_factor_exit.py` → `check_factor_exit()` |
| **两种模式** | threshold（阈值法）/ momentum（评分从高点回落法） |
| **延迟卖出** | 卖出候选中评分恢复的 → 移入 hold_plan |

**参数：**

| 参数 | 值 | 说明 |
|------|-----|------|
| SELL_THRESHOLD | 0.20 | 持仓评分 < 0.20 → 卖出候选 |
| BUY_BACK_THRESHOLD | 0.30 | 卖出候选评分 > 0.30 → 延迟卖出 |
| SELL_PENALTY_N | 1 | 连续N天低于阈值才确认（threshold模式） |
| SELL_MODE | momentum | 默认使用动量回落模式 |
| MOMENTUM_DROP_PCT | 0.20 | 评分从高点回落超20% → 卖出 |

**WF 结果（2023-2025, 1 fold×252天）：**

| 变体 | 收益 | Sharpe | 回撤 | factor_decay次数 |
|------|------|--------|------|-----------------|
| v40 threshold=0.35 | 29.44% | 0.88 | 29.8% | 0（评分范围bug） |
| v40 threshold=0.20 | -45.37% | -1.85 | 52.7% | 612（好股票被卖） |
| v40 momentum=30% | 29.44% | 0.88 | 29.8% | 0（太宽松） |
| v40 momentum=20% | 29.44% | 0.88 | 29.8% | 多个（被风控覆盖） |

**结论**：❌ 因子恶化卖出证伪。框架扩展模式验证通过，但不提供独立增量（被硬风控覆盖）。

**状态**：⚠️ 框架保留，待 v40b 或其他卖出逻辑复用

---

## 二、已退役策略

### v27 — 价量共振（历史最优，2026-06 前运行账户2）

| 维度 | 值 |
|------|-----|
| **选股逻辑** | mom_5 > 5% + pv_corr_20 + gap/illiq/boll 加分 |
| **止损/止盈** | -1.5% / +3% |
| **WF** | 15/15(100%)正收益，夏普5.96，回撤3.29% |

**退役原因**：v39c/v40 新框架更灵活，v27 作为历史记录保留。

---

### v20c — 尾盘缩量企稳（已退役）

| 维度 | 值 |
|------|-----|
| **选股逻辑** | 尾盘缩量 + 价格企稳 + 软约束评分 |
| **WF** | 面板顺序 bug 修复后策略失效（5/16 正收益，全量-67%） |

**退役原因**：核心因子 IC≈0，无预测能力。

---

### v11b — Ensemble 截面因子（已暂停）

| 维度 | 值 |
|------|-----|
| **选股逻辑** | 3组独立选股（动量/波动率/反转）并集 |
| **WF** | 11/16(69%)正收益，夏普1.70 |

**状态**：⏸️ 暂停，代码保留。

---

### v38 — 价量共振v3（已归档）

| 维度 | 值 |
|------|-----|
| **选股逻辑** | mom>7% + pv_corr>0.15 + 评分排序 |
| **回补后** | 184%/夏普1.22/回撤14.9% |

**归档原因**：交易频率太低（215次/全量），收益远低于 v27。

---

### v32/v33/v35 — 调研策略（已归档）

| 策略 | 方向 | WF夏普 | 结论 |
|------|------|--------|------|
| v32 | 分析师预期 | 7.20 | 相对v27无提升 |
| v33 | 残差动量 | 6.14 | 双因子无效 |
| v35 | 行业轮动 | 7.27 | 相对v27无提升 |

---

## 三、架构记录

### 策略注册完整流程

新增策略必须完成以下 4 步：

1. **创建 `scripts/strategies/vXX.py`**：calc_factors + select_stocks_vXX
2. **注册 `core/strategy_map.py`**：select_fn + calc_factors_fn + params（+ factor_exit_fn 可选）
3. **注册 `scripts/backtest/strategy_adapter.py`**：_register 方法 + _risk_params + _regime_params
4. **注册 `scripts/backtest/wf_runner.py`**：_calc_factors elif 分支 + 交易循环（如有 factor_exit）

**验证命令**：
```bash
python -c "from scripts.backtest.strategy_adapter import get_adapter; print(get_adapter().list_strategies())"
python -c "from core.strategy_map import list_strategy_names; print(list_strategy_names())"
```

### 账户-策略分离

- 账户在 DB `account.strategy` 字段绑定策略
- 策略不绑定账户，可随时切换
- 统一入口：`account_runner.py --strategy <name>`

### 因子恶化卖出扩展模式

v40 验证的 factor_exit_fn 接口：
- 策略可注册自定义 `factor_exit_fn`（可选）
- wf_runner 交易循环中在风控检查前调用
- 返回 `(to_sell, to_defer, tracker)` 三元组
- 支持 threshold（阈值法）和 momentum（动量回落法）

---

## 四、实验记录索引

| 日期 | 文档 | 内容 |
|------|------|------|
| 2026-06-23 | `experiments/2026-06-23_v40_factor_exit.md` | v40 因子恶化卖出完整实验 |
| 2026-06-22 | `experiments/2026-06-22_v38_experiment.md` | v38 多版本迭代记录 |
| 2026-06-21 | `experiments/2026-06-21_regime_tuning.md` | Regime 择时实验（证伪） |

---

*文档维护：每次策略变更后更新本文档 + `docs/strategy/RESULTS_LOG.md`*
