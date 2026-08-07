# 策略生产线输入项需求规格

> 版本：v1.0 | 日期：2026-08-07

---

## 1. 背景

我们是A股量化策略生产线，需要**标准化的策略输入项**来持续开发新策略。

输入项分为三层：
1. **选股因子**：用于在截面上对股票排序，回答"买什么"
2. **择时信号**：用于判断市场状态，回答"什么时候买/卖多少"
3. **风控参数**：用于控制组合风险，回答"亏多少止损/赚多少止盈"

**原则**：我们按三类分别提需求，拿过来后在策略研发流程中自己组合。

---

## 2. 通用规范

### 2.1 命名规范

| 元素 | 规范 | 示例 |
|------|------|------|
| 因子名 | `{类别}_{描述}`，小写下划线 | `tech_liquidity_v1` |
| 信号名 | `{类别}_{描述}` | `timing_breadth_v1` |
| 参数名 | `{类别}_{描述}` | `risk_sl_tp_v1` |
| 版本号 | `v{主版本}`，递增 | `v1`, `v2` |

### 2.2 数据格式

| 类型 | 格式 | 说明 |
|------|------|------|
| 因子值 | parquet/csv | 每只股票每日因子值 |
| 信号值 | parquet/csv | 每日信号值（0-1或阈值） |
| 参数组合 | JSON | 参数名+回测结果 |
| 元数据 | JSON | 见各类具体要求 |

### 2.3 索引文件

每个交付包必须包含 `_REGISTRY.csv`：
```csv
name,type,version,status,delivery_date
tech_liquidity_v1,stock,v1,active,2026-08-07
```

---

## 3. 选股因子需求

### 3.1 交付物清单

| 交付物 | 格式 | 必须/可选 | 说明 |
|--------|------|-----------|------|
| 因子值面板 | parquet/csv | 必须 | 每只股票每日因子值 |
| IC分析结果 | JSON/CSV | 必须 | RankIC、ICIR |
| 分层回测结果 | JSON/CSV | 必须 | 分5层收益对比 |
| 因子相关性 | csv | 必须 | 与其他因子的相关性矩阵 |
| 元数据 | JSON | 必须 | 见3.2 |
| 过拟合审计 | JSON/CSV | 可选 | DSR、PBO |
| 分regime分析 | JSON | 可选 | 不同市场环境下的IC |
| IC衰减分析 | JSON | 可选 | 近12个月滚动IC |

### 3.2 元数据字段（必须）

```json
{
    "name": "因子名称（tech_liquidity_v1格式）",
    "type": "stock",
    "category": "technical|fundamental|sentiment|capital_flow|alternative",
    "direction": "positive|negative",
    "data_source": "数据来源说明",
    "calc_logic": "计算逻辑的自然语言描述",
    "universe": "适用股票池（zz1800/sz50/hs300/all）",
    "history_length": "历史数据长度（如：5年）",
    "update_frequency": "更新频率（daily/weekly/monthly）",
    "status": "active|deprecated|experimental",
    "description": "一句话描述"
}
```

### 3.3 内容维度要求

**必须覆盖的维度**：

| 维度 | 说明 | 交付物 |
|------|------|--------|
| 因子值 | 每只股票每日的因子值 | 因子值面板 |
| 有效性证据 | RankIC、ICIR统计量 | IC分析结果 |
| 分层收益 | 因子值分5层的收益对比 | 分层回测结果 |
| 正交性 | 与其他因子的相关性 | 相关性矩阵 |

**建议覆盖的维度**（加分项）：

| 维度 | 说明 | 交付物 |
|------|------|--------|
| 过拟合风险 | DSR、PBO统计量 | 过拟合审计 |
| 环境依赖 | 不同市场环境下的表现 | 分regime分析 |
| 时间稳定性 | IC随时间的变化趋势 | IC衰减分析 |

### 3.4 因子值面板格式

```csv
date,code,factor_value
2026-01-01,000001.SZ,0.123
2026-01-01,000002.SZ,0.456
...
```

### 3.5 IC分析格式

```json
{
    "rank_ic_mean": 0.05,
    "rank_ic_std": 0.08,
    "icir": 0.625,
    "ic_positive_ratio": 0.65,
    "t_stat": 3.2
}
```

### 3.6 分层回测格式

```json
{
    "layers": [
        {"layer": 1, "annual_return": 0.15, "sharpe": 0.8},
        {"layer": 2, "annual_return": 0.12, "sharpe": 0.7},
        {"layer": 3, "annual_return": 0.10, "sharpe": 0.6},
        {"layer": 4, "annual_return": 0.08, "sharpe": 0.5},
        {"layer": 5, "annual_return": 0.05, "sharpe": 0.3}
    ],
    "long_short_annual_return": 0.10,
    "long_short_sharpe": 1.2
}
```

### 3.7 相关性矩阵格式

```csv
,tech_liquidity_v1,tech_volatility_v1,fundamental_pe_v1
tech_liquidity_v1,1.0,0.3,-0.2
tech_volatility_v1,0.3,1.0,0.1
fundamental_pe_v1,-0.2,0.1,1.0
```

---

## 4. 择时信号需求

### 4.1 交付物清单

| 交付物 | 格式 | 必须/可选 | 说明 |
|--------|------|-----------|------|
| 信号值面板 | parquet/csv | 必须 | 每日信号值 |
| 阈值回测结果 | JSON | 必须 | 不同阈值下的回测表现 |
| 元数据 | JSON | 必须 | 见4.2 |
| 触发逻辑说明 | Markdown | 必须 | 信号触发/停止的条件 |
| 敏感性分析 | JSON | 可选 | 阈值变化对结果的影响 |

### 4.2 元数据字段（必须）

```json
{
    "name": "信号名称（timing_breadth_v1格式）",
    "type": "timing",
    "category": "regime|sentiment|trend|volatility",
    "trigger_logic": "触发条件的自然语言描述",
    "position_logic": "仓位调整逻辑的自然语言描述",
    "data_source": "数据来源说明",
    "universe": "适用股票池",
    "history_length": "历史数据长度",
    "update_frequency": "更新频率",
    "status": "active|deprecated|experimental",
    "description": "一句话描述"
}
```

### 4.3 内容维度要求

**必须覆盖的维度**：

| 维度 | 说明 | 交付物 |
|------|------|--------|
| 信号值 | 每日的信号值（0-1或阈值） | 信号值面板 |
| 触发条件 | 什么条件下触发/停止 | 触发逻辑说明 |
| 阈值回测 | 不同阈值下的回测表现 | 阈值回测结果 |

**建议覆盖的维度**（加分项）：

| 维度 | 说明 | 交付物 |
|------|------|--------|
| 阈值敏感性 | 阈值变化对结果的影响 | 敏感性分析 |
| 胜率统计 | 信号触发后的胜率 | 阈值回测结果 |

### 4.4 信号值面板格式

```csv
date,signal_value
2026-01-01,0.85
2026-01-02,0.72
...
```

### 4.5 阈值回测格式

```json
{
    "thresholds": [0.3, 0.4, 0.5, 0.6, 0.7],
    "results": [
        {"threshold": 0.3, "sharpe": 1.2, "return": 0.15, "win_rate": 0.6},
        {"threshold": 0.4, "sharpe": 1.5, "return": 0.18, "win_rate": 0.65},
        {"threshold": 0.5, "sharpe": 1.8, "return": 0.22, "win_rate": 0.7},
        {"threshold": 0.6, "sharpe": 1.3, "return": 0.12, "win_rate": 0.75},
        {"threshold": 0.7, "sharpe": 0.9, "return": 0.05, "win_rate": 0.8}
    ]
}
```

---

## 5. 风控参数需求

### 5.1 交付物清单

| 交付物 | 格式 | 必须/可选 | 说明 |
|--------|------|-----------|------|
| 参数组合 | JSON | 必须 | 参数名+回测结果 |
| 回测结果 | JSON | 必须 | Sharpe、收益、回撤 |
| 元数据 | JSON | 必须 | 见5.2 |
| 适用条件说明 | Markdown | 必须 | 什么市场环境适用 |
| 参数敏感性 | JSON | 可选 | 参数变化对结果的影响 |

### 5.2 元数据字段（必须）

```json
{
    "name": "参数名称（risk_sl_tp_v1格式）",
    "type": "risk",
    "category": "stop_loss|take_profit|holding|position|combined",
    "params": "参数定义的自然语言描述",
    "applicable_conditions": "适用条件的自然语言描述",
    "data_source": "数据来源说明",
    "history_length": "历史数据长度",
    "status": "active|deprecated|experimental",
    "description": "一句话描述"
}
```

### 5.3 内容维度要求

**必须覆盖的维度**：

| 维度 | 说明 | 交付物 |
|------|------|--------|
| 参数组合 | 参数名+参数值 | 参数组合 |
| 回测表现 | Sharpe、收益、回撤 | 回测结果 |
| 适用条件 | 什么市场环境适用 | 适用条件说明 |

**建议覆盖的维度**（加分项）：

| 维度 | 说明 | 交付物 |
|------|------|--------|
| 参数敏感性 | 参数变化对结果的影响 | 参数敏感性 |
| 最大回撤 | 最大回撤及恢复时间 | 回测结果 |

### 5.4 参数组合格式

```json
{
    "param_name": "combined_risk_v1",
    "params": {
        "stop_loss": -0.08,
        "take_profit": 0.30,
        "hold_days_max": 15,
        "max_daily_buy": 3,
        "max_position": 0.35,
        "max_holdings": 3
    },
    "backtest": {
        "sharpe": 0.9,
        "annual_return": 0.25,
        "max_drawdown": 0.45,
        "win_rate": 0.6
    }
}
```

---

## 6. 交付检查清单

### 6.1 通用检查

- [ ] `_REGISTRY.csv` 存在且格式正确
- [ ] 命名规范符合要求（`{类别}_{描述}_v{版本}`）
- [ ] 元数据字段完整（所有必须字段）
- [ ] 数据格式正确（parquet/csv可直接加载）

### 6.2 选股因子检查

- [ ] 因子值面板：每只股票每日因子值
- [ ] IC分析：RankIC、ICIR、t统计量
- [ ] 分层回测：分5层收益+多空组合
- [ ] 相关性矩阵：与其他因子的相关性

### 6.3 择时信号检查

- [ ] 信号值面板：每日信号值
- [ ] 阈值回测：不同阈值下的Sharpe/收益/胜率
- [ ] 触发逻辑说明：触发/停止条件

### 6.4 风控参数检查

- [ ] 参数组合：参数名+参数值
- [ ] 回测结果：Sharpe/收益/回撤
- [ ] 适用条件说明：什么市场环境适用

---

## 7. 附录

### 7.1 术语表

| 术语 | 说明 |
|------|------|
| RankIC | 排序IC，因子值与下期收益的秩相关系数 |
| ICIR | IC均值/IC标准差，衡量IC的稳定性 |
| DSR | Deflated Sharpe Ratio，调整后的Sharpe |
| PBO | Probability of Backtest Overfitting，回测过拟合概率 |
| 分层回测 | 因子值分N层，比较各层收益差异 |
| 分regime | 不同市场环境（强势/弱势/震荡）下的分析 |
| IC衰减 | IC随时间变化的趋势 |

### 7.2 我们的验证标准（内部使用，不对外）

> 以下是我们收到输入项后的内部验证标准，不对外提供。

**选股因子**：
- |IC Mean| > 0.03 且 |IR| > 0.3 → 有效
- |IC Mean| < 0.01 或 |IR| < 0.1 → 证伪

**择时信号**：
- 阈值过滤回测Sharpe > 1.5 → 有效
- 阈值过滤回测Sharpe < 1.0 → 证伪

**风控参数**：
- 风控参数回测Sharpe > 1.0 → 有效
- 风控参数回测Sharpe < 0.8 → 证伪

### 7.3 参考文档

- 策略研发全流程框架：`docs/experiments/2026-08-07_strategy_rnd_framework.md`
- 输入项标准化章节：`docs/experiments/2026-08-07_strategy_rnd_framework.md` 阶段0
