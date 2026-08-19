# Factor Factory 交付物验证记录

> 看板: https://fkchaos.github.io/factor-factory/
> 查阅地图: docs/DELIVERABLES.md
> 机器可读: deliverables/strategy_export/stock_factors.json
> 上次更新: 2026-08-19
>
> 本文件**只记录已验证的交付物结论**，不做任务跟踪。
> 待办任务见 docs/BACKLOG.md → B04

---

## 判决标准

| IC/IR | 判决 | 行动 |
|-------|------|------|
| \|IC\|>0.03 且 \|IR\|>0.3 | ✅ valid | 进WF |
| 0.01<\|IC\|<0.03 或 0.1<\|IR\|<0.3 | ⚠️ gray | 观望 |
| \|IC\|<0.01 或 \|IR\|<0.1 | ❌ refuted | 不投入 |

## 交付物总览

| 类别 | 数量 | JSON已导出 | 说明 |
|------|------|-----------|------|
| 因子已交付 | 10 | ✅ 10/10 | f0001a~f0010a |
| 信号已交付 | 3 | ✅ | s0001x~s0003x |
| 策略导出 | 3 | — | stock_factors.json / timing_signals.json / risk_params.json |
| 跨因子矩阵 | 2 | — | ic_matrix / icir_matrix |

---

## 第一批交付验证结论（f0001a~f0010a）

### ✅ 有效但无独门优势

| f-code | 名称 | 类别 | IC | IR | 结论 | 备注 |
|--------|------|------|-----|-----|------|------|
| f0002a | 特质波动率(低波溢价) | fundamental | +0.039 | +0.380 | ✅ IC有效，WF失败(Sharpe=-0.89) | 经典因子，可作组合辅助(权重<0.2) |
| f0003a | 等权组合(隔夜反转+低波) | combo | +0.036 | +0.436 | ✅ IC有效，与f0002a冗余(0.82) | 直接用f0002a即可 |

### ⚠️ 边界灰色

| f-code | 名称 | 类别 | IC | IR | 结论 | 备注 |
|--------|------|------|-----|-----|------|------|
| f0001a | 隔夜-日内反转 | technical | +0.028 | +0.326 | ⚠️ IC差一点达标(0.028<0.03) | 独立性好，可作v75j diversifier(权重<0.15) |

### ❌ 已证伪/已弃

| f-code | 名称 | 类别 | 结论 | 死因 |
|--------|------|------|------|------|
| f0004a | 筹码成本偏离 | micro | ❌ | IC=-0.006，无效 |
| f0005a | 量能扩张速度 | volume | ❌ | IC=-0.006，无效 |
| f0006a | 动量20日 | technical | ❌ | 无hs1800数据，方向反(v75j用反转) |
| f0007a | 反转5日 | technical | ❌ | 无hs1800数据 |
| f0008a | 隔夜跳空缺口 | technical | ❌ | 无hs1800数据 |
| f0009a | 涨停封板强度 | micro | ❌ | 无hs1800数据，Sharpe虚高 |
| f0010a | 市值对数 | fundamental | ❌ | 与v61c核心因子(high_small_cap_rank)重叠 |

### 信号验证结论

| s-code | 名称 | 结论 | 备注 |
|--------|------|------|------|
| s0001x | 广度Regime | ❌ | 与v75j同类 |
| s0002x | 风险偏好Regime | ❌ | 无效 |
| s0003x | 波动率Regime | ❌ | 方向相反 |

---

## 组合潜力评估

| f-code | 类别 | 换手 | 与v61c重叠 | 与v75j重叠 | 组合潜力 |
|--------|------|------|-----------|-----------|---------|
| f0001a | technical(反转) | 中 | 低 | 低 | ⭐ 有价值，v75j diversifier |
| f0002a | fundamental(低波) | 低 | 中 | 中 | ⚠️ 边际 |
| f0003a~f0010a | — | — | — | — | ❌ 冗余或太弱 |

---

## 总结

> **当前10个因子无新alpha。** f0002a达标但为经典因子无独门优势，f0001a卡gray线但独立性好可作diversifier。factor-factory的价值在验证流水线，等下一批交付再评估。

---

## 备忘

- 我方回复了 a-share-quant-sim#1（回答3个对接问题）
- 给 factor-factory#1 提了下游反馈（验证结果+需求）
- GH_TOKEN已配：/root/.hermes/.env
- 重要红线：择时信号必须 shift(1)，T日状态最早T+1建仓
- factor-factory 补 f0004a~f0010a 的 hs1800 池数据（不急，因子太弱等下一批）
- factor-factory 补 regime_dependency 和 decay_status（P0排期中）
