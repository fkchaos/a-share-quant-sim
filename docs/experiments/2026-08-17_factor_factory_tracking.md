# Factor Factory 外部因子源跟踪

> 创建：2026-08-17 | 仓库：git@github.com:fkchaos/factor-factory.git
> 本地克隆：/root/factor-factory | 用途：稳定信息源，跟踪其交付物

---

## 交付物索引（截至 2026-08-17）

> 信息源：`deliverables/factors/_INDEX.md` + `deliverables/strategy_export/*.json`
> 每次 git pull 后检查 `_REGISTRY.csv` 有无新增条目，有则更新本表

### 选股因子

| f-code | 名称 | 简称 | RankIC | IR | 主场池 | 我方结论 | 说明 |
|--------|------|------|--------|-----|--------|---------|------|
| f0001a | 隔夜-日内反转 | overnight_intraday | 0.031 | 0.355 | zz1000 | ⏳ 待测 | v77曾证伪，需确认差异 |
| f0002a | 特质波动率 | ivol | **0.048** | **0.509** | zz1000 | ❌ WF未通过 | IC=0.055有效，但WF Sharpe=-0.89，2026年失效 |
| f0003a | 等权组合(1+2) | combo_equal_v1 | **0.044** | **0.522** | zz1000 | ⏳ 待测 | f0001a+f0002a等权 |
| f0004a | 筹码成本偏离 | chip_cost_distance | -0.006 | -0.053 | hs300 | ❌ 弃 | IC<0.01 |
| f0005a | 量能扩张速度 | volume_expansion | -0.006 | -0.066 | hs300 | ❌ 弃 | IC<0.01 |

### 择时信号

| s-code | 名称 | 叠加Sharpe | DD改善 | 我方结论 | 说明 |
|--------|------|-----------|--------|---------|------|
| s0001x | 广度Regime | 0.94 | -45%→-31% | ❌ 不用 | 自判refuted；v75j已有同类 |
| s0002x | 风险偏好Regime | 0.84 | 微弱 | ❌ 不用 | refuted |
| s0003x | 波动率Regime | 0.50 | 恶化 | ❌ 不用 | refuted；方向与先验相反 |

### 组合导出（strategy_export/）

| 文件 | 条目 | 说明 |
|------|------|------|
| stock_factors.json | 3 | f0001a/f0002a/f0003a，source=external |
| timing_signals.json | 3 | s0001x/s0002x/s0003x，exec_lag=1 |
| risk_params.json | 0 | 占位，不在他们范围 |

消费方式：直接放 `alpha-research/inputs/`，我们的阶段0可解析。

---

## 已完成验证

### f0001a 隔夜-日内反转 — 2026-08-18

**与v77的区别：**
- v77: overnight_return = open[t]/close[t-1] - 1（只用隔夜成分）
- f0001a: overnight - intraday = (open[t]/close[t-1] - 1) - (close[t]/open[t] - 1)（隔夜-日内组合）

**IC分析（zz1800池）：**
- IC均值: 0.0191 ⚠️ (<0.03)
- IR: 0.1367 ⚠️ (<0.3)
- P(>0): 55.2%
- 分年：2022年IC=0.032最强，2024年IC=-0.007最弱
- 近12个月IC: 0.0165, IR: 0.118

**冗余度检查：**
- f0001a vs IVOL: 0.023（独立）✅
- f0001a vs Turnover20: -0.016（独立）✅

**分Regime IC：**
- risk_on: IC=0.0192, IR=0.14（440天）
- risk_off: IC=0.0175, IR=0.12（883天）

**结论：** IC微弱（0.019 < 0.03阈值），不进入WF。比factor-factory结果低（0.031），可能原因：我们未做行业中性化。但独立性好，可作为组合辅助因子（权重<0.15）。

### f0002a 低波溢价（IVOL）— 2026-08-17

**IC分析：**
- IC均值: 0.0552 ✅ (>0.03)
- IR: 0.3411 ✅ (>0.3)
- P(>0): 61.6% ✅
- 分年：2022-2024年IC=0.07最强，2025年衰减至0.04，2026年失效(0.002)

**冗余度检查：**
- IVOL vs Volume20 RankIC相关性：-0.25（低相关）✅
- 结论：独立因子，适合组合

**分Regime IC：**
- risk_on: IC=0.073, IR=0.462（更有效）
- risk_off: IC=0.050, IR=0.307

**WF回测（标准条件）：**
- train=252, test=126, step=63, pool=zz1800
- 测试期平均收益: -6.97%
- 测试期平均Sharpe: -0.891
- 正收益fold: 4/16 (25%)
- **❌ WF未通过**

**结论：** IC有效但WF失败，可能原因：
1. 2026年因子失效（IC=0.002）
2. 选股太集中（前3只）
3. 广度过滤过度保守

**文件：**
- 设计文档：`docs/experiments/2026-08-17_v81_ivol_design.md`
- 策略文件：`scripts/strategies/v81_ivol.py`
- IC结果：`/tmp/v81_ivol_ic.json`
- 冗余度结果：`/tmp/v81_redundancy.json`
- Regime结果：`/tmp/v81_regime.json`

---

## 待验证 TODO

- [x] f0001a 隔夜反转：与v77对比，搞清为什么结论相反 → **IC=0.019微弱，不进入WF，可作辅助因子**
- [ ] f0003a 组合因子：评估是否值得在zz1000池用
- [ ] idea_backlog中i20260806-004（盈利增长）：唯一基本面idea，值得单独评估

---

## 同步检查清单

每次 factor-factory 有更新时：
1. `cd /root/factor-factory && git pull`
2. 检查 `_REGISTRY.csv` 是否有新增 f-code / s-code
3. 检查 `strategy_export/` JSON 条目变化
4. 新因子进入「待验证 TODO」
5. 更新本表「交付物索引」
