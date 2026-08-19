# Factor Factory 交付物跟踪（长期维护）

> 看板: https://fkchaos.github.io/factor-factory/
> 查阅地图: docs/DELIVERABLES.md
> 机器可读: deliverables/strategy_export/stock_factors.json
> 上次更新: 2026-08-18

---

## 交付物总览

| 类别 | 数量 | JSON已导出 | 说明 |
|------|------|-----------|------|
| 因子已交付 | 10 | ✅ 10/10 | f0001a~f0010a |
| 信号已交付 | 3 | ✅ | s0001x~s0003x |
| 策略导出 | 3 | — | stock_factors.json / timing_signals.json / risk_params.json |
| 跨因子矩阵 | 2 | — | ic_matrix / icir_matrix |

## 我方验证摘要

### 判决标准
| IC/IR | 判决 | 行动 |
|-------|------|------|
| \|IC\|>0.03 且 \|IR\|>0.3 | ✅ valid | 进WF |
| 0.01<\|IC\|<0.03 或 0.1<\|IR\|<0.3 | ⚠️ gray | 观望，等更强数据 |
| \|IC\|<0.01 或 \|IR\|<0.1 | ❌ refuted | 不投入 |

### 因子详情（按hs1800池排序，我方股票池=zz1800）

| f-code | 名称 | 类别 | 方向 | hs1800 IC | hs1800 IR | 判决 | 备注 |
|--------|------|------|------|-----------|-----------|------|------|
| f0002a | 特质波动率(低波溢价) | fundamental | 正 | +0.039 | +0.380 | ✅ valid | 经典因子，早知，辅助候选 |
| f0003a | 等权组合(隔夜反转+低波) | combo | 正 | +0.036 | +0.436 | ✅ valid | =f0001a+f0002a，冗余 |
| f0001a | 隔夜-日内反转 | technical | 正 | +0.028 | +0.326 | ⚠️ gray | 差一点达标 |
| f0010a | 市值对数 | fundamental | 正 | — | — | — | 无hs1800数据，与v61c冗余 |
| f0009a | 涨停封板强度 | micro | 正 | — | — | — | 无hs1800数据，Sharpe虚高 |
| f0006a | 动量20日 | technical | 反 | — | — | — | 无hs1800数据 |
| f0007a | 反转5日 | technical | 正 | — | — | — | 无hs1800数据 |
| f0008a | 隔夜跳空缺口 | technical | 正 | — | — | — | 无hs1800数据 |
| f0004a | 筹码成本偏离 | micro | 正 | — | — | ❌ refuted | 无hs1800数据 |
| f0005a | 量能扩张速度 | volume | 反 | — | — | ❌ refuted | 无hs1800数据 |

### 结论
> **当前10个因子无新alpha。** f0002a达标但为经典因子无独门优势，f0003a是前两者等权冗余，f0001a卡gray线。f0004a~f0010a全部refuted或gray且缺hs1800数据。factor-factory的价值在验证流水线，等下一批交付再评估。

---

## 组合潜力评估维度

每个因子标注以下信息，方便后续想组合时查阅：

| 维度 | 说明 | 我们需要什么 |
|------|------|------------|
| **方向** | 正/反 | 组合时符号对齐 |
| **因子类别** | technical/fundamental/micro/sentiment/volume | 跨类别组合效果最好 |
| **Regime依赖** | 什么市场环境生效 | 互补型regime最佳 |
| **换手成本** | 高换手吃alpha | 低换手优先 |
| **与现有策略重叠** | 跟v61c/v75j因子相关性 | 低重叠才有组合价值 |

### 各因子组合潜力

| f-code | 类别 | Regime | 换手 | 与v61c重叠 | 与v75j重叠 | 组合潜力 |
|--------|------|--------|------|-----------|-----------|---------|
| f0001a | technical(反转) | 未知 | 中 | 低（方向不同） | 低 | ⭐ 有价值，可作v75j diversifier |
| f0002a | fundamental(低波) | 未知 | 低 | 中（v61c有低换手） | 中 | ⚠️ 边际，可能与现有因子相关 |
| f0003a | combo | 未知 | 中 | 高（=f0001a+f0002a） | 高 | ❌ 冗余 |
| f0004a | micro(筹码) | 未知 | — | 未知 | 未知 | ❌ 太弱 |
| f0005a | volume(量能) | 未知 | — | 未知 | 未知 | ❌ 太弱 |
| f0006a | technical(动量) | 未知 | 高 | 低（反向） | 低 | ❌ 太弱 |
| f0007a | technical(反转) | 未知 | 高 | 低 | 低 | ❌ 太弱 |
| f0008a | technical(跳空) | 未知 | 中 | 低 | 低 | ❌ 太弱 |
| f0009a | micro(涨停) | 未知 | 高 | 低 | 低 | ❌ 太弱 |
| f0010a | fundamental(规模) | 未知 | 低 | **高（=small_cap_rank）** | 中 | ❌ 与v61c核心因子重叠 |

---

## 备忘

### 对接事项
- 我方回复了 a-share-quant-sim#1（回答3个对接问题）
- 给 factor-factory#1 提了下游反馈（验证结果+需求）
- GH_TOKEN已配：/root/.hermes/.env
- 重要红线：择时信号必须 shift(1)，T日状态最早T+1建仓

### 待跟进
- factor-factory 补 f0004a~f0010a 的 hs1800 池数据（不急，因子太弱等下一批）
- factor-factory 补 regime_dependency 和 decay_status（P0排期中）
