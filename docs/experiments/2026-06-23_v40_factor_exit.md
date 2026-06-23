# v40 因子恶化卖出实验记录

> 日期：2026-06-23
> 状态：❌ 证伪（框架可用）

## 实验目的

在 v39c 的多因子评分体系基础上，增加"因子恶化卖出"能力：
1. 持仓股每日重评分，评分低于阈值 → 卖出
2. 卖出候选中评分恢复的 → 延迟止盈止损（买回）

## 设计思路

### 核心逻辑
```
每日信号流程：
1. 持仓股评分 → 评分 < SELL_THRESHOLD → 卖出候选
2. 卖出候选中评分 > BUY_BACK_THRESHOLD → 延迟卖出（移入 hold_plan）
3. 正常选股 top N → buy_plan
```

### 两种卖出触发模式
| 模式 | 逻辑 | 参数 |
|------|------|------|
| threshold | 评分 < SELL_THRESHOLD 连续 N 天 → 卖出 | SELL_THRESHOLD=0.20, SELL_PENALTY_N=1 |
| momentum | 评分从持仓高点回落 > MOMENTUM_DROP_PCT → 卖出 | MOMENTUM_DROP_PCT=0.20 |

### 新增参数
| 参数 | 值 | 说明 |
|------|-----|------|
| SELL_THRESHOLD | 0.20 | 持仓评分低于此值触发卖出候选 |
| BUY_BACK_THRESHOLD | 0.30 | 卖出候选评分高于此值延迟卖出 |
| SELL_PENALTY_N | 1 | 连续N天低于阈值才确认卖出 |
| SELL_MODE | momentum | 默认使用动量回落模式 |
| MOMENTUM_DROP_PCT | 0.20 | 评分从高点回落超20% → 卖出 |

## 代码变更

| 文件 | 变更 |
|------|------|
| `scripts/strategies/v40_factor_exit.py` | 新建：评分函数 + check_factor_exit + select_stocks_v40 |
| `core/strategy_map.py` | 注册 v40（含 factor_exit_fn） |
| `scripts/backtest/strategy_adapter.py` | 注册 _v40_select + _risk_params |
| `scripts/backtest/wf_runner.py` | _calc_factors 分支 + 交易循环中加入 factor_exit 检查 |
| `scripts/sim/account_runner.py` | _run_signal_impl 中加入 factor_exit 逻辑 |

## 实验结果

### WF 回测对比（2023-01 ~ 2025-12, 1 fold）

| 实验 | 收益 | 夏普 | 回撤 | factor_decay 次数 | 结论 |
|------|------|------|------|-------------------|------|
| v39c baseline | 46.01% | 1.18 | 31.9% | — | 基准 |
| v40 threshold=0.35 | 29.44% | 0.88 | 29.8% | 0 | 评分范围不够（bug） |
| v40 threshold=0.25 | 29.44% | 0.88 | 29.8% | 0 | 同上 |
| v40 threshold=0.20 | -45.37% | -1.85 | 52.7% | 612 | 好股票全被卖 |
| v40 momentum drop=30% | 29.44% | 0.88 | 29.8% | 0 | 阈值太宽松 |
| v40 momentum drop=20% | 29.44% | 0.88 | 29.8% | 多个 | 被风控覆盖 |

### 关键发现

1. **评分范围 bug**：权重 sum=1.0 时，全市场评分 max≈0.32。SELL_THRESHOLD=0.35 永远无法触发。修复：权重 sum 调至 3.0 或将阈值降到 0.20。

2. **WF 模式循环遗漏**：v40 的 factor_exit_fn 仅在 wf_runner full 模式循环中调用，WF 模式循环遗漏。修复后 factor_decay 卖出正常触发（612次），但结果不变。

3. **因子恶化被风控覆盖**：v39c 选出的持仓股在因子评分恶化时，往往也已触发硬风控（STOP_LOSS=-1.5% 或 HOLD_DAYS_MAX=5），factor_decay 卖出是冗余的。

4. **threshold 模式阈值太高**：SELL_THRESHOLD=0.20 在权重 sum=1.0 时把好股票也卖了（v39c 选出的股票评分也在 0.20-0.37 区间波动），导致 -45.37%。

## 根因分析

### 为什么因子恶化不提供独立增量？

v39c 的选股逻辑是动量驱动（mom_5 > 3%），选出的股票特征：
- 短期动量强，但持续性不确定
- 持有期短（max 5 天），硬风控已经足够严格
- 评分恶化 ≈ 动量衰减 ≈ 价格开始下跌 ≈ 即将触发止损

在这种高强度筛选 + 短持有期的组合下，**硬风控已经先于因子恶化卖出行动了**，因子恶化信号没有额外信息量。

### 什么场景下因子恶化卖出可能有价值？
- 更长持有周期（hold_max > 10），让因子恶化先于超时触发
- 更宽松的风控参数（如 SL=-5%），让因子恶化先于止损触发
- 选股逻辑本身不是纯动量（如价值因子/质量因子），评分恶化是更早期的信号

## 框架验证结论

尽管因子恶化卖出策略证伪，但 **框架扩展模式验证通过**：

✅ factor_exit_fn 注册和调用链路完整
✅ sell_penalty_tracker 跨日状态追踪正常
✅ threshold/momentum 双模式切换正常
✅ 与硬风控共存无冲突

后续需要新卖出逻辑时，只需修改 `check_factor_exit()` 函数，不需要改动主流程。

## 下一步

v40b：纯轮动逻辑（每日卖出持仓评分最低4只 + 买入全市场评分最高4只，无硬风控），验证"追强弃弱"在 A 股的有效性。
