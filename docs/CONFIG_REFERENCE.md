# 配置参考

> 所有可调参数统一在 `core/config.py` 顶部 `CONFIG` 字典中定义。
> 修改 CONFIG 一处，所有 dataclass 默认值自动生效。

## 一、CONFIG 字典

### 交易成本

| 参数 | 默认值 | 说明 | 影响范围 |
|------|--------|------|---------|
| `initial_capital` | 200000 | 初始资金（元） | 模拟盘、回测 |
| `commission_rate` | 0.0003 | 佣金率（万3） | 每次买入+卖出 |
| `stamp_tax_rate` | 0.001 | 印花税（千1，卖出收） | 每次卖出 |
| `slippage_rate` | 0.001 | 滑点（千1） | 买入加价、卖出减价 |

**单次交易成本示例**（买入 1 万元股票）：
- 佣金 = 10000 × 0.03% = 3 元（不足 5 元按 5 元收，见 `account.py:buy`）
- 滑点 = 10000 × 0.1% = 10 元
- 买入总成本 = 10000 + 3 + 10 = 10013 元

**单次交易成本示例**（卖出 1 万元股票）：
- 佣金 = 10000 × 0.03% = 3 元
- 印花税 = 10000 × 0.1% = 10 元
- 滑点 = 10000 × 0.1% = 10 元
- 卖出净收入 = 10000 - 3 - 10 - 10 = 9977 元

### 风控参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `stop_loss` | 0.20 | 固定止损比例（亏损 20% 触发） |
| `stop_loss_atr_k` | 6.0 | ATR 动态止损倍数：止损价 = 成本价 - K × ATR(14) |
| `top_n` | 12 | 目标持仓数量 |
| `rebalance_freq` | 20 | 调仓频率（交易日，约每月一次） |
| `max_single_weight` | 0.15 | 组合层面单只最大仓位（风控上限） |
| `max_daily_turnover` | 0.30 | 单日最大换手率（30%） |
| `min_rebalance_interval` | 3 | 最小调仓间隔（交易日，防止频繁调仓） |

**止损逻辑**（`account.py:check_stop_loss`）：
1. 固定止损：`(成本价 - 现价) / 成本价 ≥ stop_loss` → 全仓卖出
2. ATR 止损（需开启 `use_atr_stop`）：`(成本价 - 现价) / 成本价 ≥ K × ATR(14) / 现价` → 全仓卖出
3. 两种模式独立判断，任一触发即卖出

### 选股参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `max_position` | 0.10 | 单只最大仓位占比（10%，策略级） |
| `max_industry_weight` | 0.25 | 行业仓位上限（25%，v11b WF 最优） |

**行业限制逻辑**（`strategy.py:filter_and_rank`）：
- 按行业分组，每行业最多 `ceil(max_industry_weight × top_n)` 只
- `max_industry_weight=0` 表示不限制

### 波动率缩放

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `vol_target` | 0.20 | 目标年化波动率（20%） |

**逻辑**：当组合实际波动率 > vol_target 时，等比缩减所有仓位。

---

## 二、Dataclass 默认值

所有 dataclass 通过 `field(default_factory=lambda: CONFIG["key"])` 从 CONFIG 读取默认值。

### TradingCosts

```python
@dataclass
class TradingCosts:
    initial_capital: float = CONFIG["initial_capital"]    # 200000
    commission_rate: float = CONFIG["commission_rate"]    # 0.0003
    stamp_tax_rate:  float = CONFIG["stamp_tax_rate"]     # 0.001
    slippage_rate:   float = CONFIG["slippage_rate"]      # 0.001
```

### RiskLimits

```python
@dataclass
class RiskLimits:
    stop_loss:            float = CONFIG["stop_loss"]              # 0.20
    stop_loss_atr_k:      float = CONFIG["stop_loss_atr_k"]        # 6.0
    top_n:                int   = CONFIG["top_n"]                  # 12
    rebalance_freq:       int   = CONFIG["rebalance_freq"]         # 20
    max_single_weight:    float = CONFIG["max_single_weight"]      # 0.15
    max_daily_turnover:   float = CONFIG["max_daily_turnover"]     # 0.30
    min_rebalance_interval: int = CONFIG["min_rebalance_interval"] # 3
```

### MarketFilter

```python
@dataclass
class MarketFilter:
    include_prefixes: tuple = ('6', '0', '3')          # 沪主板/深主板/创业板
    exclude_prefixes: tuple = ('688', '8', '4', '2')   # 科创板/北交所/三板/B股
    exclude_delisted: bool  = True                      # 自动排除退市股
    delist_max_gap:   int   = 30                        # 超过30天无数据视为退市
```

### StrategyConfig

```python
@dataclass
class StrategyConfig:
    label: str = "default"                    # 策略标识名
    weight_method: str = "equal"              # equal | ic_ir | markowitz
    top_n: int = CONFIG["top_n"]              # 持仓数
    rebalance_freq: int = CONFIG["rebalance_freq"]  # 调仓频率
    factor_weights: Optional[Dict] = None     # None = 用 DEFAULT_FACTOR_WEIGHTS

    # 风控
    stop_loss: float = CONFIG["stop_loss"]
    max_position: float = CONFIG["max_position"]
    max_industry_weight: float = CONFIG["max_industry_weight"]
    max_daily_turnover: float = 0             # 0 = 不限制

    # 波动率缩放
    use_vol_scaling: bool = True
    vol_target: float = CONFIG["vol_target"]

    # 止盈
    use_take_profit: bool = False
    tp_tiers: Optional[list] = None           # [(涨幅, 卖出比例), ...]

    # 持有期衰减
    use_holding_decay: bool = False

    # ATR 止损
    use_atr_stop: bool = False
    atr_k: float = CONFIG["stop_loss_atr_k"]

    # Ensemble
    ensemble_groups: Optional[Dict] = None    # {组名: {因子: 权重}}
    ensemble_group_top_n: int = 4             # 每组选股数
```

---

## 三、因子列表

### 全部 29 个因子（`DEFAULT_FACTOR_WEIGHTS`）

| 因子 | 默认权重 | 方向 | 说明 |
|------|---------|------|------|
| `mom_5` | +0.05 | 动量 | 5 日收益率 |
| `mom_10` | +0.10 | 动量 | 10 日收益率 |
| `mom_20` | +0.10 | 动量 | 20 日收益率 |
| `mom_60` | +0.08 | 动量 | 60 日收益率 |
| `mom_120` | +0.05 | 动量 | 120 日收益率 |
| `rev_3` | +0.05 | 反转 | 3 日反转（负 IC） |
| `rev_5` | +0.08 | 反转 | 5 日反转 |
| `rev_10` | +0.05 | 反转 | 10 日反转 |
| `vol_10` | -0.03 | 波动率 | 10 日波动率（负权重 = 偏好低波动） |
| `vol_20` | -0.05 | 波动率 | 20 日波动率 |
| `vol_60` | -0.05 | 波动率 | 60 日波动率 |
| `vol_change` | +0.03 | 波动率变化 | 短期 vs 长期波动率变化 |
| `vol_ratio_5` | +0.05 | 量比 | 5 日量比 |
| `vol_ratio_20` | +0.05 | 量比 | 20 日量比 |
| `amount_ratio` | +0.05 | 额比 | 成交额比 |
| `rsi_6` | +0.03 | 超买超卖 | 6 日 RSI |
| `rsi_14` | +0.05 | 超买超卖 | 14 日 RSI |
| `rsi_28` | +0.02 | 超买超卖 | 28 日 RSI |
| `macd_12_26` | +0.08 | 趋势 | MACD(12,26) |
| `macd_5_35` | +0.04 | 趋势 | MACD(5,35) 短期 |
| `boll_pos_10` | +0.03 | 布林带 | 10 日布林位置 |
| `boll_pos_20` | +0.03 | 布林带 | 20 日布林位置 |
| `boll_width_20` | +0.03 | 布林带 | 20 日布林宽度 |
| `atr_14` | -0.03 | 波动率 | 14 日 ATR（负权重） |
| `skew_20` | +0.02 | 偏度 | 20 日收益偏度 |
| `kurt_20` | -0.02 | 峰度 | 20 日收益峰度 |
| `vwap_mom` | +0.03 | VWAP | VWAP 动量 |
| `rel_strength_20` | +0.05 | 相对强度 | 20 日相对强度 |
| `rel_strength_60` | +0.03 | 相对强度 | 60 日相对强度 |

**权重方向说明**：
- 正权重 → 因子值越大越好（动量、量比、RSI 等）
- 负权重 → 因子值越小越好（波动率、ATR、峰度等）
- 权重绝对值 = 相对重要性

---

## 四、修改配置的方法

### 方法一：改 CONFIG 字典（推荐）

编辑 `core/config.py` 顶部：

```python
CONFIG = dict(
    initial_capital = 500000,    # 改初始资金为 50 万
    stop_loss = 0.15,            # 改止损为 15%
    top_n = 15,                  # 改持仓数为 15
    ...
)
```

所有使用默认值的 dataclass 实例自动生效。

### 方法二：策略级覆盖

在 `STRATEGY_PROFILES` 中定义策略时，直接指定参数：

```python
PROFILE_MY_STRATEGY = StrategyConfig(
    label="my_strategy",
    top_n=15,                    # 覆盖 CONFIG 默认值
    stop_loss=0.15,
    max_industry_weight=0.30,
    ...
)
```

### 方法三：命令行覆盖（仅回测）

```bash
python scripts/run_backtest.py --strategy v11b_zz800_union \
    --top-n 15 --stop-loss 0.15 --rebalance-freq 10
```

---

## 五、配置生效路径

```
CONFIG 字典
  ├── TradingCosts()     → account.py (交易成本)
  ├── RiskLimits()       → strategy.py (风控参数)
  ├── MarketFilter()     → data.py (股票池过滤)
  ├── StrategyConfig()   → strategy.py (策略参数)
  │   └── STRATEGY_PROFILES → run_backtest.py / sim_daily_v7.py
  └── DEFAULT_FACTOR_WEIGHTS → scoring.py (因子加权)
```
