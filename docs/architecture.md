# 代码架构讲解

> 面向开发者的实现逻辑与框架说明
>
> 最后更新：2026-06-02（v5 策略上线 + 参数解耦后）

## 一、整体架构：共享引擎 + 策略 Profile 模式

```
                          core/ (唯一权威引擎)
  ┌─────────────────────────────────────────────────────────────────┐
  │  config.py                                                      │
  │    DEFAULT_FACTOR_WEIGHTS (29因子权重, sum=1.0)                  │
  │    StrategyConfig dataclass (所有策略参数)                        │
  │    STRATEGY_PROFILES dict (预定义策略: v4/v5)                     │
  │    TradingCosts + RiskLimits dataclass                          │
  │  factors.py    calc_factors_panel() / calc_factors_single()     │
  │                29 技术因子计算                                    │
  │  scoring.py    composite_score(panel) / score_all_stocks(live)  │
  │                截面 Z-Score + 加权评分                            │
  │  account.py    PortfolioState + buy/sell/check_stop_loss        │
  │                partial_sell / check_take_profit                  │
  │                apply_holding_decay / allocate_weights           │
  │                纯函数式交易 API                                    │
  │  position.py   Position 领域模型                                 │
  └──────────┬──────────────────────────────┬──────────────────────┘
             │                              │
             ▼                              ▼
  ┌──────────────────────┐   ┌─────────────────────────────┐
  │  sim_daily_v6.py     │   │  run_backtest.py            │
  │  (模拟盘 Pipeline)    │   │  (历史回测引擎)               │
  │                      │   │                             │
  │  策略来源:            │   │  策略来源:                    │
  │  STRATEGY_PROFILES   │   │  STRATEGY_PROFILES           │
  │  [_PROFILE]          │   │  + _build_cfg() helper       │
  │                      │   │                             │
  │  Pipeline (每日):     │   │  流程:                       │
  │  ① 更新数据           │   │  1. 加载 CSV 面板             │
  │  ② 加载账户+tp_taken │   │  2. calc_factors_panel()     │
  │  ③ 加载价格           │   │  3. IC 分析(可选)             │
  │  ④ 止损              │   │  4. composite_score()        │
  │  ⑤ 分级止盈(◉v5)     │   │  5. 回测循环→buy/sell        │
  │  ⑥ 持有期decay(◉v5)  │   │  6. 绩效指标+自检             │
  │  ⑦ 数据质量           │   │                             │
  │  ⑧ 调仓              │   │                             │
  │  ⑨ 保存+报告          │   │                             │
  └──────────────────────┘   └─────────────────────────────┘

  ┌──────────────────────┐
  │ update_daily_data.py │
  │ (数据层: 腾讯 API → CSV)│
  └──────────────────────┘
```

**设计原则**：
- `core/` 是纯数据结构和函数 — 无 I/O、无副作用
- `STRATEGY_PROFILES` 是策略参数唯一权威来源（回测+模拟盘共用）
- 因子权重唯一权威来源是 `core/config.py` 的 `DEFAULT_FACTOR_WEIGHTS`

---

## 二、`core/` 层：四个模块详解

### 2.1 `config.py` — 配置管理 + 策略 Profiles

```python
# 因子权重（29因子，sum=1.0）
DEFAULT_FACTOR_WEIGHTS = { 'mom_5': 0.05, ... }

# 策略参数 dataclass
@dataclass
class StrategyConfig:
    label: str
    weight_method: str          # equal | ic_ir | markowitz
    top_n: int
    rebalance_freq: int
    stop_loss: float
    max_industry_weight: float
    max_daily_turnover: float
    use_take_profit: bool       # v5
    tp_tiers: list              # v5: [(0.10,0.30),(0.20,0.30),(0.30,1.00)]
    use_holding_decay: bool     # v5

# 预定义策略 Profiles（回测+模拟盘共用）
STRATEGY_PROFILES = {
    "v4_baseline":      PROFILE_V4_BASELINE,          # 无限制基准
    "v4_industry_cap":  PROFILE_V4_WITH_INDUSTRY_CAP, # +行业限制25%
    "v5_tp_decay":      PROFILE_V5_TP_DECAY,          # +分级止盈+持有期decay
}
```

| Profile | 年化 | 夏普 | 回撤 | Calmar | 核心差异 |
|---------|------|------|------|--------|---------|
| v4_baseline | 24.82% | 1.11 | -28.87% | 0.86 | 无行业/换手率限制 |
| v4_industry_cap | 20.72% | 0.97 | -27.01% | 0.77 | +行业≤25% |
| **v5_tp_decay** ⚡ | **23.97%** | **1.37** | **-20.05%** | **1.20** | +分级止盈+持有期decay |

### 2.2 `factors.py` — 因子计算引擎

双模式设计：

```python
# 面板模式（回测）: DataFrame (dates × stocks) → {factor_name: DataFrame}
calc_factors_panel(close_panel, volume_panel, amount_panel)

# 单股模式（模拟盘）: DataFrame (single stock) → {factor_name: float}
calc_factors_single(df)
```

共 29 个因子，分 7 类：

| 类别 | 因子 |
|------|------|
| 动量 | mom_5, mom_10, mom_20, mom_60, mom_120 |
| 反转 | rev_3, rev_5, rev_10 |
| 波动率 | vol_10, vol_20, vol_60, vol_change |
| 成交量 | vol_ratio_5, vol_ratio_20, amount_ratio |
| RSI | rsi_6, rsi_14, rsi_28 |
| 趋势 | macd_12_26, macd_5_35, boll_pos_10, boll_pos_20, boll_width_20 |
| 统计/其他 | atr_14, skew_20, kurt_20, vwap_mom, rel_strength_20, rel_strength_60 |

**⚠️ 防错**：不传 `vol_panel` 时会有 `UserWarning`——之前 bug 的遗留防护。

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
def buy(state, code, price, date, shares=None) -> PortfolioState:
def sell(state, code, price, date, reason='SELL') -> PortfolioState:
def partial_sell(state, code, price, date, sell_fraction, reason) -> PortfolioState:

# 风控函数
def check_stop_loss(state, date, prices) -> PortfolioState:
def check_take_profit(state, date, prices, tiers=None) -> PortfolioState:
    # tiers: [(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)]
    # 每档只触发一次（通过 holdings[code]['dp_taken'] 追踪）
def apply_holding_decay(state, date, prices, rebalance_freq=20) -> PortfolioState:
    # >rf天→70%, >2rf天→40%

# 权重分配
def allocate_weights(top_stocks, price_data, method='equal', vol_series=None, max_position=0.10):
    # method: equal | vol_inverse | markowitz

def portfolio_value(state, date, prices) -> float:
```

**📝 holdings 结构变更 (v5)**：每只股票持仓新增 `tp_taken` 字段（list of float），记录已触发的止盈档位，防止重复触发。

### 2.4 `scoring.py` — 评分合成

```python
def standardize(df):                                    # 截面 Z-Score + MAD 去极值
def composite_score(factors, weights):                  # 加权合成（回测 panel 模式）
def score_all_stocks(all_factors, weights):             # 评分（模拟盘单股模式）→ {code: score}
def factor_correlation(factors):                        # 因子相关性矩阵 + 高相关对检测
```

---

## 三、调度层：sim_daily_v6.py

### 每日 Pipeline（9 步）

```
daily_operation():
  ① step_update_data()           subprocess 调用 update_daily_data.py
  ② step_load_account()          从 account.json → PortfolioState（含 tp_taken 兼容）
  ③ step_load_prices()           遍历 CSV → price_data (Series)
  ④ step_check_stop_loss()       check_stop_loss() → 触发则卖出
  ⑤ step_check_take_profit()  ◉  check_take_profit() → 分级卖出（仅 v5）
  ⑥ step_holding_decay()      ◉  apply_holding_decay() → 减持（仅 v5）
  ⑦ step_data_quality()          DataQualityAuditor.audit()
  ⑧ step_rebalance()             因子计算 → 评分 → 换手率控制 → 行业限制 → 补仓/买入
  ⑨ step_save_state() + step_report() + step_tomorrow_plan()
```

**策略切换**：修改 `_PROFILE = "v5_tp_decay"` 即可（v4_baseline | v4_industry_cap | v5_tp_decay）。

### 辅助模块

| 模块 | 用途 | 对应 P 级 |
|------|------|-----------|
| `constraints.py` | 涨跌停/T+1/停牌检查 | P0-1 |
| `data_quality.py` | 数据过期/空值/异常跳变 | P0-2 |
| `portfolio_controls.py` | 日换手率上限控制 | P0-3 |
| `industry.py` | 行业分类 + 行业≤25% | P1-1 |
| `indices.py` | 6个指数趋势展示 | P1-2 |

---

## 四、回测引擎：run_backtest.py

### 与模拟盘的一致性保证

```
sim_daily_v6.py ──▶ core.account.buy / sell / check_stop_loss / check_take_profit / apply_holding_decay
run_backtest.py  ──▶ core.account.buy / sell / check_stop_loss / check_take_profit / apply_holding_decay
                      ↑↑↑ 完全相同的函数 ↑↑↑
```

**这是整个项目最重要的设计决策**。之前存在两份独立的交易逻辑，现在只有一份。

### 策略运行方式

```python
# 从 STRATEGY_PROFILES 构建 config（命令行参数可覆盖）
def _build_cfg(profile, score, extra_kwargs=None):
    kw = dict(top_n=..., rebalance_freq=..., stop_loss=...,
              use_take_profit=profile.use_take_profit,
              tp_tiers=profile.tp_tiers, ...)
    return {'label': profile.label, 'score': score, 'kwargs': kw}
```

### 命令行接口

```bash
# 跑所有策略
python scripts/run_backtest.py --strategy all --start 2021-01-01

# 跑单个策略
python scripts/run_backtest.py --strategy v5_tp_decay --top-n 12 --rebalance-freq 20

# 参数扫描 + Walk-Forward
python scripts/run_backtest.py --strategy v5_tp_decay --scan --walk-forward

# 自动记录结果到 RESULTS_LOG.md
python scripts/run_backtest.py --strategy v5_tp_decay --log
```

### 防错机制

- **vol_panel 缺失 warning**：`calc_factors_panel(close_panel)` 不传 vol_panel 时触发
- **stock_names 加载独立函数**：失败时有 warning，不静默影响行业限制
- **启动配置摘要**：每次回测打印 ind_cap/tp/decay/atr 状态
- **结果自检**：负收益/超大回撤/零止损触发时主动告警

---

## 五、数据层：增量更新机制

```
① 读取本地 CSV，找到最后日期 local_latest
② 请求最近 N 天数据（N = 缺口 + 5，防止遗漏）
③ 追加到原 CSV（不覆盖）
④ 失败的等待 3 秒后重试
```

**数据路径**：
- 本地：`/root/data/` (data/daily/*.csv)
- 回测：需设置 `BACKTEST_DATA_DIR=/root/data`
- 模拟盘脚本：用 `data/` 目录（相对于 repo 根目录）

---

## 六、Golden Tests

```
tests/test_golden.py — 12 个快速测试（< 1s）
  TestFactorComputation (5):  29因子完整性、权重和=1.0、vol_panel有/无检测
  TestScoring (2):           评分分布正确性、score_all_stocks 输出
  TestAccountLogic (4):      分级止盈触发、持有期decay、等权分配
  TestImplicitDependencyGuard (1): vol_panel=None → warning
  TestGoldenBaseline (1, slow): 端到端回测基准值验证

运行: python -m pytest tests/test_golden.py -v -k "not slow"
```

---

## 七、关键常量一览

| 常量 | 值 | 位置 |
|------|-----|------|
| 初始资金 | 1,000,000 | `core.config.risk.initial_capital` |
| 佣金率 | 0.03% | `core.config.costs.commission_rate` |
| 印花税 | 0.1% | `core.config.costs.stamp_tax_rate` |
| 滑点 | 0.1% | `core.config.costs.slippage_rate` |
| 因子数 | 29 | `core.config.DEFAULT_FACTOR_WEIGHTS` |
| 因子权重和 | 1.0 | `core.config.DEFAULT_FACTOR_WEIGHTS` |
| 止盈档位 | [(0.10,0.30),(0.20,0.30),(0.30,1.00)] | `PROFILE_V5_TP_DECAY.tp_tiers` |
| 数据 API | `web.ifzq.gtimg.cn` | `update_daily_data.py` |

---

## 八、改进行动追踪

| 优先级 | 方向 | 状态 | 日期 |
|--------|------|------|------|
| P0 🔴 | 交易约束（涨跌停/停牌/T+1） | ✅ 完成 | 2026-05-29 |
| P0 🔴 | 数据质量门禁 | ✅ 完成 | 2026-05-29 |
| P0 🔴 | 换手率上限控制 | ✅ 完成 | 2026-05-29 |
| P1 🟠 | 行业仓位上限 | ✅ 完成 | 2026-05-29 |
| P1 🟠 | 指数趋势展示 | ✅ 完成 | 2026-05-29 |
| ⭐ | core/ 统一（回测=模拟盘交易逻辑） | ✅ 完成 | 2026-05-30 |
| ⭐ | 29 因子权重对齐 | ✅ 完成 | 2026-05-30 |
| ⭐ | 废弃脚本清理（→ archive/） | ✅ 完成 | 2026-05-30 |
| ⭐ | **分级止盈 (check_take_profit)** | ✅ 完成 | 2026-06-02 |
| ⭐ | **持有期 decay (apply_holding_decay)** | ✅ 完成 | 2026-06-02 |
| ⭐ | **策略参数解耦 (STRATEGY_PROFILES)** | ✅ 完成 | 2026-06-02 |
| ⭐ | **Golden Tests (12 tests)** | ✅ 完成 | 2026-06-02 |
| ⭐ | **RESULTS_LOG 自动记录** | ✅ 完成 | 2026-06-02 |
| P2 🟡 | 参数配置抽离 | ✅ 完成 | 2026-05-30 |
| P3 🟢 | Walk-Forward 滚动验证 | ✅ 完成 | 2026-05-30 |
| P3 🟢 | Sortino + 因子相关性分析 | ✅ 完成 | 2026-05-30 |
| P4 🔵 | 日志系统 + Pipeline 重构 | ✅ 完成 | 2026-05-30 |
| P4 🔵 | 生产监控（RankIC/基准对比） | 📋 待开始 | - |
