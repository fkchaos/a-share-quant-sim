# A 股量化项目调研报告

> 调研日期：2026-05-29
> 调研目标：分析 GitHub 上同类 A 股量化项目的策略差异，找出可借鉴的优化方向

## 一、调研范围

| 项目 | Stars | 定位 |
|------|-------|------|
| [Neyray/a_share_quant](https://github.com/Neyray/a_share_quant) | ⭐ 新 | 4策略并行模拟盘，多模块工程化 |
| [yutiansut/QUANTAXIS](https://github.com/quantaxis/quantaxis) | ⭐ 10.6k | 全栈量化框架（数据/回测/交易/可视化） |
| [akfamily/akshare](https://github.com/akfamily/akshare) | ⭐ 19.8k | 金融数据接口库 |
| [zvtvz/zvt](https://github.com/zvtvz/zvt) | ⭐ 4.1k | 模块化量化框架 |
| [mpquant/Ashare](https://github.com/mpquant/Ashare) | ⭐ 3.5k | 极简 A 股数据接口 |

**重点分析对象：Neyray/a_share_quant**（同属个人量化模拟盘，架构思路最接近）

---

## 二、Neyray 项目架构

```
a_share_quant/
├── quant_sim/               # 核心引擎（~20个模块）
│   ├── cli.py              # 统一命令行入口
│   ├── config.py           # 配置管理
│   ├── data.py             # 行情数据（多源 fallback）
│   ├── data_quality.py     # 数据质量门禁
│   ├── indicators.py       # 技术指标计算
│   ├── universe.py         # 动态股票池构建
│   ├── constraints.py      # A股交易约束（涨跌停/停牌/T+1）
│   ├── portfolio_controls.py  # 组合换手率上限
│   ├── indices.py          # 指数趋势判断（HS300/CSI500/CSI1000）
│   ├── industry.py         # 行业分类 + 行业强度因子
│   ├── monitoring.py       # 生产监控（信号数/RankIC/基准对比）
│   ├── strategy.py         # 趋势动量策略
│   ├── research.py         # 中期研究因子策略
│   ├── multifactor.py      # 横截面多因子策略
│   ├── ml_strategy.py      # 滚动岭回归 ML 策略
│   ├── broker.py           # 虚拟账户/订单/TradeContext
│   ├── backtest.py         # 回测引擎
│   └── paper.py            # 模拟盘执行
├── scripts/                # cron shell 脚本
├── data/
│   ├── cache/              # 行情/指数/行业缓存
│   └── state/              # 4个独立账户 JSON
└── reports/                # 日报 / 信号 / 回测 / 监控
```

### 四策略并行对比

| 维度 | default（趋势） | research（研究） | multifactor（多因子） | ml（机器学习） |
|------|----------------|-----------------|----------------------|--------------|
| 股票池 | 15只手写白马股 | 动态全市场 ~200只 | 动态全市场 ~200只 | 15只手写 |
| 信号源 | MA + 动量 + 波动率 | 多维度因子评分 | 横截面 z-score 排名 | 岭回归预测5日收益 |
| 调仓频率 | 2次/日 + 收盘结算 | 1次/日 15:20 | 1次/日 15:30 | 1次/日 15:40 |
| 风控特色 | 硬止损12% + 止盈35% | 市场状态过滤 + 行业仓位上限 | 三类监控熔断 + 换手率上限 | - |

---

## 三、逐模块代码对比：我们的差距在哪

### 3.1 ✅ 对齐的部分

这些我们做到了，且质量不输：

| 模块 | 我们的实现 | Neyray 的实现 | 对比 |
|------|-----------|--------------|------|
| **因子计算** | 31个技术因子（mom/rev/vol/rsi/macd/boll/skew 等） | ~11个横截面因子 + 自定义动量 + RSI | ✅ 我们的因子更丰富 |
| **标准化** | Z-Score：(val-mean)/std，全市场截面 | 同样 Z-Score | ✅ 完全对齐 |
| **评分加权** | 等权合成 | 等权或 inverse_vol 加权 | ✅ 各有优劣 |
| **止损** | 单只 -20% 硬止损 | 单只 -12% 硬止损 + -35% 止盈 | ✅ 我们更保守 |
| **交易成本** | 佣金 0.03% + 印花税 0.1% + 滑点 0.1% | 佣金 + 印花税 + 滑点 | ✅ 完全对齐 |
| **状态持久化** | JSON 序列化账户状态 | JSON 序列化账户状态 | ✅ 完全对齐 |
| **调仓频率** | 每 20 个交易日 | 每日 1-2 次 | ✅ 我们换手更低，更适合个人 |

### 3.2 ❌ 缺失的部分（工程完备性差距）

以下模块我们**完全没有**，Neyray 有完整实现：

#### 差距 1：A 股交易约束（最重要）

**我们的代码：** 调仓时直接买入/卖出，假设任何股票在任何交易日都能成交。

```python
# 我们的 sim_daily.py 调仓逻辑：直接用收盘价买卖
account.sell(code, price, latest_date, 'SELL')    # 假设一定能卖掉
account.buy(code, price, latest_date)               # 假设一定能买到
```

**问题：** A 股有涨跌停制度。涨停时一字板买不到，跌停时一字板卖不出，停牌时完全无法交易。这意味着回测结果过于乐观。

**Neyray 的代码：** 每只股票有一个 `TradeContext`，在买卖前检查是否可交易：

```python
@dataclass(frozen=True)
class TradeContext:
    symbol: str
    day: str
    limit_up: float              # 涨停价（从昨收计算）
    limit_down: float            # 跌停价
    suspended: bool              # 是否停牌
    is_one_word_limit_up: bool    # 一字涨停（封板，买不到）
    is_one_word_limit_down: bool  # 一字跌停（封板，卖不出）

    def is_buy_blocked(self) -> tuple[bool, str]:
        if self.is_one_word_limit_up:
            return True, "one_word_limit_up"
        if self.limit_up is not None and self.close >= self.limit_up - 1e-6:
            return True, "limit_up"
        return False, ""

    def is_sell_blocked(self) -> tuple[bool, str]:
        if self.is_one_word_limit_down:
            return True, "one_word_limit_down"
        if self.limit_down is not None and self.close <= self.limit_down + 1e-6:
            return True, "limit_down"
        return False, ""
```

**影响：** 回测中每次调仓可能有 5-15% 的股票因涨跌停无法成交。没有这个模块，年化收益可能虚高 2-5%。

#### 差距 2：数据质量门禁

**我们的代码：** 更新完数据后直接用来计算因子，没有检查数据是否可用。

```python
# 我们的 update_daily_data.py：更新完就结束了
# 如果某只股票数据过期、停牌、除权除息导致价格跳变——
# 没有任何检查，直接进入因子计算
```

**问题：** 如果某只股票停牌了（成交量为0），或者除权除息导致价格跳变 30%，这些异常数据会产生错误因子值，污染评分。

**Neyray 的代码：** 独立的 `DataQualityAuditor`，在交易逻辑前运行：

```python
class DataQualityAuditor:
    def audit(self, symbols, as_of):
        # 1. 最新数据是否 >5 天前（过期检查）
        # 2. 最近 60 天是否有空 close/volume
        # 3. 单日涨跌幅是否 >11%（主板异常阈值）
        # 4. 复权价格跳变是否 >30%（除权除息异常）
        # 输出: approved / risk_level / blocking_issues / warnings
```

**影响：** 没有质量门禁，脏数据会悄悄影响策略信号。在极端市场（如 2015 年股灾期间大量停牌）会产生严重偏差。

#### 差距 3：组合换手率上限

**我们的代码：** 每次调仓时全部卖出不在目标中的股票，全部买入目标股票。

```python
# 我们的调仓逻辑：不控制换手
# 每次调仓可能换手 50-80%（卖出大部分 + 买入新的）
to_sell = [c for c in holdings if c not in top_stocks]
for code in to_sell:
    account.sell(code, price, date)     # 全部卖出
for code in new_targets:
    account.buy(code, price, date)       # 全部买入新的
```

**问题：** 高换手意味着高交易成本。在回测中成本低估，在实盘中会严重侵蚀利润。

**Neyray 的代码：** 每次调仓后检查换手率，超限则等比缩减：

```python
def cap_daily_turnover(account, target_weights, prices, max_turnover=0.25):
    # 计算当前持仓权重
    current = {symbol: pos.shares * price / equity}
    # 计算目标与当前的偏差
    deltas = {symbol: target - current}
    # 总换手 = Σ|delta|
    requested = sum(abs(d) for d in deltas.values())
    if requested > max_turnover:
        # 等比缩放，使总换手 = max_turnover
        scale = max_turnover / requested
        adjusted = {symbol: current + delta * scale}
```

**影响：** 回测中不控制换手，年化收益虚高 1-3%。

#### 差距 4：行业仓位上限

**我们的代码：** 选股时没有行业维度控制。如果某段时间策略集中选了半导体行业，行业风险无约束。

```python
# 我们的选股逻辑：只看分数，不看行业
scores = generate_scores()
sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
top_stocks = [code for code, _ in sorted_scores[:TOP_N]]
# 可能出现 10只全是半导体行业的情况
```

**影响：** 行业集中度过高。如果该行业遭遇政策风险（如 2021 年教育行业），整个组合会遭受重大回撤。

**Neyray 的代码：**

```python
def cap_industry_weights(target_weights, industry_map, max_weight=0.25):
    industry_weights = {}
    for symbol, weight in target_weights.items():
        industry = industry_map.get(symbol)
        industry_weights[industry] += weight
    for industry, total_weight in industry_weights.items():
        if total_weight > max_weight:
            scale = max_weight / total_weight
            # 超限行业内的股票权重等比压缩
```

#### 差距 5：指数趋势判断

**我们的代码：** 没有市场状态的宏观判断。

**Neyray 的代码：** 独立模块缓存 HS300/CSI500/CSI1000 的日线，输出各指数 MA20/60/120 状态。不改变策略仓位，仅在日报中作为参考信息展示。

**影响：** 我们不择时，纯靠因子。在市场极端环境下（如单边下跌），没有宏观视角来理解策略为何失效。

#### 差距 6：生产监控（熔断机制）

**我们的代码：** 没有。

**Neyray 的代码：** 三个独立的监控器，读取历史报告产物（不发起网络请求）：

```python
class SignalCountMonitor:
    # 今日候选数远低于近 20 日中位数 → 报警
    # （可能数据质量出了问题）

class RankICMonitor:
    # 滚动 60 日 RankIC <= -0.02 → 报警
    # （因子在失效，暂停新买入）

class BenchmarkRelativeMonitor:
    # 近 20 日跑输 HS300 超过 3% → 报警
    # （策略相对基准在恶化）
```

**影响：** 没有监控，策略失效后不会自动降低风险敞口。

---

## 四、改进路线图

### 第 1 周：补必须有的

| 天数 | 模块 | 具体内容 |
|------|------|---------|
| 2-3 天 | **A 股交易约束** | TradeContext + 涨跌停/停牌检查 + T+1 |
| 1-2 天 | **数据质量门禁** | 过期检查 + 空值检查 + 异常涨跌检查 |

### 第 2 周：补推荐的

| 天数 | 模块 | 具体内容 |
|------|------|---------|
| 1 天 | **指数趋势展示** | 采集 5-6 个指数 ETF 日线 + 日报展示 |
| 2 天 | **行业仓位上限** | 行业分类数据 + 仓位上限约束 + 超限缩放 |
| 0.5 天 | **换手率控制** | cap_daily_turnover + 日报展示 |

### 第 3 周：验证

| 天数 | 内容 |
|------|------|
| 2 天 | 新模块基础上回测 2024-2025 |
| 1 天 | 对比 v3 vs v8 策略表现 |

---

## 五、不推荐照搬的

| 特性 | 原因 |
|------|------|
| **ML 策略（岭回归）** | A 股日频噪声大，ML 容易过拟合，需充分回测 |
| **每日多次调仓** | 增加复杂度，个人项目没必要 |
| **BTC/ETH Agent** | 与 A 股无关，不要混在一个仓库 |

---

## 六、总结

我们的 v3 策略核心（因子设计 + 评分 + 止损）已经不输 Neyray 的 multifactor 策略。

**真正的差距在工程完备性：**

| 差距 | 影响 | 是否必须 |
|------|------|---------|
| 涨跌停/停牌约束 | 回测收益虚高 2-5% | ✅ 必须 |
| 数据质量门禁 | 脏数据污染信号 | ✅ 必须 |
| 换手率控制 | 回测收益虚高 1-3% | ✅ 必须 |
| 行业仓位上限 | 行业集中风险 | 🟠 推荐 |
| 指数趋势展示 | 市场状态感知 | 🟠 推荐 |
| 生产监控 | 策略失效自动降仓 | 🟡 可选 |

补完 P0 的三个模块后，回测结果会更可信。再补 P1-P2，系统就从"能跑的原型"变成"可靠的模拟交易系统"。
