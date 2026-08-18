# v81 低波溢价因子验证设计

> 2026-08-17 | 来源：factor-factory f0002a | 目的：验证外部因子是否可用于我们的策略

## 背景

factor-factory 交付了 f0002a 特质波动率（IVOL）因子，在 zz1000 池 RankIC=0.048，IR=0.509，是其因子库中最强单因子。低波溢价是 A 股经典异象，我们之前未系统测试过。

## 因子定义

- 日收益 ret = close / close.shift(1) - 1
- 市场收益 ret_m = 窗口内所有资产等权日收益
- 对每只资产：ret_i = alpha + beta * ret_m + eps
- IVOL = std(eps)（回归残差波动）
- 因子值 = -IVOL（做多低特质波动）

## 验证步骤

1. **IC分析**：计算200+交易日的截面RankIC
   - 标准：|IC Mean| > 0.03 且 |IR| > 0.3 → 有效
   - 分年稳定性：各年度IC是否一致
   - IC衰减：近12个月IC趋势

2. **与现有因子冗余度**：检查与v75j流动性因子的相关性

3. **WF回测**：如IC通过，进入标准WF验证

## 编号

- 策略编号：v81
- 因子代码：scripts/strategies/v81_ivol.py
- IC分析：scripts/tools/v81_ic_analysis.py
