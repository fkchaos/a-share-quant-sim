### ⚠️ QMT Adapter vs 本地策略交叉Review清单（2026-08-26 新增）

**当QMT回测结果异常时，必须和本地回测对比**：

```python
# 本地快速回测脚本
from core.db import load_panel_from_db
from scripts.strategies.v61c_turnover_size import calc_factors_v61c, DEFAULT_PARAMS

panels, codes = load_panel_from_db(start_date='2026-02-01', end_date='2026-04-30',
                                    need_open=True, need_hl=True, pool='zz1800')
close, vol, amt, open_, high, low = panels
start_i = close.index.get_indexer(['2026-03-02'], method='nearest')[0]

cash = 100000.0; holdings = {}; navs = []
for i in range(start_i, len(close)):
    # sell (risk check)
    for c in list(holdings):
        pnl = (close[c].iloc[i] - holdings[c]['cost']) / holdings[c]['cost']
        days = i - holdings[c]['idx']
        if pnl < DEFAULT_PARAMS['STOP_LOSS'] or pnl > DEFAULT_PARAMS['TAKE_PROFIT'] or days >= DEFAULT_PARAMS['HOLD_DAYS_MAX']:
            cash += holdings[c]['shares'] * close[c].iloc[i]; del holdings[c]
    # buy
    slots = DEFAULT_PARAMS['MAX_HOLDINGS'] - len(holdings)
    if slots > 0:
        fs = calc_factors_v61c(close.iloc[:i+1], vol.iloc[:i+1], amt.iloc[:i+1], high.iloc[:i+1], low.iloc[:i+1])
        scores = list(fs.values())[0]
        for code in scores.sort_values(ascending=False).head(DEFAULT_PARAMS['MAX_HOLDINGS']*2).index:
            if code in holdings or slots <= 0: continue
            price = close[code].iloc[i]
            shares = int(cash*0.95/slots/price/100)*100
            if shares >= 100:
                cash -= shares*price; holdings[code] = {'shares':shares,'cost':price,'idx':i}
                slots -= 1
    navs.append(cash + sum(holdings[c]['shares']*close[c].iloc[i] for c in holdings))

ret = (navs[-1]-100000)/1000; print('Return: %+.2f%%' % ret)
```

**对比结论**（2026-08-26 实测）：
| | 本地 | QMT |
|--|------|-----|
| Return | -0.15% | 明显亏损 |
| Sharpe | 0.071 | 负 |
| MaxDD | -8.6% | -28% |

本地持平而QMT亏损 → **说明QMT适配器有bug，不是策略问题**。

**已定位并修复的差异**：
1. QMT V61C_RISK_CONFIG参数与本地不一致（SL=-0.10 vs -0.08, TP=0.20 vs 0.25）→ ✅ 已修复
2. QMT缺失v61c SELL_OUT_OF到期续持逻辑 → ✅ 已修复（2026-08-26）
3. get_market_data_ex缺end_time导致买入价用今天价格 → ✅ 已修复（2026-08-26）
4. get_market_data_ex用subscribe=False对非主图品种返回空 → ✅ 已修复，改用subscribe=True
5. SIMTEST账户可能有残留持仓导致m_dOpenPrice不准确 → 需重置账户（代码层面已解决）

## ⚠️ QMT Adapter vs 本地策略交叉Review清单（2026-08-26 新增）

QMT适配版和本地模拟盘策略**必须逐项对比**，常见遗漏：

### 1. 风控参数不共享
不同策略变体（v61c vs v75j）的止损/止盈/持有期**可能不同**。不能共用一个RISK_CONFIG。

```python
# config.py — 为每个策略变体定义独立风控
RISK_CONFIG = { ... }        # default (v75j)
V61C_RISK_CONFIG = { ... }   # v61c专用

# qmt_runner.py — check_risk 支持可选 risk_config 参数
def check_risk(C, account, holding_days, risk_config=None):
    if risk_config is None:
        from .config import RISK_CONFIG
        risk_config = RISK_CONFIG

# v61c_strategy.py — 传入自己的风控
qmt_runner.check_risk(C, _account, _hold_days, _risk_config)
```

**⚠️ 必须和本地策略参数一致**。2026-08-26实测发现QMT版V61C_RISK_CONFIG的SL=-0.10/TP=0.20，而本地是SL=-0.08/TP=0.25，导致QMT回测亏损更多。**每次适配新策略时，逐项对比config.py参数和原始策略DEFAULT_PARAMS。**

### 🔴 Config 集中化 + Per-Strategy Capital（2026-08-27 重构）

所有策略参数集中在 `config.py` 的 `STRATEGIES` 字典中，策略文件通过 `get_strategy_params()` 读取。

```python
# config.py
ACCOUNTS = {
    1: {'strategy': 'v61c'},
    2: {'strategy': 'v75j'},
}

STRATEGIES = {
    'v61c': {
        'stop_loss': -0.08, 'take_profit': 0.25, 'hold_days_max': 5,
        'capital': 100000,        # per-strategy fixed budget
        'max_holdings': 5, 'max_per_stock': 0.25,  # per stock max 25% = 2.5万
        'rebalance_days': 5, 'sell_out_of': 15, 'max_daily_buy': 5,
    },
    'v75j': {
        'stop_loss': -0.08, 'take_profit': 0.25, 'hold_days_max': 20,
        'capital': 100000,        # per-strategy fixed budget
        'max_holdings': 3, 'max_per_stock': 0.35,  # per stock max 35% = 3.5万
        'breadth_high': 0.50, 'breadth_low': 0.30,
        'max_daily_buy': 3,
    },
}

def get_strategy_params(name):
    """Get strategy parameters by name. Returns copy to prevent mutation."""
    if name not in STRATEGIES:
        raise ValueError("Unknown strategy: %s" % name)
    return dict(STRATEGIES[name])
```

**策略文件读取方式**：
```python
# v61c_strategy.py
from .config import get_strategy_params
_params = get_strategy_params('v61c')
max_holdings = _params.get('max_holdings', 5)
max_per_stock = _params.get('max_per_stock', 0.25)
```

**改参数只动 config.py 一个地方**，策略文件不再硬编码。Legacy aliases (RISK_CONFIG, V61C_RISK_CONFIG 等) 保留向后兼容但标记 deprecated。

#### ⚠️ Per-Strategy Capital 仓位计算（2026-08-27 新增）

**问题**：多个策略共享一个QMT账户时，`account.get_total_value()` 返回账户总资产（含其他策略的持仓），导致仓位计算互相干扰。

**解决方案**：每个策略用固定的 `capital` 作为仓位基准，不依赖账户总资产。

```python
# qmt_runner.py execute_buy()
def execute_buy(C, account, target_weight, bar_date='', capital=50000):
    available = account.get_cash()
    for code, weight in target_weight.items():
        buy_amount = capital * weight        # 用固定capital，不是total_value
        buy_amount = min(buy_amount, available * 0.95)  # 但受可用现金约束
        ...

# v61c_strategy.py
target[code] = max_per_stock  # = 0.25 (25% of capital = 2.5万)
bought = qmt_runner.execute_buy(C, _account, target, bar_date=today, capital=_params['capital'])
```

**仓位公式**：`buy_amount = capital × max_per_stock`，上限 `available × 0.95`。

#### ⚠️ hold_days_max vs rebalance_days 区分

两个参数含义不同：
- **`hold_days_max`**：风控超时，超过此天数**强制卖出**（不查排名）。所有策略都有。
- **`rebalance_days`**：v61c到期续持检查周期（到期→查Top15→续/卖）。**仅v61c有此参数**。

**v75j 没有 `rebalance_days` 逻辑**（超时直接卖，不查排名），config 里不应有此参数。之前 v75j config 里的 `rebalance_days=10` 是死参数（adapter 不使用）。

**v61c vs v75j 卖出逻辑对比**：
| | v61c | v75j |
|--|------|------|
| 止损/止盈 | 硬性 -8%/+25% | 硬性 -8%/+25% |
| 超时 | hold_days_max=5 | hold_days_max=20 |
| 到期续持 | ✅ 到期查Top15，续/卖 | ❌ 无，到期直接卖 |
| 排名下降 | ✅ 不在Top15→卖 | ❌ 无此逻辑 |

#### ⚠️ Per-Strategy Position Tracking（2026-08-27 新增）

**问题**：两个策略跑同一个QMT账户时，`account.get_holdings()` 返回所有持仓（不分策略），`check_risk` 可能卖错策略的股票，`max_holdings` 按总数算会互相挤占。

**解决方案**：`PER_STRATEGY_POSITIONS = True` 开关，每个策略维护独立的 `_positions_{strategy}.json`。

```python
# config.py
PER_STRATEGY_POSITIONS = True   # True=每策略独立持仓隔离
                                 # False=共享账户（旧逻辑）

# qmt_runner.py — helper functions
def get_strategy_holdings(strategy_name, account):
    """Get holdings: per-strategy if enabled, else account-wide."""
    from .config import PER_STRATEGY_POSITIONS
    if not PER_STRATEGY_POSITIONS:
        return account.get_holdings()
    pos = load_strategy_positions(strategy_name)
    return [{'code': c, 'shares': v['shares'], 'avg_cost': v['cost_price']}
            for c, v in pos.items() if v.get('shares', 0) > 0]

def strategy_buy(strategy_name, code, shares, cost_price, date=''):
    """Record a buy in strategy's position file."""

def strategy_sell(strategy_name, code, shares):
    """Record a sell in strategy's position file."""
```

**策略文件用法**：
```python
# v61c_strategy.py — check holdings
holdings = qmt_runner.get_strategy_holdings('v61c', _account)
current_count = len([p for p in holdings if p.get('shares', 0) > 0])
slots = max_holdings - current_count

# After buy — record
bought = qmt_runner.execute_buy(C, _account, target, bar_date=today, capital=_params['capital'])
for code in bought:
    qmt_runner.strategy_buy('v61c', code, shares, price, today)

# After sell — record
_account.sell_all(code)
qmt_runner.strategy_sell('v61c', code, 999999)  # sell all
```

**JSON文件格式**（`_positions_v61c.json`）：
```json
{
  "301339.SZ": {"shares": 1800, "cost_price": 11.07, "added_at": "2026-08-25"},
  "000030.SZ": {"shares": 4700, "cost_price": 4.23, "added_at": "2026-08-27"}
}
```

**注意事项**：
- 此为临时措施，单账户多策略场景下的隔离方案
- `PER_STRATEGY_POSITIONS = False` 时回退到旧逻辑（查账户持仓）
- 首次运行前需初始化 `_positions_*.json`（从模拟盘持仓导入）
- buy/sell 必须同时更新 QMT 账户和本地 JSON

### 2. v61c SELL_OUT_OF逻辑不要遗漏
v61c的核心是"到期续持优化"：到期时检查排名是否在Top15内，在则续持。QMT版必须实现。

**v75j 没有续持逻辑**（超时直接卖，不查排名），不要混淆两个策略的超时行为。

### 3. 科创板过滤不要遗漏
原始v75a有 `scores[~scores.index.str.startswith(('688', '689'))]`，QMT版必须保留。

### 3. 流动性因子度量要精确
v75j的"流动性"=**20日均成交额(amount)**，不是float_shares（流通股数）。
两者差异大：高股价股票成交额大但float_shares不一定大。

### 4. volume单位差异
QMT返回volume单位是**股**（不是手）。
- 换手率公式：`volume / float_shares`（QMT）vs `volume * 100 / float_shares`（腾讯API）

### 5. 死变量清理
适配过程中容易产生赋值但从未读取的变量（如缓存到错误变量名），必须清理。

### 标准Review步骤
```
1. 列出原始策略的因子公式/风控参数/过滤规则
2. 逐项对比QMT版实现
3. 检查volume单位转换
4. 检查科创板过滤
5. 检查风控参数是否独立
6. grep死变量（赋值但未读取的模块级变量）
```

## ⚠️ 策略适配验证方法论（2026-08-26 新增）

**主公明确要求**：适配到QMT后，必须用debug输出验证数据和逻辑正确性，不能只靠本地测试。

### 验证三步法

**第一步：环境诊断**（首次部署）
- 加载 `qmt_verify.py`，跑本地逻辑验证
- 确认9项测试全通过（风控/仓位/广度/排名/科创过滤等）

**第二步：数据验证**（每个策略）
- 开启DEBUG，在选股函数加`[DATA]`打印
- 在data.py加`[KLINE]`打印
- 对比QMT返回数据和本地数据：
  - 价格是否一致
  - volume单位是股还是手（`amount/close ≈ vol` → 股，`≈ vol×100` → 手）
  - 流通股本是否合理（对比东方财富/Wind）

**第三步：逻辑验证**（对比本地回测）
- 选1-2个月时间窗口，本地和QMT跑相同条件
- 对比：选股结果、交易次数、持仓变化、收益率

### 关键对比指标

| 指标 | 本地 | QMT | 是否一致 |
|------|------|-----|---------|
| 选股top5 | ? | ? | |
| 换手率数值 | ? | ? | |
| 流通市值 | ? | ? | |
| 交易记录 | ? | ? | |
| 最终收益 | ? | ? | |

**如果不一致**：
1. 先查数据（price/vol/float_shares）
2. 再查逻辑（过滤规则/风控触发/仓位计算）
3. 最后查执行（buy_value vs buy/整手取整/资金不足跳过）

