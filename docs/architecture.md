# 代码架构讲解

> 面向开发者的实现逻辑与框架说明
>
> 最后更新：2026-06-05（v11b Ensemble 策略 + 统一评分引擎重构）

## 一、整体架构：共享引擎 + 策略 Profile 模式

```
                          core/ (唯一权威引擎)
  ┌─────────────────────────────────────────────────────────────────┐
  │  config.py                                                      │
  │    DEFAULT_FACTOR_WEIGHTS (40因子权重, sum=1.0)                  │
  │    StrategyConfig dataclass (所有策略参数)                        │
  │    STRATEGY_PROFILES dict (预定义策略: v4~v11b, 当前最优 v11b (Ensemble))          │
  │    TradingCosts + RiskLimits dataclass                          │
  │  factors.py    calc_factors_panel() / calc_factors_single()     │
  │                40 技术因子计算                                    │
  │  scoring.py    composite_score(panel) / score_all_stocks(live)  │
  │                + ensemble_union_score(panel/live)               │
  │                截面 Z-Score + 加权评分 + 多组 Ensemble           │
  │  account.py    PortfolioState + buy/sell/check_stop_loss        │
  │                partial_sell / check_take_profit                  │
  │                apply_holding_decay / allocate_weights           │
  │                纯函数式交易 API                                    │
  │  position.py   Position 领域模型                                 │
  │  ──────────────────────────────────────────────────────────────  │
  │  ml.py         FeatureBuilder + RollingTrainer + EnsembleTrainer│
  │                ML 训练/预测核心 (Walk-Forward 回测用)              │
  │  ml_predictor.py  train_and_save() + MLPredictor                │
  │                离线训练 + 在线推理 (模拟盘用)                      │
  │  strategy.py   StrategyEngine 统一策略入口                       │
  │                factor / ensemble / ml / hybrid 四种模式          │
  └──────────┬──────────────────────────────┬──────────────────────┘
             │                              │
             ▼                              ▼
  ┌──────────────────────┐   ┌─────────────────────────────┐
  │  sim_daily_v7.py    │   │  run_backtest.py            │
  │  (三阶段模拟盘)      │   │  (历史回测引擎)               │
  │                      │   │                             │
  │  策略来源:            │   │  策略来源:                    │
  │  StrategyEngine      │   │  composite_score /           │
  │  (config/strategy_   │   │  run_ml_pipeline             │
  │   config.json)       │   │                             │
  │                      │   │  流程:                       │
  │  **模拟盘 Pipeline (三阶段)**: │  │  1. 加载 CSV 面板             │
  │  ① 更新数据 (AM)       │  │  2. calc_factors_panel()     │
  │  ② 加载账户            │  │  3. IC 分析(可选)             │
  │  ③ 止损/止盈/decay     │  │  4. composite_score/         │
  │  ④ 数据质量            │  │     run_ml_pipeline          │
  │  ⑤ 调仓 → 信号文件(PM) │  │  5. 回测循环→buy/sell        │
  │  ⑥ 执行信号→交易(PM)   │  │  6. 绩效指标+自检             │
  │  ⑦ 保存+报告(收盘)     │  │                             │
  │ 11:35 信号 → 13:00 执行 → 15:30 报告 │                  │
  └──────────────────────┘   └─────────────────────────────┘

  ┌──────────────────────┐   ┌─────────────────────────────┐
  │ train_ml_model.py   │   │  update_daily_data.py       │
  │ (ML 离线训练)        │   │  (数据层: 腾讯 API → CSV)    │
  │ 每周一 06:00 cron   │   │                             │
  └──────────────────────┘   └─────────────────────────────┘
  ┌──────────────────────┐   ┌─────────────────────────────┐
  │ fill_daily_gaps.py   │   │  ic_analysis_zz800.py       │
  │ (缺口填充)           │   │  (中证800 IC/IR分析)         │
  └──────────────────────┘   └─────────────────────────────┘
```

**设计原则**：
- `core/` 是纯数据结构和函数 — 无 I/O、无副作用
- `STRATEGY_PROFILES` 是策略参数唯一权威来源（回测+模拟盘共用）
- 因子权重唯一权威来源是 `core/config.py` 的 `DEFAULT_FACTOR_WEIGHTS`
- **策略评分统一入口**：`StrategyEngine`（factor/ensemble/ml/hybrid 四种模式）
- **ML 训练/推理分离**：`train_and_save()` 离线训练 → `MLPredictor` 在线推理
- **上午信号 = 策略决策**（风控+调仓 → plan）；**下午执行 = 纯执行**（按 plan 买卖）

---

## 二、`core/` 层：六个模块详解

### 2.1 `config.py` — 配置管理 + 策略 Profiles

```python
# 因子权重（40因子，sum=1.0）
DEFAULT_FACTOR_WEIGHTS = { 'mom_5': 0.05, ... }

# 策略参数 dataclass
@dataclass
class StrategyConfig:
    label: str
    weight_method: str          # equal | vol_inverse
    top_n: int
    rebalance_freq: int
    stop_loss: float
    max_industry_weight: float
    max_daily_turnover: float
    use_take_profit: bool
    tp_tiers: list
    use_holding_decay: bool
    factor_weights: dict        # 因子权重（v6b 为8因子等权）
    ensemble_groups: dict = None  # 多组 Ensemble 配置（v11b）
    ensemble_group_top_n: int = 4  # 每组选几只

# 预定义策略 Profiles（回测+模拟盘共用）
STRATEGY_PROFILES = {
    "v4_baseline":      PROFILE_V4_BASELINE,
    "v4_industry_cap":  PROFILE_V4_WITH_INDUSTRY_CAP,
    "v6b_8f_pos_ic":    PROFILE_V6B_8F_POS_IC,        # 8因子正IC等权
    "v7a_8f_ind40":     PROFILE_V7A_8F_IND40,
    "v7b_8f_ind50":     PROFILE_V7B_8F_IND50,
    "v7c_8f_no_ind":    PROFILE_V7C_8F_NO_IND,
    "v8_all_icir":      PROFILE_V8_ALL_ICIR,
    "v10_small_cap":    PROFILE_V10_SMALL_CAP,
    "v10b_small_mom":   PROFILE_V10B_SMALL_MOM,
    "v11b_zz800_union": PROFILE_V11B_ZZ800_UNION,  # 3组Ensemble, 当前最优
}
```

### 2.2 `factors.py` — 因子计算引擎

双模式设计：

```python
# 面板模式（回测）: DataFrame (dates × stocks) → {factor_name: DataFrame}
calc_factors_panel(close_panel, volume_panel, amount_panel, open_p, high_p, low_p)

# 单股模式（模拟盘）: DataFrame (single stock) → {factor_name: float}
calc_factors_single(df)
```

共 **40 个因子**，分 8 类：

| 类别 | 因子 |
|------|------|
| 动量 | mom_5, mom_10, mom_20, mom_60, mom_120 |
| 反转 | rev_3, rev_5, rev_10 |
| 波动率 | vol_10, vol_20, vol_60, vol_change |
| 成交量 | vol_ratio_5, vol_ratio_20, amount_ratio |
| RSI | rsi_6, rsi_14, rsi_28 |
| 趋势 | macd_12_26, macd_5_35, boll_pos_10, boll_pos_20, boll_width_20 |
| 统计/其他 | atr_14, skew_20, kurt_20, vwap_mom, rel_strength_20, rel_strength_60 |
| 短线/风格 | amplitude, illiquidity, turnover_skew, turnover_change, price_impact, pv_corr, chip_kurt, obv_slope, gap_ratio, high_low_range, intraday_drift, small_cap |

### 2.3 `account.py` — 纯函数式交易 API

```python
@dataclass
class PortfolioState:
    cash: float
    initial_capital: float
    holdings: Dict[str, dict]  # {code: {shares, cost_price, entry_date, tp_taken: list}}
    trade_log: List[dict]
    nav_history: List[dict]

# 核心交易函数（全部返回新 state，不修改旧 state）
def buy(state, code, price, date, shares=None, target_value=None) -> PortfolioState:
def sell(state, code, price, date, reason='SELL') -> PortfolioState:
def partial_sell(state, code, price, date, sell_fraction, reason) -> PortfolioState:

# 风控函数
def check_stop_loss(state, date, prices) -> PortfolioState:
def check_take_profit(state, date, prices, tiers=None) -> PortfolioState:
def apply_holding_decay(state, date, prices, rebalance_freq=20) -> PortfolioState:

# 权重分配
def allocate_weights(top_stocks, price_data, method='equal', vol_series=None, max_position=0.10):

def portfolio_value(state, date, prices) -> float:
```

### 2.4 `scoring.py` — 评分合成 + Ensemble 多组选股

```python
def standardize(df):                                    # 截面 Z-Score
def composite_score(factors, weights):                  # 加权合成（回测 panel 模式）
def score_all_stocks(all_factors, weights, dynamic_weights=None):  # 模拟盘单股模式 → {code: score}
def ensemble_union_score(factors, groups, group_top_n):  # Ensemble 面板模式
def ensemble_union_score_single(all_factors, groups, group_top_n):  # Ensemble 单股模式
def factor_correlation(factors):                        # 因子相关性矩阵
```

### 2.5 `ml.py` — ML 训练/预测核心（Walk-Forward 回测用）

```python
class FeatureBuilder:
    """从因子面板构建 ML 特征矩阵和标签（多周期）"""
    def build(factors, close_panel, stock_names) -> (X, y_multi, date_index, code_index):

class RollingTrainer:
    """Walk-Forward 滚动训练引擎（单模型 LGB）"""
    def run(X, y_multi, date_index, code_index) -> (predictions, fold_info):

class EnsembleTrainer:
    """三模型 ensemble Walk-Forward 训练（LGB+XGB+Ridge + OLS Stacking）"""
    def run(X, y_multi, date_index, code_index) -> (predictions, fold_info):

def ml_score_panel(predictions, date_index, code_index, close_panel) -> DataFrame:
    """ML 预测值 → 选股评分面板"""

def run_ml_pipeline(factors, close_panel, ...) -> (score_panel, fold_info):
    """端到端 ML 流水线"""
```

### 2.6 `ml_predictor.py` — ML 离线训练 + 在线推理（模拟盘用）

```python
def train_and_save(factors, close_panel, model_dir, profile, hybrid_alpha, ...) -> meta:
    """
    全量训练并保存 ML ensemble 模型。
    - 取最近 train_days 天数据
    - 训练 LGB + XGB + Ridge
    - OLS Stacking（positive constraint）
    - pickle 序列化 + JSON 元数据
    - 保存到 model_dir/latest.json
    """

class MLPredictor:
    """在线推理器（模拟盘用）"""
    def __init__(self, model_dir):   # 加载 latest.json + pickle 模型
    def predict(all_factors) -> {code: score}:  # 对当日截面做 ML 预测
```

### 2.7 `strategy.py` — 统一策略评分入口

```python
class StrategyEngine:
    """
    统一选股评分引擎。
    支持 factor / ensemble / ml / hybrid 四种模式。
    通过 config/strategy_config.json 配置。
    """
    def __init__(self, profile, mode, hybrid_alpha, model_dir):
        # mode: "factor" | "ensemble" | "ml" | "hybrid"

    def score_panel(factors_panel, close_panel) -> DataFrame:
        """面板模式评分（回测用）"""

    def score_single(all_factors) -> {code: score}:
        """单股模式评分（模拟盘用）"""
        # factor → score_all_stocks()
        # ml → MLPredictor.predict()
        # ensemble → 3组独立选股，并集得分
        # hybrid → α×ML_zscore + (1-α)×factor_zscore

    def filter_stocks(scores, price_data, portfolio_value, ...) -> (codes, scores):
        """统一选股过滤：板块 + 流动性 + 行业分散 → top_n"""
```

**策略配置文件** (`config/strategy_config.json` 或 `$DATA_DIR/strategy_config.json`)：
```json
{
  "mode": "ensemble",
  "profile": "v11b_zz800_union"
}
```

---

## 三、调度层：sim_daily_v7.py

### 三阶段 Pipeline（2026-06-05 最终版）

```
intraday_signal (11:35) — 策略决策，生成 plan：
  ① update_data(腾讯API) → ② load_account → ③ data_quality
  → ④ 风控：止损/止盈/decay → risk_sell
  → ⑤ 调仓：StrategyEngine.score_single() + filter_stocks()
  → ⑥ 生成 plan（sell_plan/hold_plan/buy_plan），保存 state

intraday_execute (13:00) — 纯执行，不做策略判断：
  ⑦ load trade_plan + 开盘价
  ⑧ 执行 plan：sell → hold(add) → buy
  ⑨ 保存 state + 执行报告

report_only (15:30) — 纯只读报告，零副作用：
  ⑩ load_account + 本地价格 → 输出报告
  （不更新数据/不调仓/不修改 state）
```

### Plan 结构

```json
{
  "date": "2026-06-03_AM",
  "trade_count": 21,
  "no_rebalance": false,
  "total_nav": 197884.0,
  "sell_plan": [
    {"code": "600183", "name": "生益科技", "shares": "all", "price": 137.80, "reason": "止损"},
    {"code": "002938", "name": "鹏鼎控股", "shares": 100, "price": 112.00, "reason": "非目标持仓"}
  ],
  "hold_plan": [
    {"code": "600522", "name": "中天科技", "current_shares": 300, "price": 43.80,
     "current_weight": 0.067, "target_weight": 0.083, "action": "add", "add_amount": 3200}
  ],
  "buy_plan": [
    {"code": "600362", "name": "江西铜业", "reference_price": 48.50, "target_amount": 16584}
  ]
}
```

- `sell_plan`：按顺序执行（风控优先 → 调仓卖出），`shares="all"` 表示清仓
- `hold_plan`：`action=hold` 不动，`action=add` 补仓到目标权重
- `buy_plan`：新买入，按 `target_amount` 分配资金
- 风控操作（止损/止盈/decay）合并到 `sell_plan`，用 `reason` 区分

### 辅助模块

| 模块 | 用途 | 对应 P 级 |
|------|------|-----------|
| `constraints.py` | 涨跌停/T+1/停牌检查 | P0-1 |
| `data_quality.py` | 数据过期/空值/异常跳变 | P0-2 |
| `portfolio_controls.py` | 日换手率上限控制 | P0-3 |
| `industry.py` | 行业分类 + 行业上限 | P1-1 |
| `indices.py` | 6个指数趋势展示 | P1-2 |

---

## 四、回测引擎：run_backtest.py

### 与模拟盘的一致性保证

```
sim_daily_v7.py ──▶ core.account.buy / sell / check_stop_loss / check_take_profit
run_backtest.py   ──▶ core.account.buy / sell / check_stop_loss / check_take_profit
                      ↑↑↑ 完全相同的函数 ↑↑↑
```

### ML 回测路径

```bash
# Walk-Forward ML 回测
python scripts/ml_rolling_train.py --hybrid-alpha 0.8 --start 2021-01-01

# 纯因子回测
python scripts/run_backtest.py --strategy v11b_zz800_union --walk-forward
```

### 防错机制

- **启动配置摘要**：每次回测打印 ind_cap/tp/decay/atr 状态
- **结果自检**：负收益/超大回撤/零止损触发时主动告警
- **数据质量门禁**：过期/空值/异常跳变检测

---

## 五、ML 训练脚本：train_ml_model.py

```bash
# 一键训练（全量数据 → 三模型 ensemble → 保存）
python scripts/train_ml_model.py

# 输出: /root/data/ml_models/latest.json + pickle 文件
# 耗时: ~60s (280只 × 5年 × 3模型)
```

**Cron 定时训练**：每周一 06:00 自动训练（赶在开盘前完成）

---

## 六、数据层：增量更新机制

```
① 读取本地 CSV，找到最后日期 local_latest
② 请求最近 N 天数据（N = 缺口 + 5，防止遗漏）
③ 追加到原 CSV（不覆盖）
④ 失败的等待 3 秒后重试
```

**数据路径**：
- 默认：`data/daily/`（工程内）
- 覆盖：设 `BACKTEST_DATA_DIR=/path/to/data` 环境变量
- 所有脚本均支持

---

## 七、Golden Tests

```
tests/test_golden.py — 12 个快速测试（< 1s）
tests/test_ensemble.py — 19 个 Ensemble 评分测试（< 1s）
  TestFactorComputation (5):  40因子完整性、权重和=1.0
  TestScoring (2):           评分分布正确性
  TestAccountLogic (4):      分级止盈、持有期decay、等权分配
  TestGoldenBaseline (1, slow): 端到端回测基准值验证

tests/test_sim_trading.py — 39 个模拟盘执行测试（< 1s）
  TestBasicTrading (5):       买入/卖出/partial_sell/碎股/交易日志
  TestStopLoss (3):           止损触发/不触发/全仓卖出
  TestTakeProfit (4):         tier1/tier2/不触发/tp_taken防重复
  TestHoldingDecay (2):       超期衰减/期内不衰减
  TestPlanGeneration (5):     调仓日/非调仓日/sell/buy/hold分配
  TestPlanExecution (5):      执行顺序/跳过不存在持仓/零价格
  TestPlanSafety (4):         日期校验/空plan/清除防重复
  TestEndToEndEnd (3):        非调仓日/调仓日/止损端到端
  TestSimBacktestConsistency (5): 净值计算/plan结构/一致性

运行: python -m pytest tests/ -v -k "not slow"
```

---

## 十、Cron 报告格式

### 上午信号报告
```
一、当前持仓（执行前）
二、操作计划（卖出/补仓/新买入/持有不动）
三、汇总（现金占比/预期持仓数/调仓日/ML状态）
```

### 下午执行报告
```
一、执行明细（卖出/买入/补仓，含状态）
二、执行后持仓
三、净值变化
四、异常/注意事项
```

### 收盘报告（report_only 模式）
```
一、净值概况（总净值/今日收益/总收益率/持仓数/现金占比）
二、持仓明细（代码/名称/股数/市值/权重/盈亏）
三、行业分布
四、指数概况
```
注意：report_only 模式不更新数据，用本地已有价格（净值可能有 1 天误差）

---

## 八、关键常量一览

| 常量 | 值 | 位置 |
|------|-----|------|
| 初始资金 | 200,000 | `config.yaml costs.initial_capital` |
| 佣金率 | 0.03% | `core.config.costs.commission_rate` |
| 印花税 | 0.1% | `core.config.costs.stamp_tax_rate` |
| 滑点 | 0.1% | `core.config.costs.slippage_rate` |
| 因子数 | 40 | `core.config.DEFAULT_FACTOR_WEIGHTS` |
| 止盈档位 | [(0.10,0.30),(0.20,0.30),(0.30,1.00)] | `PROFILE_V5_TP_DECAY.tp_tiers` |
| ML 训练窗口 | 252天 | `train_ml_model.py TRAIN_DAYS` |
| ML 预测周期 | [5, 20]天 | `train_ml_model.py FORWARD_PERIODS` |
| 数据 API | `web.ifzq.gtimg.cn` | `update_daily_data.py` |

---

## 九、改进行动追踪

| 优先级 | 方向 | 状态 | 日期 |
|--------|------|------|------|
| P0 🔴 | 交易约束（涨跌停/停牌/T+1） | ✅ 完成 | 2026-05-29 |
| P0 🔴 | 数据质量门禁 | ✅ 完成 | 2026-05-29 |
| P0 🔴 | 换手率上限控制 | ✅ 完成 | 2026-05-29 |
| P1 🟠 | 行业仓位上限 | ✅ 完成 | 2026-05-29 |
| P1 🟠 | 指数趋势展示 | ✅ 完成 | 2026-05-29 |
| ⭐ | core/ 统一（回测=模拟盘交易逻辑） | ✅ 完成 | 2026-05-30 |
| ⭐ | 分级止盈 + 持有期 decay | ✅ 完成 | 2026-06-02 |
| ⭐ | 策略参数解耦 (STRATEGY_PROFILES) | ✅ 完成 | 2026-06-02 |
| ⭐ | Golden Tests (12 tests) | ✅ 完成 | 2026-06-02 |
| ⭐ | **ML Ensemble 训练/推理引擎** | ✅ 完成 | 2026-06-03 |
| ⭐ | **StrategyEngine 统一策略入口** | ✅ 完成 | 2026-06-03 |
| ⭐ | **ML hybrid 模拟盘上线** | ✅ 完成 | 2026-06-03 |
| ⭐ | **ML 周度自动训练 cron** | ✅ 完成 | 2026-06-03 |
| P4 🔵 | 生产监控（RankIC/基准对比） | 📋 待开始 | - |
