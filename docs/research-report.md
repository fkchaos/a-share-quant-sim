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

## 二、Neyray 项目架构分析

### 2.1 核心设计理念：多策略并行对比

```
a_share_quant/
├── quant_sim/               # 核心引擎（~20个模块）
│   ├── cli.py              # 统一命令行入口
│   ├── config.py           # 配置管理
│   ├── data.py             # 行情数据（多源 fallback）
│   ├── data_quality.py     # ★ 数据质量门禁
│   ├── indicators.py       # 技术指标计算
│   ├── universe.py         # ★ 动态股票池构建
│   ├── constraints.py      # ★ A股交易约束（涨跌停/停牌/T+1）
│   ├── portfolio_controls.py  # ★ 组合换手率控制
│   ├── indices.py          # ★ 指数趋势判断
│   ├── industry.py         # ★ 行业分类与行业强度
│   ├── monitoring.py       # ★ 生产监控（信号数/RankIC/基准对比）
│   ├── strategy.py         # 趋势动量策略
│   ├── research.py         # 中期研究因子策略
│   ├── multifactor.py      # 横截面多因子策略
│   ├── ml_strategy.py      # 滚动岭回归 ML 策略
│   ├── broker.py           # 虚拟账户/订单/TradeContext
│   ├── backtest.py         # 回测引擎
│   └── paper.py            # 模拟盘执行
├── scripts/                # cron shell 脚本
├── data/
│   ├── cache/              # 行情缓存
│   └── state/              # 4个独立账户 JSON
└── reports/                # 日报 / 信号 / 回测 / 监控
```

### 2.2 四策略对比

| 维度 | default（趋势） | research（研究） | multifactor（多因子） | ml（机器学习） |
|------|----------------|-----------------|----------------------|--------------|
| 股票池 | 15只手写白马股 | 动态全市场 ~200只 | 动态全市场 ~200只 | 15只手写 |
| 信号源 | MA + 动量 + 波动率 | 多维度因子评分 | 横截面 z-score 排名 | 岭回归预测5日收益 |
| 调仓频率 | 2次/日 + 收盘结算 | 1次/日 15:20 | 1次/日 15:30 | 1次/日 15:40 |
| 风控特色 | 硬止损12% + 止盈35% | 市场状态过滤 + 行业仓位上限 | 三类监控熔断 + 换手率上限 | - |
| 账户隔离 | default_account.json | research_account.json | multifactor_account.json | ml_account.json |

---

## 三、关键差异分析（vs 我们的 v3 策略）

### 3.1 我们已有的（对齐项）

| 能力 | 我们的实现 | Neyray 的实现 | 评价 |
|------|-----------|--------------|------|
| 多因子评分 | ✅ 31个因子 | ✅ ~11个横截面因子 | 我们的因子更丰富 |
| Z-Score 标准化 | ✅ 全市场截面 | ✅ 全市场截面 | 对齐 |
| 止损 | ✅ -20% 硬止损 | ✅ -12% 硬止损 | 我们更保守 |
| 调仓频率 | ✅ 20日 | ✅ 每日/2次 | 我们换手更低 |
| 等权分配 | ✅ 等权 | ✅ inverse_vol 加权 | 各有优劣 |
| 交易成本 | ✅ 佣金+印花税+滑点 | ✅ 佣金+印花税+滑点 | 对齐 |
| 状态持久化 | ✅ JSON | ✅ JSON | 对齐 |

### 3.2 我们没有的（差距项）★

| 能力 | 我们的状态 | Neyray 的实现 | 价值评估 |
|------|-----------|--------------|---------|
| **A股交易约束** | ❌ 无 | ✅ TradeContext: 涨跌停/停牌/一字板/T+1 | 🔴 **高** |
| **动态股票池** | ❌ 固定沪深300 | ✅ 实时行情过滤 ST/价格/成交额 | 🔴 **高** |
| **数据质量门禁** | ❌ 无 | ✅ 缓存巡检: 过期/空值/异常涨跌/复权异常 | 🔴 **高** |
| **生产监控** | ❌ 无 | ✅ 信号数/RankIC/基准对比 三类熔断 | 🟠 **中高** |
| **行业仓位控制** | ❌ 无 | ✅ 行业分类 + 行业强度因子 + 仓位上限 | 🟠 **中高** |
| **指数趋势判断** | ❌ 无 | ✅ HS300/CSI500/CSI1000 MA状态 | 🟠 **中高** |
| **组合换手率控制** | ❌ 无 | ✅ 单日换手上限缩放 | 🟡 **中** |
| **多策略并行** | ❌ 单策略 | ✅ 4策略独立账户对比 | 🟡 **中** |
| **ML 策略** | ❌ 无 | ✅ 滚动岭回归预测 | 🟡 **中**（需回测验证） |
| **CLI 工具** | ❌ 直接 python | ✅ 统一命令行入口 + 子命令 | 🟢 **工程化** |
| **配置管理** | ❌ 硬编码 | ✅ config.json + example | 🟢 **工程化** |

---

## 四、可借鉴的优化方向（按优先级）

### P0 🔴 必须补：A 股交易约束模块

**为什么最重要？**

我们的策略在回测中**假设任何股票在调仓日都能买卖**，但实际上：
- 涨停的股票买不到（一字板）
- 跌停的股票卖不出
- 停牌的股票完全无法交易
- T+1 制度：当天买入的当天不能卖出

这意味着我们的回测结果**过于乐观**——在实际调仓日，可能有一部分股票因为涨跌停/停牌而无法成交，导致实际持仓偏离目标。

**Neyray 的实现方式：**

```python
@dataclass(frozen=True)
class TradeContext:
    symbol: str
    day: str
    limit_up: float          # 涨停价
    limit_down: float        # 跌停价
    suspended: bool          # 是否停牌
    is_one_word_limit_up: bool    # 一字涨停（封板）
    is_one_word_limit_down: bool  # 一字跌停
    
    def is_buy_blocked(self) -> tuple[bool, str]:
        if self.is_one_word_limit_up:
            return True, "one_word_limit_up"    # 一字涨停买不到
        if self.close >= self.limit_up:
            return True, "limit_up"             # 涨停价难买到
        return False, ""
    
    def is_sell_blocked(self) -> tuple[bool, str]:
        if self.is_one_word_limit_down:
            return True, "one_word_limit_down"   # 一字跌停卖不出
        if self.close <= self.limit_down:
            return True, "limit_down"            # 跌停价难卖出
        return False, ""
```

**我们的实现路径：**
1. 在 `update_daily_data.py` 采集涨跌停价格（腾讯接口返回）
2. 在 `sim_daily.py` 调仓前增加 tradability 检查
3. 对无法买入的股票跳过并记录，对无法卖出的保留在持仓

预计工作量：**2-3 天**

### P1 🔴 必须补：数据质量门禁

**为什么重要？**

当前脚本如果遇到以下情况会静默产生错误信号：
- 某只股票出现停牌（成交量为0或NaN）
- 除权除息导致价格跳变（复权异常）
- 数据更新失败但脚本继续运行

Neyray 的方案很优雅：
```python
class DataQualityAuditor:
    def audit(self, symbols, as_of):
        # 1. 检查每个 symbol 最新日期是否 >5 天前（过期）
        # 2. 检查最近60天是否有空 close/volume
        # 3. 检查单日涨跌幅是否 >11%（异常）
        # 4. 检查复权价格跳变是否 >30%（除权异常）
        # 输出: DataQualityResult(approved, risk_level, issues[])
```

**我们的实现路径：**
1. 在更新完数据后增加质量检查步骤
2. 对异常股票标记并从当日候选池排除
3. 质量报告保存到 `data/portfolio/quality_YYYYMMDD.json`

预计工作量：**1-2 天**

### P2 🟠 推荐：指数趋势择时

**Neyray 的方案：**

```python
class IndexBenchmarkService:
    # 缓存 HS300 / CSI500 / CSI1000 / SSE50 / 创业板指 的日线
    # 输出: MA20/MA60/MA120 状态
    # 用于日报顶部展示"指数趋势"表格
```

**为什么有价值？**

之前我们尝试过"系统择时"（v6）但效果不好。区别在于：
- v6 是**用因子信号来决定是否择时**，本质还是因子
- Neyray 的方案是**在日报中展示指数状态**，人为参考，不直接改变仓位

这是一个**信息展示**而非**策略逻辑**，不会引入过拟合风险，但能帮助我们在市场极端环境下理解策略表现。

**我们的实现路径：**
1. 在 `update_daily_data.py` 增加指数日线采集（5-6个指数ETF）
2. 在日报中增加"市场状态"板块，展示各指数 MA20/60/120 状态
3. 初期不改变策略逻辑，仅作为参考信息

预计工作量：**1 天**

### P3 🟠 推荐：行业仓位上限

**为什么有用？**

当前策略在沪深300成分股中均匀选股，但如果某段时间策略集中选了某个行业（比如半导体），行业风险没有控制。Neyray 的约束：

```python
# 行业仓位上限：单一行业不超过 25-30%
cap_industry_weights(target_weights, max_industry_weight=0.25)

# 行业仓位上限触发时，超限行业按比例缩放
# 日报中展示"行业超限触发"表格
```

**我们的实现路径：**
1. 在 `hs300_constituents.csv` 中增加行业列（或用东方财富行业分类）
2. 在调仓后增加行业仓位检查
3. 超限行业的权重等比压缩

预计工作量：**2 天**（含行业分类数据准备）

### P4 🟡 可选：换手率控制

```python
# 单日组合换手上限 25%
cap_daily_turnover(account, target_weights, max_turnover=0.25)
# 如果目标换手超过上限，等比缩放所有权重变化
```

**为什么有用？**

当前策略每次调仓可能产生很高的换手（全部卖出 + 全部买入）。换手率控制能：
- 降低交易成本冲击
- 使回测更接近实际执行

预计工作量：**0.5 天**

### P5 🟡 可选：CLI 工具和配置管理

**Neyray 的方案：**

```bash
python -m quant_sim.cli signal            # 只看信号不执行
python -m quant_sim.cli rebalance         # 执行调仓
python -m quant_sim.cli settle            # 收盘结算
python -m quant_sim.cli data-quality      # 数据质量检查
python -m quant_sim.cli index-trend       # 指数趋势
python -m quant_sim.cli monitors-check    # 监控检查
```

把所有参数从硬编码抽到 `config.json`，通过 `--config` 指定。

预计工作量：**2-3 天**（重构性质）

---

## 五、Neyray 项目中不推荐照搬的

| 特性 | 原因 |
|------|------|
| **ML 策略（岭回归）** | A 股日频数据噪声极大，ML 容易过拟合。需要充分回测验证后才能引入 |
| **每日多次调仓** | 对模拟盘有意义（模拟开盘+临收两个信号），但对实盘会增加交易成本 |
| **四层 cron 调度** | 工程上合理，但复杂度大幅增加。个人项目可以简化 |
| **BTC/ETH Agent** | 与 A 股完全不相关，建议不要混在一个仓库 |

---

## 六、改进路线图（建议顺序）

```
第1周
├── P0: A 股交易约束模块（涨跌停/停牌/T+1）
├── P0: 数据质量门禁
└── P2: 指数趋势展示

第2周
├── P3: 行业仓位上限
├── P4: 换手率控制
└── P5: CLI 工具 + 配置管理

第3周（验证期）
├── 在新模块基础上回测 2024-2025 数据
├── 对比 v3 和 v8（加入新模块后）的表现
└── 确认改进确实有效
```

---

## 七、量化框架领域的参考

| 框架 | Stars | 适合场景 | 我们的选择 |
|------|-------|---------|-----------|
| **QUANTAXIS** | 10.6k | 全功能企业级，支持实盘 | 太重，调研参考 |
| **ZVT** | 4.1k | 模块化，可扩展 | 结构可参考 |
| **Backtrader** | - | 通用回测框架 | 重新开发成本高 |
| **当前自研** | - | 轻量、可控、够用 | **继续迭代** |

**结论**：自研框架的轻量路线是正确的。不需要引入重型框架，重点补上缺失的模块。

---

## 八、总结

Neyray 项目最大的价值不是某个具体策略，而是**工程化水平**：

1. **TradeContext** 补全了 A 股交易的真实约束
2. **数据质量门禁** 让系统不会在脏数据上产生错误信号
3. **多策略并行** 让改进效果可以被客观对比
4. **模块化设计** 让每个功能可以独立开关

我们的 v3 策略**因子设计已经不输**（31个因子 vs ~11个横截面因子），主要差距在**工程完备性**。按优先级逐步补齐后，系统会从一个"能跑的原型"升级为"可靠的模拟交易系统"。
