---
name: ic-wf-disconnect
description: "IC与WF矛盾诊断。Use when IC vs WF results conflict."
category: data-science
---

# IC-WF脱节诊断

当IC分析与WF回测结果矛盾时的诊断方法。

## 触发条件

- IC显示因子有效但WF失败
- IC显示因子无效但WF通过

## 详细指南

见 `references/ic-wf-disconnect-diagnosis.md`

## 案例集

见 `references/v74a-v75a-case-studies-20260805.md` — v74a(IC强WF弱)、v75a(IC弱WF强)、v75b(慢过滤杀动量)、v75c(数据源缺失致门控失效) 四个真实案例

见 `references/v75-series-regime-filter-findings-20260805.md` — v75系列6种择时/过滤方法完整对比，含MA/regime/波动率/广度四种方案的WF结果和原理分析

## 快速检查

### IC强但WF弱：
- 因子覆盖率>80%？
- 排序周度重叠>50%？
- 分年IC稳定？
- 选股域一致？

### IC弱但WF强：
- 分年IC牛/熊差异大？
- 集中持仓+高止盈？
- 锁定特定板块？

### 择时/过滤方法选择（v75系列实验结论）：
- ❌ MA类regime（MA50/MA100+斜率）：滞后指标，不适合短线动量
- ✅ 市场广度（%科技股在MA20以上）：最有效的regime指标
- ✅ 波动率缩放（仓位∝1/波动率）：可提升Sharpe 5-10%
- 详见 `references/v75-series-regime-filter-findings-20260805.md`

## 规则

IC是必要非充分条件。WF结果才是金标准。

## WF执行注意事项

### 并行WF会OOM
同时运行两个WF进程会被OOM杀。必须串行执行：
```bash
# ❌ BAD — will OOM
python3 wf_runner.py --strategy v75a ... &
python3 wf_runner.py --strategy v75c ... &
# ✅ GOOD — sequential
python3 wf_runner.py --strategy v75a ... && \
python3 wf_runner.py --strategy v75c ...
```

### index_kline表只有ETF
`index_kline`表不包含sh000001等大盘指数，只有ETF。`get_index_kline("sh000001")`返回空列表。regime检测需从`daily_kline`自建指数。
