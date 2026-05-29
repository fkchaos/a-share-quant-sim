# 代码架构讲解

> 面向开发者的实现逻辑与框架说明
>
> 最后更新：2026-05-30（反映 core/ 解耦重构）

## 一、整体架构：共享引擎模式

```
┌──────────────────────────────────────────────────────────────┐
│                      config.yaml                              │
│  factor_weights / risk / costs / strategies / param_scan      │
└──────────┬─────────────────────────────────┬────────────────┘
           │ 加载                             │ 加载
           ▼                                 ▼
┌──────────────────────────────────────────────────────────────┐
│                         core/                                │
│  config.py   ← yaml → Config dataclass (唯一参数源)           │
│  position.py ← Position 领域模型 (替代裸 dict)                 │
│  factors.py  ← Factor Registry (29因子 Strategy pattern)     │
│  account.py  ← PortfolioState + buy/sell/stop_loss (参数注入) │
│  scoring.py  ← Z-score + composite_score                     │
└──────────┬───────────────────────────────┬──────────────────┘
           │                               │
           ▼                               ▼
┌─────────────────────┐      ┌─────────────────────────┐
│  sim_daily_v6.py    │      │  run_backtest.py        │
│  (模拟盘调度层)      │      │  (历史回测引擎)          │
│                     │      │                         │
│  1. 数据更新 (subprocess) │  1. 加载已有 CSV 面板    │
│  2. 加载 PortfolioState │  2. calc_factors_panel() │
│  3. 止损 → check_stop_loss│ 3. IC 分析 (可选)       │
│  4. 调仓 → buy/sell      │ 4. 评分合成 → composite  │
│  5. 报告 + 持久化         │ 5. 回测循环 → buy/sell  │
│                     │      │ 6. 绩效指标计算          │
└─────────────────────┘      └─────────────────────────┘

┌─────────────────────┐
│ update_daily_data.py│
│ (数据层)             │
│ 腾讯 API → 本地 CSV  │
└─────────────────────┘
```

**设计原则**：`core/` 是纯数据结构和函数 — 无 I/O、无副作用。模拟盘和回测是两个不同的事件循环，但共用同一套交易函数。

**类比**：开一家连锁餐厅

- **core/** = 总部厨房（统一烹饪配方，所有分店共用）
- **sim_daily_v6.py** = 直营店（每天用配方做菜、服务顾客）
- **run_backtest.py** = 厨房实验室（用配方反复测试新菜品）
- **update_daily_data.py** = 采购部门（统一采购食材）

---

## 二、`core/` 层：四个模块详解

### 2.1 `config.py` — 配置管理

```python
@dataclass
class Config:
    costs: TradingCosts          # initial_capital, commission_rate, stamp_tax_rate, slippage_rate
    risk: RiskLimits             # stop_loss, top_n, rebalance_freq, max_single_weight
    factor_weights: Dict[str, float]   # 31 个因子权重
    strategies: Dict[str, StrategyConfig]  # 策略预设
    data_dir, daily_dir, ...     # 路径配置

# 全局单例（模块级加载）
config = load_config()  # 自动搜索 config.yaml
```

**使用方式**：`core.config.factor_weights` / `core.config.risk.stop_loss`

**优先级**：CLI 参数 > config.yaml > 内置默认值

### 2.2 `factors.py` — 因子计算

支持两种 I/O 模式，数学逻辑完全一致：

```python
# 模式1: 信号模式（模拟盘用）
def calc_factors_single(df: DataFrame) -> Dict[str, float]:
    """输入: 单只股票的 OHLCV DataFrame → 输出: {mom_20: 0.05, rsi_14: 65.2, ...}"""

# 模式2: 面板模式（回测用，向量化加速 100x）
def calc_factors_panel(close_panel, volume_panel, amount_panel) -> Dict[str, DataFrame]:
    """输入: (N天 × M股) DataFrame → 输出: {mom_20: DataFrame(N×M), ...}"""
```

**31 个因子全景**：

| 类别 | 因子 | 计算逻辑 |
|------|------|----------|
| 动量 (5) | mom_5/10/20/60/120 | `close[-1]/close[-N] - 1` |
| 反转 (3) | rev_3/5/10 | `-(mom_N)` |
| 波动率 (4) | vol_10/20/60, vol_change | `returns[-N:].std()` + 短/长波比 |
| 成交量 (3) | vol_ratio_5/20, amount_ratio | `vol[-1] / vol[-N:].mean()` |
| RSI (3) | rsi_6/14/28 | `100 - 100/(1 + avg_gain/avg_loss)` |
| 趋势 (5) | macd_12_26, macd_5_35, boll_pos_10/20, boll_width_20 | EMA 差 + 布林带位置/宽度 |
| 统计 (4) | skew_20, kurt_20, atr_14, vwap_mom | 偏度、峰度、ATR、VWAP 动量 |
| 相对强度 (2) | rel_strength_20/60 | `mom_N - cross_sectional_mean_mom_N` |

### 2.3 `account.py` — 交易状态与操作

**PortfolioState 数据类**（不可变风格，每笔交易返回新副本）：

```python
@dataclass
class PortfolioState:
    cash: float
    initial_capital: float
    holdings: Dict[str, dict]   # {code: {shares, cost_price, entry_date}}
    trade_log: List[dict]       # 完整审计日志
    nav_history: List[dict]     # 净值历史

    def copy(self) -> 'PortfolioState'
```

**纯函数式交易 API**：

```python
state = buy(state, code, price, date, shares=None)    # → 新 state
state = sell(state, code, price, date, reason='SELL') # → 新 state
state = check_stop_loss(state, date, price_data)       # → 新 state
value = portfolio_value(state, date, price_data)      # → float
```

**为什么用函数式而非 OOP？**
- 便于回测引擎的无副作用循环
- 方便并行回测（每个线程独立的 state 副本）
- 避免隐式状态修改导致的 debug 困难

**买入逻辑的工程设计**：

```
第1步: 等权分配 → target_value = cash / n_stocks
第2步: 单只上限 → min(target_value, cash × 12%)
第3步: 加入滑点 → adj_price = price × 1.001
第4步: A股规则 → 100股整数倍 (1手)
第5步: 钱不够 → 砍仓位重试
第6步: 加仓 → 加权平均成本
```

**卖出逻辑的关键细节**：
- 普通卖出：佣金 + 印花税（买卖都交）
- 止损卖出：仅佣金（模拟中印花税豁免 — A 股实际也收，这是个可修正的差异点）

### 2.4 `scoring.py` — 评分合成

```python
def standardize(df: DataFrame) -> DataFrame:
    """横截面 Z-Score: z = (val - mean) / std"""

def composite_score(factors: dict, weights: dict) -> DataFrame:
    """加权合成: Σ weight × z_score(factor)"""

def composite_score_equal(factors: dict) -> DataFrame:
    """等权合成: 每个因子 1/N 权重"""
```

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
| v3_baseline | equal | 31 因子等权，top_n=20, rebal=5 |
| v3_optimized | equal | 等权 + 波动率目标化 |
| ic_ir_weighted | ic_ir | IC-IR 加权因子 |
| ic_selected | ic_ir | IC-IR 加权 + 仅保留有效因子 |
| markowitz | markowitz | Markowitz 均值-方差优化 |

### 命令行接口

```bash
python run_backtest.py --ic-analysis --scan  # 最全比较
python run_backtest.py --config my.yaml      # 自定义配置
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
| P3 🟢 | 统一回测工具 + 测试套件 | ✅ 完成 | 2026-05-30 |
| ⭐ | **core/ 解耦（回测=模拟盘交易逻辑）** | ✅ 完成 | 2026-05-30 |
| P3 🟢 | 单元测试（持续扩充） | 🔧 进行中 | - |
| P4 🔵 | 日志系统（print → logging） | 📋 待开始 | - |

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
