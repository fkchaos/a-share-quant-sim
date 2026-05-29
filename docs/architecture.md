# 代码架构讲解

> 面向开发者的实现逻辑与框架说明
>
> 最后更新：2026-05-30（core/ 统一重构后）

## 一、整体架构：共享引擎模式

```
                          core/ (唯一权威引擎)
  ┌─────────────────────────────────────────────────────────┐
  │  config.py    DEFAULT_FACTOR_WEIGHTS (29因子权重)        │
  │               TradingCosts + RiskLimits dataclass        │
  │  factors.py   calc_factors_panel() / calc_factors_single │
  │               29 技术因子计算                             │
  │  scoring.py   composite_score(panel) / score_all_stocks  │
  │               截面 Z-Score + 加权评分                     │
  │  account.py   PortfolioState + buy/sell/check_stop_loss  │
  │               纯函数式交易 API                             │
  │  position.py  Position 领域模型 (替代裸 dict)              │
  └──────────┬──────────────────────────────┬──────────────┘
             │                              │
             ▼                              ▼
  ┌─────────────────────┐    ┌─────────────────────────┐
  │  sim_daily_v6.py    │    │  run_backtest.py        │
  │  (模拟盘调度层)      │    │  (历史回测引擎)          │
  │                     │    │                         │
  │  1. 数据更新(subproc)│    │  1. 加载 CSV 面板        │
  │  2. load PortfolioSt│    │  2. calc_factors_panel() │
  │  3. 止损→check_stop  │    │  3. IC 分析(可选)        │
  │  4. 调仓→buy/sell    │    │  4. composite_score()    │
  │  5. 报告+持久化       │    │  5. 回测循环→buy/sell   │
  │                     │    │  6. 绩效指标计算          │
  └─────────────────────┘    └─────────────────────────┘

  ┌─────────────────────┐
  │ update_daily_data.py│
  │ (数据层)             │
  │ 腾讯 API → 本地 CSV  │
  └─────────────────────┘
```

**设计原则**：`core/` 是纯数据结构和函数 — 无 I/O、无副作用。模拟盘和回测是两个不同的事件循环，但共用同一套交易函数。因子权重唯一权威来源是 `core/config.py` 的 `DEFAULT_FACTOR_WEIGHTS`。

---

## 二、`core/` 层：四个模块详解

### 2.1 `config.py` — 配置管理

```python
@dataclass
class Config:
    costs: TradingCosts          # initial_capital, commission_rate, stamp_tax_rate, slippage_rate
    risk: RiskLimits             # stop_loss, top_n, rebalance_freq, max_single_weight
    factor_weights: Dict[str, float]   # 29 个因子权重
    strategies: Dict[str, StrategyConfig]  # 策略预设
    data_dir, daily_dir, ...     # 路径配置

# 全局单例（模块级加载）
config = load_config()  # 自动搜索 config.yaml
```

**使用方式**：`core.config.factor_weights` / `core.config.risk.stop_loss`

**优先级**：CLI 参数 > config.yaml > 内置默认值

### 2.2 `factors.py` — 因子计算引擎

双模式设计：相同的数学逻辑，不同的输入形状。

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

### 2.3 `account.py` — 纯函数式交易 API

```python
@dataclass
class Position:
    code: str; shares: int; cost_price: float; entry_date: str
    def add_shares(self, new_shares, price) -> 'Position':  # 加权平均成本
    def pnl(self, price) -> float:
    def to_dict(self) -> dict:          # 序列化兼容旧格式
    @staticmethod
    def from_dict(code, d) -> 'Position':  # 反序列化

@dataclass
class PortfolioState:
    cash: float
    initial_capital: float
    holdings: Dict[str, dict]  # {code: {shares, cost_price, entry_date}}
    trade_log: List[dict]
    nav_history: List[dict]

def buy(state, code, price, date, shares=None) -> PortfolioState:    # 返回新 state
def sell(state, code, price, date, reason='SELL') -> PortfolioState:
def check_stop_loss(state, date, prices) -> PortfolioState:
def portfolio_value(state, date, prices) -> float:
def status_report(state, date, prices) -> dict:
```

**买入流程**：等权分配 → 单只上限 → 滑点 → 100股整数倍 → 钱不够砍仓 → 加权平均成本。

**设计原则**：纯函数式 API，返回新 state，不修改旧 state。所有成本参数从 `core.config` 读取（全局单例）。

### 2.4 `scoring.py` — 评分合成

```python
def standardize(df):                         # 截面 Z-Score
def composite_score(factors, weights):        # 加权合成（回测 panel 模式）
def composite_score_equal(factors):           # 等权合成（v3 baseline 对比用）
def score_all_stocks(all_factors, weights):   # 评分（模拟盘单股模式）→ {code: score}
def rel_strength_adjust(all_factors, stocks): # 相对强度因子填充
```

**关键设计**：`composite_score(panel)` 用于回测（操作 DataFrame），`score_all_stocks(live)` 用于模拟盘（操作 dict）。两者使用相同的权重和标准化逻辑。

---

## 三、调度层：sim_daily_v6.py

### 每日运行流程

```
daily_operation() 的 13 个步骤：

 ①  更新行情数据   subprocess 调用 update_daily_data.py
 ②  加载账户状态   从 account.json → PortfolioState
 ③  确定最新日期   从 CSV 文件最后一行
 ④  构建价格序列   遍历所有 CSV → price_data (Series)
 ⑤  计算当前净值   core.account.portfolio_value()
 ⑥  持仓明细报告   代码/名称/股数/成本/现价/盈亏/权重
 ⑦  数据质量门禁   DataQualityAuditor.audit()
 ⑧  止损检查       core.account.check_stop_loss() → 触发则卖出
 ⑨  调仓判断       trade_count % rebalance_freq == 0?
     ├─ 是 → calc_factors → 评分排序 → 逐个 sell 不在目标的 → 逐个 buy 新的
     └─ 否 → 跳过
 ⑩  换手率控制     cap_daily_turnover (兼容 SimAccount / PortfolioState)
 ⑪  行业仓位上限   cap_industry_weights (单一行业 ≤25%)
 ⑫  保存状态      account.json + trade_count.txt
 ⑬  生成报告       NAV / 行业分布 / 指数趋势 / 明日操作计划
```

### 辅助模块

| 模块 | 用途 | 对应 P 级 |
|------|------|-----------|
| `constraints.py` | 涨跌停/T+1/停牌检查 | P0-1 |
| `data_quality.py` | 数据过期/空值/异常跳变 | P0-2 |
| `portfolio_controls.py` | 日换手率 ≤30% | P0-3 |
| `industry.py` | 行业分类 + 行业≤25% | P1-1 |
| `indices.py` | 6个指数趋势展示 | P1-2 |

---

## 四、数据层：增量更新机制

### 核心逻辑

```
①  读取本地 CSV，找到最后日期 local_latest
②  请求最近 N 天数据（N = 缺口 + 5，防止遗漏）
③  追加到原 CSV（不覆盖）
④  失败的等待 3 秒后重试
```

### 为什么用 requests 而不是 AKShare？

```
环境限制：
  ✅ baidu.com → 通
  ✅ gtimg.cn（腾讯）→ 通
  ❌ eastmoney（东方财富）→ RemoteDisconnected

腾讯 API 优势：不需要 token/签名/回调，请求简单
```

---

## 五、回测引擎：run_backtest.py

### 与模拟盘的一致性保证

```
sim_daily_v6.py ──▶ core.account.buy/sell/check_stop_loss
run_backtest.py  ──▶ core.account.buy/sell/check_stop_loss
                      ↑↑↑ 完全相同的函数 ↑↑↑
```

**这是整个项目最重要的设计决策**。之前存在两份独立的交易逻辑，可能导致回测结果与模拟盘不一致。现在只有一份 → 修一处生效两处。

### 支持的策略

| 策略 | weight_method | 核心差异 |
|------|-------------|---------|
| v3_baseline | weighted | FACTOR_WEIGHTS 加权，top_n=20, freq=5, sl=15% |
| v3_optimized | weighted | FACTOR_WEIGHTS 加权 + vol_scaling，top_n=12, freq=20, sl=20% |
| ic_ir_weighted | ic_ir | IC-IR 绝对值加权所有因子 |
| ic_selected | ic_ir | IC-IR 加权 + 仅保留有效因子（\|IC_IR\|≥0.03） |
| markowitz | equal+opt | 等权因子评分 + Markowitz 均值-方差优化权重 |

### 命令行接口

```bash
python run_backtest.py --strategy all --start 2021-01-01
python run_backtest.py --strategy v3_optimized --top-n 12 --rebalance-freq 20
python run_backtest.py --ic-analysis --scan
```

---

## 六、改进行动追踪

| 优先级 | 方向 | 状态 | 日期 |
|--------|------|------|------|
| P0 🔴 | 交易约束（涨跌停/停牌/T+1） | ✅ 完成 | 2026-05-29 |
| P0 🔴 | 数据质量门禁 | ✅ 完成 | 2026-05-29 |
| P0 🔴 | 换手率上限控制 | ✅ 完成 | 2026-05-29 |
| P1 🟠 | 行业仓位上限 | ✅ 完成 | 2026-05-29 |
| P1 🟠 | 指数趋势展示 | ✅ 完成 | 2026-05-29 |
| P2 🟡 | 参数配置抽离 (config.yaml) | ✅ 完成 | 2026-05-30 |
| P2 🟡 | 统一回测引擎 + IC 分析 | ✅ 完成 | 2026-05-30 |
| ⭐ | **core/ 统一（回测=模拟盘交易逻辑）** | ✅ 完成 | 2026-05-30 |
| ⭐ | **29 因子权重对齐（去除 boll_width_10）** | ✅ 完成 | 2026-05-30 |
| ⭐ | **score_all_stocks() 统一评分入口** | ✅ 完成 | 2026-05-30 |
| ⭐ | **废弃脚本清理（→ archive/）** | ✅ 完成 | 2026-05-30 |
| P3 🟢 | 可视化分析（净值曲线/IC时序图） | 📋 待开始 | - |
| P3 🟢 | 过拟合防护（因子相关性+样本外验证） | 📋 待开始 | - |
| P4 🔵 | 生产监控（信号数/RankIC/基准对比） | 📋 待开始 | - |

---

## 七、值得学习的编程模式

### 1. 防御性编程

```python
# 每一步都检查边界条件
if shares <= 0: return state  # no-op
if code not in state.holdings: return state  # no-op
if not pd.isna(p) and p > 0:   # 数据可能缺失
if len(close) >= w:             # 历史数据可能不够
```

金融代码**必须**防御性编程。数据永远不会完美。

### 2. subprocess 解耦

```python
subprocess.run([sys.executable, "update_daily_data.py"], ...)
```

用 `subprocess` 而不是 `import`：数据更新和交易逻辑完全独立。一个崩溃不影响另一个。

### 3. 函数式交易 API

```python
state = buy(state, code, price, date)  # 返回新 state，不修改旧 state
```

便于回测并行化和状态快照。

### 4. 配置驱动设计

```yaml
# config.yaml — 不碰代码，改这里
factor_weights:
  mom_20: 0.10      # 调这里
risk:
  stop_loss: 0.20   # 调这里
```

---

## 八、关键常量一览

全部集中在 `config.yaml`：

```yaml
costs:
  initial_capital: 1000000
  commission_rate: 0.0003
  stamp_tax_rate: 0.001
  slippage_rate: 0.001

risk:
  stop_loss: 0.20
  top_n: 10
  rebalance_freq: 20
  max_single_weight: 0.15
  max_daily_turnover: 0.30
```
