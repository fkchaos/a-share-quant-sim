# 代码架构讲解

> 面向开发者的实现逻辑与框架说明

## 一、整体架构：三层结构

```
┌──────────────────────────────────────────────────┐
│              sim_daily.py (调度层)                  │  ← 指挥官：决定每天做什么
│  数据更新 → 加载账户 → 止损检查 → 调仓 → 报告生成    │
├──────────────────────────────────────────────────┤
│              sim_account.py (引擎层)                 │  ← 核心：所有计算逻辑
│  SimAccount 类 + 因子计算 + 评分系统                 │
├──────────────────────────────────────────────────┤
│           update_daily_data.py (数据层)              │  ← 后勤：获取和存储数据
│  腾讯 API → 本地 CSV 文件                           │
└──────────────────────────────────────────────────┘
```

**类比**：开一家餐厅

- **数据层** = 采购员（买菜回来存仓库）
- **引擎层** = 厨师（做菜的核心手艺）
- **调度层** = 大堂经理（安排每天的上菜流程）

---

## 二、引擎层：SimAccount 类

### 设计模式：状态机

```python
class SimAccount:
    def __init__(self):
        self.cash = 1_000_000        # 现金（状态）
        self.holdings = {}            # 持仓（状态）
        self.trade_log = []           # 交易记录（日志）
        self.nav_history = []         # 净值曲线（日志）
```

这个类就是一个**虚拟账户**，所有操作都是修改 `self.cash` 和 `self.holdings` 这两个状态。

### 核心方法调用链

```
portfolio_value()        # 净值 = 现金 + Σ(持仓股数 × 当前价)
    ↑
buy() / sell()           # 修改 cash 和 holdings
    ↑
check_stop_loss()        # 扫描持仓，找出亏损 ≥ 20% 的
    ↑
status_report()          # 汇总所有信息 → 返回报告字典
```

### 买入逻辑的工程设计

```python
def buy(self, code, price, date, shares=None):
    # 第1步：自动计算股数（等权分配）
    target_value = self.cash / n_stocks
    target_value = min(target_value, self.cash * 0.12)  # 风控：单只 ≤ 12%

    # 第2步：加入滑点（真实成交比报价差）
    adj_price = price * (1 + 0.001)  # 买入时更贵

    # 第3步：A股规则 — 100股整数倍（1手）
    shares = int(target_value / adj_price / 100) * 100

    # 第4步：钱不够就砍仓位
    if self.cash < cost + commission:
        shares = int((self.cash * 0.98) / adj_price / 100) * 100

    # 第5步：加仓时用加权平均成本
    total_cost = old_shares * old_cost + new_shares * price
    new_avg_cost = total_cost / total_shares
```

**这里体现了真实交易的 5 层细节**：等权分配、风控上限、滑点、最小交易单位、加权平均成本。简化任何一层，回测结果都会失真。

### 卖出逻辑的差异点

```python
def sell(self, code, price, date, reason='SELL'):
    adj_price = price * (1 - 0.001)  # 卖出时更便宜（滑点）
    revenue = shares * adj_price
    commission = revenue * 0.0003     # 佣金（双边收取）
    stamp_tax = revenue * 0.001      # 印花税（仅卖出收取）
```

**注意**：`sell()` 的参数 `reason` 用于区分普通卖出和止损卖出。代码里止损卖出时 `stamp_tax = 0`，这是因为 A 股**股票卖出即收印花税**——实际上这里有个小 bug，印花税不应该被豁免。如果要严格模拟，可以去掉这个条件判断。

---

## 三、因子计算系统：量化策略的心脏

### 因子是什么？

**因子就是给股票打分的项目**。就像高考评语文、数学、英语成绩，我们给股票的"动量"、"反转"、"成交量"等项目打分，最后加权求和得到总分。

### 因子全景（31个）

| 类别 | 因子 | 含义 |
|------|------|------|
| 动量 (5) | mom_5, mom_10, mom_20, mom_60, mom_120 | 今价/N日前价的涨幅 |
| 反转 (3) | rev_3, rev_5, rev_10 | 动量的反义词（超跌反弹信号） |
| 波动率 (3) | vol_10, vol_20, vol_60 | 收益率标准差（负权重 = 惩罚高波动） |
| 成交量 (3) | vol_ratio_5, vol_ratio_20, amount_ratio | 今日量/均量（放量信号） |
| RSI (3) | rsi_6, rsi_14, rsi_28 | 相对强弱指标（超买超卖） |
| 趋势 (2) | macd, boll_pos | 趋势方向和布林带位置 |
| 其他 (2) | skew, rel_strength | 收益率偏度、相对强度 |

### 计算流程

```
原始K线数据 (OHLCV: 开/高/低/收/量)
    │
    ▼
┌──────────────────────────────────────┐
│  calc_factors_for_signal(df)         │
│  单只股票 → 31个因子值                │
├──────────────────────────────────────┤
│  mom:  close[-1]/close[-N] - 1       │
│  rev:  -(close[-1]/close[-N] - 1)    │
│  vol:  returns[-N:].std()            │
│  RSI:  涨均值 / 跌均值 → 0~100      │
│  MACD: EMA(12) - EMA(26)            │
│  Boll: (价格-下轨)/(上轨-下轨)       │
└──────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────────┐
│  generate_scores()                   │
│  所有股票 → 标准化 → 加权求和         │
├──────────────────────────────────────┤
│  1. 遍历所有 CSV 计算因子             │
│  2. Z-Score 标准化                   │
│     z = (val - mean) / std           │
│  3. 按权重加权求和                   │
│  4. 返回 {股票代码: 总分}            │
└──────────────────────────────────────┘
```

### Z-Score 标准化：为什么必须做？

```python
# 原始因子的数值范围差异巨大：
#   mom_20:     -0.05 ~ +0.30   (百分之几)
#   vol_ratio:   0.5  ~ 3.0     (倍数)
#   RSI:        20    ~ 80      (绝对数值)

# 直接相加 → 大数值因子主导结果

# 标准化后：
#   大部分值在 -3 ~ +3 之间
#   > 0 表示高于市场平均
#   < 0 表示低于市场平均
```

这是量化系统里**最关键的工程细节之一**。

### 动量和反转同时做多：矛盾吗？

```python
weights = {
    'mom_20': +0.10,    # 中期动量 → 追涨
    'rev_5':  +0.08,    # 短期反转 → 抄底
}
```

**不矛盾**。这叫**多因子正交化**：

- **短期**（3-5天）反转 → 捕捉超跌反弹
- **中期**（20天）动量 → 捕捉趋势延续
- 两者捕捉不同周期的市场行为，互补而非冲突

---

## 四、调度层：每日运行流程

```
daily_operation() 的 10 个步骤：

 ①  更新行情数据   subprocess 调用 update_daily_data.py
 ②  加载账户状态   从 account.json 反序列化到内存
 ③  确定最新日期   从数据文件中读取最后交易日
 ④  构建价格序列   所有股票的当日收盘价 → price_data (Series)
 ⑤  计算当前净值   portfolio_value()
 ⑥  持仓明细报告   代码/名称/股数/成本/现价/盈亏/权重
 ⑦  止损检查      check_stop_loss() → 触发则卖出
 ⑧  调仓判断      trade_count % 20 == 0?
     ├─ 是 → generate_scores() → 卖出不在 top10 的 → 买入新的
     └─ 否 → 跳过
 ⑨  保存状态      account.json + trade_count.txt
 ⑩  生成收盘报告   NAV / 收益率 / 明日操作计划
```

### 明日计划的设计价值

```python
if is_rebal_tomorrow:
    # 预演明天的调仓操作
    # 输出：要卖什么、买什么、预估金额
else:
    # 止损风险预警
    # 哪些持仓已亏损 15%+（接近 20% 止损线）
    # 关注持仓中跌幅最大 / 涨幅最大的
```

这个设计让用户**提前一天知道要做什么**，不至于收盘后手忙脚乱。非调仓日也不是空转——她会持续给出风险预警。

### trade_count 机制：不是按日历天数

```python
# 用 trade_count 而不是 date 来决定调仓日

# 为什么？因为交易日历不是连续的：
#   周末、节假日不交易
#   如果按自然日算，20天可能是4周（实际只有15-18个交易日）

# 实际效果：每经过 20 个有数据的日子调仓一次
trade_count += 1
if trade_count % 20 == 0:
    rebalance()
```

---

## 五、数据层：增量更新机制

### 核心逻辑

```
①  读取本地 CSV，找到最后日期 local_latest
②  请求最近 N 天数据（N = 缺口 + 5，防止遗漏）
③  过滤出 local_latest 之后的新数据
④  追加到原 CSV（append，不覆盖）
⑤  重试失败的（网络抖动常见）
```

### 关键技术点

```python
# 1. 智能请求天数
gap = (today - local_date).days
days = gap + 5  # 多请求几天防止遗漏

# 2. 频率控制（防止被 API 封禁）
time.sleep(0.15)  # 280只 ≈ 5分钟

# 3. 失败重试
if fail_list:
    time.sleep(3)  # 等待3秒后重试
    for code in fail_list:
        update_stock(code, days=20)  # 增加请求天数

# 4. 增量追加（不覆盖已有数据）
new_data = df[df.index > local_latest]
combined = pd.concat([old_df, new_data])
combined = combined[~combined.index.duplicated(keep='last')]
```

### 为什么用 requests 而不是 AKShare？

```
环境限制：
  ✅ baidu.com → 通
  ✅ gtimg.cn（腾讯） → 通  
  ❌ eastmoney（东方财富） → RemoteDisconnected

腾讯 API 的优势：
  - 不需要 token、不需要签名
  - 请求简单，返回结构清晰
  - 数据质量足够用于日频策略
```

CSV 格式：

```csv
date,open,high,low,close,volume,amount,outstanding_share,turnover
2026-01-04,10.50,10.80,10.30,10.65,1234567,1.31e+09,,
```

---

## 六、值得学习的编程模式

### 1. 状态持久化（序列化/反序列化）

```python
# 保存：Python 对象 → JSON 文件
data = {
    'cash': account.cash,
    'holdings': account.holdings,
    'trade_log': account.trade_log,
    'nav_history': account.nav_history,
}
json.dump(data, f, indent=2, default=str)

# 加载：JSON 文件 → Python 对象
data = json.load(f)
account.cash = data['cash']
account.holdings = data['holdings']
```

**为什么不用数据库？** 数据量小（1个账户 + 280个CSV），JSON 文件足够。用数据库反而增加复杂度。这是**工程上的合适选择**，不是偷懒。

### 2. 防御性编程

```python
# 每一步都检查边界条件
if shares <= 0: return False
if code not in self.holdings: return False
if not pd.isna(p) and p > 0:   # 数据可能缺失
if len(close) >= w:             # 历史数据可能不够
if std > 0:                      # 避免除以零
```

金融代码**必须**防御性编程。数据永远不会完美。

### 3. 解耦设计（subprocess 调用）

```python
subprocess.run(
    [sys.executable, "~/update_daily_data.py"],
    capture_output=True, text=True, timeout=300
)
```

用 `subprocess` 而不是 `import` 的原因：让数据更新和交易逻辑**解耦**。两个脚本可以独立运行、独立测试。如果改用 `import`，当数据更新脚本出错时，会直接导致交易脚本崩溃。

---

## 七、改进路线图

| 优先级 | 方向 | 说明 |
|--------|------|------|
| P0 🔴 | 数据校验 | 缺少对 CSV 数据质量的检查（停牌、除权除息等） |
| P1 🟠 | 异常恢复 | 网络失败时没有断点续传，需要从头重试 |
| P2 🟡 | 参数配置 | 参数硬编码在脚本里，应抽到配置文件（YAML/pyproject.toml） |
| P3 🟢 | 单元测试 | 目前没有测试，回归全靠手动运行 |
| P4 🔵 | 日志系统 | 用的是 `print()`，应改用 `logging` 模块 |

---

## 八、关键常量一览

可在 `sim_daily.py` 和 `sim_account.py` 顶部调整：

```python
# 账户参数
INITIAL_CAPITAL  = 1_000_000    # 初始资金
COMMISSION_RATE  = 0.0003       # 佣金 0.03%
STAMP_TAX_RATE   = 0.001        # 印花税 0.1%
SLIPPAGE_RATE    = 0.001        # 滑点 0.1%

# 策略参数
TOP_N            = 10           # 持仓数量
REBAL_FREQ       = 20           # 调仓频率（交易日）
STOP_LOSS        = 0.20         # 止损线 -20%
```
