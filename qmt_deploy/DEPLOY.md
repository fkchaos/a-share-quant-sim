# QMT Adapter 部署指南

> Last updated: 2026-09-11 — reflects refactored architecture with strategy_base.py, order state machine, atomic JSON, and auto-sync.

## 目录结构

```
qmt_deploy/
├── v61c_qmt.py                 # v61c入口文件（加载这个）
├── v75j_qmt.py                 # v75j入口文件（加载这个）
├── qmt_diagnostic.py           # 环境诊断（先跑这个）
├── qmt_adapter/
│   ├── config.py               # 风控/市场/账户参数（集中管理）
│   ├── data.py                 # 行情数据获取（K线、价格、订阅）
│   ├── qmt_data.py             # ZZ1800股票池 + FLOAT_SHARES
│   ├── qmt_data_static.py      # 行业分类静态数据（INDUSTRY, TECH_SECTORS）
│   ├── trading.py              # 交易下单封装 + 订单状态机（7状态、3层确认）
│   ├── strategy_base.py        # 共享工具：get_bar_date, hold_days, 涨停过滤
│   ├── qmt_runner.py           # 公共逻辑（风控/rebalance/买入/持仓JSON）
│   ├── v61c_strategy.py        # v61c选股逻辑
│   ├── v75j_strategy.py        # v75j选股逻辑
│   └── strategy_skeleton.py    # 策略骨架模板
├── references/
│   ├── strategy-writing-guide.md
│   └── qmt-official-backtest-guide.md
├── qmt_verify.py               # 本地验证脚本
└── DEPLOY.md                   # 本文档
```

## 部署步骤

### 1. 复制文件到QMT

将整个 `qmt_deploy/` 目录复制到QMT Windows机器上。

```
建议路径: D:\qmt\strategies\a-share-quant-sim\
```

### 2. QMT加载策略

1. 打开QMT客户端
2. 进入「策略交易」→「策略编辑器」
3. 新建策略，选择「导入文件」
4. 加载入口文件：`v61c_qmt.py` 或 `v75j_qmt.py`
5. **不要**直接加载 `qmt_adapter/` 下的文件，入口文件会自动导入

### 3. 配置参数

打开 `qmt_adapter/config.py`：

```python
# 所有策略参数集中管理
STRATEGIES = {
    'v61c': {
        'stop_loss': -0.08, 'take_profit': 0.25, 'hold_days_max': 5,
        'capital': 100000, 'max_holdings': 5, 'max_per_stock': 0.20,
        'rebalance_days': 5, 'sell_out_of': 15, 'max_daily_buy': 5,
    },
    'v75j': {
        'stop_loss': -0.08, 'take_profit': 0.25, 'hold_days_max': 20,
        'max_stock_price': 300, 'capital': 100000, 'max_holdings': 3,
        'max_per_stock': 0.33, 'breadth_high': 0.50, 'breadth_low': 0.30,
        'max_daily_buy': 3,
    },
}

# 账户配置（回测用SIMTEST，实盘改成真实ID）
ACCOUNT_CONFIG = {
    'account_id': 'SIMTEST',
    'account_type': 'STOCK',
}

# PER_STRATEGY_POSITIONS = True  # 已启用，持仓JSON按策略隔离
```

### 4. 开启DEBUG

编辑入口文件顶部：

```python
# v61c_qmt.py 或 v75j_qmt.py
DEBUG = True   # ← 回测时设True，实盘设False
```

### 5. 运行回测

#### 首次使用：先跑环境诊断

1. 加载 `qmt_diagnostic.py`，跑1天回测
2. 查看输出，确认所有 `[OK]` 项通过
3. 如果有 `[FAIL]` 项，参考下方「常见问题」排查

#### 正式回测

1. QMT「策略交易」→「回测」
2. 选择策略文件（v61c_qmt.py 或 v75j_qmt.py）
3. 设置回测参数：
   - 初始资金：100000
   - 回测周期：建议先跑3个月验证
   - K线周期：日线
   - 复权方式：前复权
4. 点击「运行回测」

---

## 架构概览

```
entry file (v61c_qmt.py / v75j_qmt.py)
  │
  ├── init(C) → qmt_runner.qmt_init(C) → strategy.init(C)
  │
  ├── handlebar(C)  [BACKTEST mode]  ──┐
  │                                     ├──→ strategy.on_signal(C)
  └── on_timer(C)   [LIVE mode]     ──┘
                                          │
                          ┌────────────────┤
                          ▼                ▼
                    check_risk()     execute_buy()
                    (sell triggers)  (stock selection)
                          │                │
                          ▼                ▼
                    QmtAccount.buy/sell()
                          │
                          ▼
                    passorder() → order_state_machine
                          │
                          ├── order_callback (logging)
                          ├── deal_callback (logging)
                          └── schedule_run polling → _do_order_check_single()
                                    │
                                    ├── Layer 1: ORDER query
                                    ├── Layer 2: DEAL query (fallback)
                                    └── Layer 3: POSITION query (fallback)
                                          │
                                          ▼
                                    _order_transition(filled)
                                          │
                                          ▼
                                    _sync_strategy_position()
                                          │
                                          ▼
                                    strategy_buy/sell → _positions_*.json
```

---

## 模块职责

### strategy_base.py — 共享工具

从 v61c_strategy 和 v75j_strategy 中提取的 ~90 行共享代码：

| 函数 | 职责 |
|------|------|
| `get_bar_date(C)` | 从 ContextInfo 获取当前 K 线日期，**禁止**用 `datetime.now()` |
| `load_hold_days(strategy_name)` | 从 `_hold_days_{strategy}.json` 加载持仓天数 |
| `persist_hold_days(strategy_name, hold_days, today)` | 原子写入（tmp + rename）持仓天数 |
| `is_limit_up(close, prev_close)` | 涨停检测（阈值 9.5%，考虑价格精度） |
| `apply_limit_up_filter(codes, kline_data)` | 从候选列表中过滤涨停股 |

### trading.py — 订单状态机

7 状态、3 层确认、原子 JSON 更新。详见「订单状态机」章节。

### qmt_runner.py — 公共逻辑

| 函数 | 职责 |
|------|------|
| `qmt_init(C)` | QMT 环境初始化，注入内置函数 |
| `check_risk(C, account, holding_days, risk_config, ...)` | 风控检查（SL/TP/HD），返回卖出列表 |
| `execute_buy(C, account, target_weight, ...)` | 买入执行（lot sizing、资金检查） |
| `get_strategy_holdings(strategy_name, account)` | 获取持仓（per-strategy JSON 或 account 级） |
| `strategy_buy/sell()` | 持仓 JSON 增删改 |
| `load/save_strategy_positions()` | `_positions_{strategy}.json` 原子读写 |

### config.py — 集中配置

所有策略参数在 `STRATEGIES` dict 中按策略名分组。`get_strategy_params(name)` 返回副本（防止意外修改）。

---

## 订单状态机

### 状态定义

```
pending       — order placed, waiting for QMT ack
ordered       — QMT acknowledged, waiting for fill
partial       — partially filled
cancel_pending — cancel requested, awaiting confirmation
filled        — fully filled     [TERMINAL]
rejected      — rejected/failed  [TERMINAL]
cancelled     — cancelled        [TERMINAL]
```

### 状态转换

```
pending → ordered:        ORDER found, traded=0
pending → partial:        ORDER found, traded>0
pending → filled:         ORDER/DEAL/POSITION confirms traded>=vol
pending → rejected:       ORDER status=57 or 60s timeout

ordered → partial:        traded increases
ordered → filled:         traded >= vol
ordered → cancelled:      ORDER status=54 (confirmed)
ordered → cancel_pending: ORDER status 51/52

partial → filled:         traded >= vol
partial → cancelled:      ORDER status=54 or 53 (partial cancel)
partial → cancel_pending: ORDER status 51/52

cancel_pending → partial:  traded increases while cancel pending
cancel_pending → filled:   traded >= vol while cancel pending
cancel_pending → ordered:  cancel rejected (status reverts to 50)
```

### 三层确认 (ORDER → DEAL → POSITION)

`_do_order_check_single()` 按优先级查询：

1. **Layer 1: ORDER query** — 查 `get_trade_detail_data('ORDER')`，按 remark 匹配
   - 检查 `m_nOrderStatus`：57=拒绝、54=取消、51/52=取消请求中
   - 检查 `m_nVolumeTraded` 累计成交量
   - 获取 `m_strOrderSysID` 用于撤单
2. **Layer 2: DEAL query** — ORDER 未找到时回退查 `get_trade_detail_data('DEAL')`
   - 累计所有匹配 remark 的成交
3. **Layer 3: POSITION query** — ORDER/DEAL 都未找到时回退查 `get_trade_detail_data('POSITION')`
   - BUY：确认持仓存在
   - SELL：确认持仓减少

### 超时处理

| 条件 | 超时时间 | 动作 |
|------|---------|------|
| pending 无任何记录 | 60s | → rejected |
| ordered/partial 无 order_id | 30s | → rejected (force expired) |
| ordered/partial/cancel_pending | 120s | → cancel (最多 3 次重试) |
| 任意活跃状态 | 300s | → rejected (force expired) |

### 超时处理

| 条件 | 超时时间 | 动作 |
|------|---------|------|
| pending 无任何记录 | 60s | → rejected |
| ordered/partial 无 order_id | 30s | → rejected (force expired) |
| ordered/partial/cancel_pending | 120s | → cancel (最多 3 次重试) |
| 任意活跃状态 | 300s | → rejected (force expired) |

### ORDER 状态码 (QMT 官方)

| 代码 | 含义 | 分类 |
|------|------|------|
| 48 | 未上报 | — |
| 49 | 待报 | — |
| 50 | 已报 | — |
| 51 | 撤单请求 | CANCEL_PENDING |
| 52 | 部分撤单请求 | CANCEL_PENDING |
| 53 | 部分撤单确认 | TERMINAL_CANCEL (order done) |
| 54 | 全部撤单确认 | TERMINAL_CANCEL |
| 55 | 部分成交 | — |
| 56 | 全部成交 | — |
| 57 | 废单 | TERMINAL_REJECT |
| 86 | 已确认 | — |

---

## JSON 持久化

### 两套独立文件

| 文件 | 更新时机 | 更新者 | 用途 |
|------|---------|--------|------|
| `_hold_days_{strategy}.json` | 每根 K 线结束 | strategy `on_signal()` | 持仓天数计数 |
| `_positions_{strategy}.json` | 订单成交确认 | `trading.py` → `_sync_strategy_position()` | per-strategy 持仓 |

**不合并**的原因：更新频率不同（hold_days 每 bar 更新，positions 仅在成交时更新），关注点不同。

### 原子写入

所有 JSON 写入均采用 `tmp + os.rename` 模式，防止写入中断导致文件损坏。

### hold_days 格式

```json
{
  "hold_days": {"000001.SZ": 3, "600519.SH": 1},
  "last_date": "2026-09-10"
}
```

### positions 格式

```json
{
  "000001.SZ": {"shares": 100, "cost_price": 12.34, "added_at": "2026-09-10"},
  "600519.SH": {"shares": 200, "cost_price": 1800.50, "added_at": "2026-09-08"}
}
```

---

## strategy_name 大小写约定

| 场景 | 格式 | 示例 |
|------|------|------|
| 内部标识 (config, JSON文件名, 日志) | **lowercase** | `'v61c'`, `'v75j'` |
| passorder `strategyName` 参数 | **UPPERCASE** | `'V61C'`, `'V75J'` |
| `get_trade_detail_data` 查询 | **UPPERCASE** | `'V61C'`, `'V75J'` |

**规则**：传入 `QmtAccount.buy/sell()` 时用 lowercase，trading.py 内部 `.upper()` 转换后传给 QMT API。

---

## 持仓追踪 (PER_STRATEGY_POSITIONS)

`config.py` 中 `PER_STRATEGY_POSITIONS = True` 已启用。

### 工作原理

1. `QmtAccount.buy/sell()` → `passorder()` 发送订单
2. 订单状态机轮询 → 检测到成交 (`filled`)
3. `_order_transition(filled)` → `_sync_strategy_position()`
4. `_sync_strategy_position()` → `qmt_runner.strategy_buy/sell()` → 更新 `_positions_{strategy}.json`

### 不从 QMT 账户同步持仓

JSON 为空 = 策略没买过。避免新策略初始化时误拽入其他策略持仓。

### 关闭隔离

设 `PER_STRATEGY_POSITIONS = False` 回退到旧逻辑（共享账户持仓）。

---

## 两个策略的区别

| | v61c | v75j |
|--|------|------|
| 选股因子 | 低换手率 + 小市值 | 科技板块流动性 |
| 持仓数 | 5只 | 3只 |
| 单只仓位 | 20% | 33% |
| 总仓位上限 | 基于 capital × max_per_stock | 基于 capital × max_per_stock |
| 超期卖出 | 5天 (rebalance_days) | 20天 (hold_days_max) |
| 止损 | -8% | -8% |
| 止盈 | +25% | +25% |
| 板块 | 全市场（排除科创板） | 科技板块（排除科创板） |
| 广度过滤 | 无 | MA20比例<30%不买, 30-50%线性减仓 |
| 续持逻辑 | Top15排名内到期续持 | 无（到期即卖） |

---

## 调仓逻辑

**per-stock独立调仓**，没有全局调仓日：

```
每根K线（on_signal）：
1. 所有持仓 hold_days++
2. 风控检查：止损/止盈/最大持仓天数 → 触发即卖
3. v61c 特有：续持检查 — 到期但仍在 Top15 → reset hold_days
4. v61c 特有：排名掉出 Top15 → 即时卖出
5. v75j 特有：到期即卖（无续持）
6. 空位检查：max_holdings - 当前持仓数 = slots
7. v75j：广度过滤 → breadth < low_thresh 则不买
8. slots > 0 → 选股买入
```

---

## 常见问题

### Q1: 策略加载报编码错误

**现象**: `UnicodeDecodeError` 或 `GBK codec error`

**原因**: 策略文件使用GBK编码（QMT要求）

**解决**: 
- 不要用UTF-8编辑器修改策略文件
- 用记事本或GBK兼容的编辑器
- 如果文件被转成UTF-8，用 `iconv` 转回GBK：
  ```bash
  iconv -f UTF-8 -t GBK file.py -o file_gbk.py
  ```

### Q2: 策略加载报模块找不到

**现象**: `ModuleNotFoundError: No module named 'qmt_adapter'`

**原因**: 入口文件和qmt_adapter目录不在同一层级

**解决**: 
- 确保目录结构正确：`v61c_qmt.py` 和 `qmt_adapter/` 在同一目录
- 在QMT中加载入口文件时，选择正确的路径

### Q3: 回测不出交易记录

**现象**: 回测完成但没有任何买卖

**可能原因**:
1. `DEBUG=False` → 看不到输出（但交易应该有）
2. 股票池为空 → 检查 `qmt_data.py` 的 `ZZ1800_STOCKS`
3. 数据没下载 → QMT需要先下载历史数据
4. 广度过滤(v75j) → 弱市时策略不买

### Q4: 买入失败

**现象**: debug显示 `buy targets` 但没有成交

**可能原因**:
1. 资金不足 → 检查 `available cash`
2. 停牌/涨跌停 → 当天无法交易
3. 最小交易单位 → A股最少100股

### Q5: 风控没触发

**现象**: 持仓亏损超过止损线但没卖出

**排查**:
1. 检查debug输出中的 `risk:` 行
2. 确认参数正确
3. 检查 `get_close_price` 是否返回了有效价格
4. T+1 保护：持有首日 (hold_days < 1) 跳过风控

### Q6: v75j广度一直是1.0

**现象**: debug显示 `breadth: 1.0000 (xxx/xxx above MA20)`

**原因**: 数据不足20天，广度默认返回1.0

**解决**: 确保QMT下载了足够的历史数据（至少25天）

### Q7: 科创板股票被选中

**现象**: 买入了688/689开头的股票

**解决**: 检查debug输出中的科技股列表，确认过滤逻辑正常

### Q8: 涨停股被买入

**现象**: 买入了当天涨停的股票

**排查**: 涨停过滤在选股阶段执行（buy_list 生成时），检查：
1. 阈值是否正确（主板 10% 用 1.095，创业板/科创板 20% 用 1.195）
2. kline 数据是否包含 prev_close

### Q9: 持仓JSON不更新

**现象**: 成交了但 `_positions_*.json` 没有变化

**排查**:
1. 检查 `PER_STRATEGY_POSITIONS = True` 是否启用
2. 检查 `_order_transition` 是否被调用（日志中有 `ORDER_POLL` 输出）
3. 检查 `_sync_strategy_position` 是否报错

### Q10: 撤单后订单仍活跃

**现象**: 发起撤单但订单仍在轮询

**排查**:
1. 检查 `order_id` 是否获取成功（需要 `m_strOrderSysID`）
2. 检查 `cancel_retries` 是否达到 3 次上限
3. 检查 `can_cancel_order` 是否返回 True

---

## Debug输出说明

开启 `DEBUG=True` 后，QMT的「策略输出」窗口会显示：

### 关键日志标识

| 标识 | 含义 |
|------|------|
| `[INIT]` | 策略初始化 |
| `[BAR]` | 当前 K 线日期 |
| `[RISK]` | 风控检查结果 |
| `[V61C]` / `[V75J]` | 策略内部逻辑 |
| `[PASSORDER]` | 下单参数 |
| `[ORDER_POLL]` | 订单状态轮询 |
| `[ORDER_CB]` | QMT 订单回调 |
| `[DEAL_CB]` | QMT 成交回调 |
| `[BUY]` / `[SELL]` | 买入/卖出执行 |

### v61c示例
```
[INIT][V61C] init done. pool=1800, rebalance_days=5
[V61C] risk: SL=-0.08 TP=0.25 HD=5
[V61C] risk: 000xxx pnl=-10.5% days=3 -> SELL(SL)
[V61C] time exit: 000yyy days=5, still in Top15 -> HOLD (renew)
[V61C] 2 slots available, selecting stocks...
[V61C] candidates=1523, top 10:
  000034.SZ score=1.9400 turnover=4.83% mcap=943.5亿
[V61C] buy targets:
  000034.SZ weight=0.2000
[PASSORDER] opType=23, ... strategyName=V61C, quickTrade=0, remark=B-BUY-000034-143025
[ORDER_POLL][B-BUY-000034-143025] pending -> ordered: ordered vol=500
[ORDER_POLL][B-BUY-000034-143025] ordered -> filled: fully filled 500/500
```

### v75j示例
```
[INIT] new day detected: 2026-09-09 -> 2026-09-10, keeping 2 positions
[V75J] init done. pool=1800, tech=423, hold_days_max=20
[V75J] risk: SL=-0.08 TP=0.25 HD=20
[V75J] breadth: 0.4500 (190/423 above MA20)
[V75J] breadth 0.45 in [0.30, 0.50) -> scale to 2 slots
[V75J] liquidity candidates=423, top 10:
  600519.SH avg_amount=45.2亿
[V75J] buy targets:
  600519.SH weight=0.3300
```

---

## 本地验证

在Linux机器上运行验证脚本：

```bash
cd /root/a-share-quant-sim
python3 qmt_deploy/qmt_verify.py
```

验证项目：
1. 风控逻辑（止损/止盈/最大天数）
2. 仓位计算
3. Per-stock超期卖出
4. 广度过滤（v75j）
5. 换手率+市值排名（v61c）
6. 科创板过滤（v75j）
7. 风控参数分离
8. DEBUG开关
9. 有空位才买入
10. 涨停过滤（strategy_base.is_limit_up）
11. 原子JSON写入
12. hold_days 持久化/加载

---

## Python 3.6.8 兼容性

QMT 内置 Python 3.6.8，以下语法**禁止使用**：

| ❌ 禁止 | ✅ 替代 |
|---------|---------|
| `x := expr` (walrus) | 先赋值再使用 |
| `dict1 \| dict2` | `{**d1, **d2}` |
| `f'{x=}'` (debug f-string) | `f'x={x}'` |
| `pd.DataFrame.map()` | `df.apply()` |
| `list \| list` (union type) | 手动检查 |

**所有 `.py` 文件必须**：
1. 第一行 `#coding:gbk`
2. 实际保存为 GBK 编码
3. 字符串常量用英文（如 `TECH_SECTORS` 用 `'Electronics'` 不用 `'电子'`）
