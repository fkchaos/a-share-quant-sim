# QMT 已知坑（Known Pitfalls）

> 从 SKILL.md 提取的坑点集合，供快速查阅。

10. **🔴 回测初始total_value=0导致不下单**（2026-08-25 踩坑）— 回测开始时`get_total_value()`可能返回0（账户资产未初始化），但`get_cash()`返回正确的初始资金。如果`execute_buy`里有`if total_value <= 0: return`检查，会跳过所有买入。**用available cash兜底**：
```python
total_value = account.get_total_value()
if total_value <= 0:
    total_value = available  # fallback to available cash
```

11. **🔴 is_rebalance_day不能用C.account_id**（2026-08-25 踩坑）— QMT的`__PyContext`对象没有`account_id`属性。在qmt_runner等adapter模块中获取account需要**frame遍历**，不能直接读C的属性。参考坑0h4的`_find_account_from_frames()`方法。

12. **🔴 不要在QMT环境执行自定义验证脚本**（2026-08-25 踩坑）— QMT内置Python是隔离的，不能运行用户自定义脚本来验证环境。当主公说"QMT的Python能执行自定义脚本吗?"时，应该意识到QMT环境不可控。**验证只能通过策略本身的print输出**，或者在服务器端用标准Python验证。

13. **🔴 QMT exec()加载脚本时`__file__`未定义且relative imports失败**（2026-09-02 踩坑）— QMT通过`exec()`加载策略脚本时：
    - `__file__`变量**未定义**（NameError），不能用`os.path.dirname(__file__)`定位文件
    - relative imports（`from .trading import ...`）**失败**，因为模块名是`_M_XXXX_YYYY`（非package）
    
    **错误示范**：
    ```python
    # ❌ __file__ not defined in QMT exec environment
    persist_path = os.path.join(os.path.dirname(__file__), '_hold_days.json')
    
    # ❌ relative imports fail: '_M_XXXX_YYYY' is not a package
    from .trading import QmtAccount
    from .config import ACCOUNT_CONFIG
    ```
    
    **正确做法**：
    ```python
    # ✅ Fallback for __file__
    try:
        _SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        _SCRIPT_DIR = r'D:\software\QMT-SIMU\python'  # QMT default path
    
    persist_path = os.path.join(_SCRIPT_DIR, '_hold_days.json')
    
    # ✅ Use absolute imports (not relative)
    from qmt_adapter.trading import QmtAccount
    from qmt_adapter.config import ACCOUNT_CONFIG
    ```
    
    **⚠️ 不要加sys.path操作**（2026-09-02 主公纠正）— QMT自动把脚本目录加入sys.path，v75j_qmt.py/v61c_qmt.py都直接用`from qmt_adapter.xxx import ...`，没加sys.path。如果现有脚本能import，说明不需要额外路径操作。
    
    **排查方法**：如果报错`NameError: name '__file__' is not defined`或`ModuleNotFoundError: No module named '_M_XXXX_YYYY.trading'`，就是这两个问题。

14. **🔴 passorder sell方法缺闭合括号（2026-09-02 发现）**— `trading.py`的sell方法中`passorder()`调用从未正确闭合，导致后续`import time`报`SyntaxError: invalid syntax`。这是原始代码的bug，不是修改引入的。
    
    **症状**：`SyntaxError: invalid syntax (trading.py, line 302)` 指向`import time as _time`
    
    **根因**：`passorder()`调用缺少`)`闭合，后面的字典赋值和`import`语句被当成passorder的参数
    
    **修复**：
    ```python
    # ❌ 缺闭合括号
    trading.passorder(
        24, 1101, account_id, stock_code, 14, -1, shares, 'V61C', 0, remark
    # 这里缺少 ), 后面的代码全部语法错误
    
    # ✅ 正确闭合
    trading.passorder(
        24, 1101, account_id, stock_code, 14, -1, shares, 'V61C', 0, remark,
        self.C                  # ContextInfo
    )
    ```

14. **🔴 passorder sell方法缺闭合括号（2026-09-02 发现）**— `trading.py`的sell方法中`passorder()`调用从未正确闭合，导致后续`import time`报`SyntaxError: invalid syntax`。这是原始代码的bug，不是修改引入的。
    
    **症状**：`SyntaxError: invalid syntax (trading.py, line 302)` 指向`import time as _time`
    
    **根因**：`passorder()`调用缺少`)`闭合，后面的字典赋值和`import`语句被当成passorder的参数
    
    **修复**：
    ```python
    # ❌ 缺闭合括号
    trading.passorder(
        24, 1101, account_id, stock_code, 14, -1, shares, 'V61C', 0, remark
    # 这里缺少 ), 后面的代码全部语法错误
    
    # ✅ 正确闭合
    trading.passorder(
        24, 1101, account_id, stock_code, 14, -1, shares, 'V61C', 0, remark,
        self.C                  # ContextInfo
    )
    ```

14. **🔴 排查报错不要凭假设，要看实际错误信息**（2026-08-25 踩坑）— 当报错`'list' object has no attribute 'keys'`但代码行是`list(C.stock_pool)`时，不要坚持"一定是.pyc缓存"的假设。`list()`不会调用`.keys()`，说明错误来源在别处。主公纠正："人家报错提过 .keys() 吗?"——实际是ContextInfo属性赋值时自动校验导致的。**排查路径：看报错→验证代码→找真正触发点，不要跳过验证直接下结论。**

### 🔴 get_trade_detail_data query_type命名错误（2026-08-26 踩坑）

**最隐蔽的bug之一**：query_type参数名与直觉不同，用错不报错、静默返回空列表。

| 用途 | ❌ 错误（静默返回空） | ✅ 正确 |
|------|---------------------|---------|
| 持仓 | `'stockpositions'` | **`'POSITION'`** |
| 委托 | `'stockorders'` | **`'ORDER'`** |
| 成交 | `'deals'` | **`'DEAL'`** |
| 账户 | `'account'` | **`'ACCOUNT'`** |

**症状**：买入通过passorder成功执行，回测界面能看到持仓，但`get_holdings()`永远返回0，风控/卖出全部失效。

**排查方法**：在`_query`方法中打印返回结果长度：
```python
result = trading.get_trade_detail_data(self.account_id, self.account_type, query_type)
print('[QMT] query %s: %d results' % (query_type, len(result) if result else 0))
```
如果`account`有结果但`position`为空 → query_type写错了。

**验证来源**：QMT知识库`docs/qmt/knowledge/01-入门/快速开始.md`和`04-示例与FAQ/完整示例.md`中所有示例均用`'position'`、`'order'`、`'deal'`。

### ⚠️ buy_value vs buy 参数混淆（2026-08-26 踩坑）

`QmtAccount`有两个买入方法，参数含义不同：

| 方法 | 参数 | 说明 |
|------|------|------|
| `buy(code, shares, price)` | shares = **股数** | 直接指定买多少股 |
| `buy_value(code, amount, price)` | amount = **金额(元)** | 自动按price算股数，取整到100股 |

**踩坑**：`execute_buy`里写`account.buy(code, buy_amount, price)`，buy_amount=5000被当成5000股→远超资金→全部跳过→持仓为空。

```python
# ❌ 错误：5000被当成5000股
account.buy(code, buy_amount, price)  # buy_amount=5000 → 5000股×234=117万

# ✅ 正确：5000元自动转股数
account.buy_value(code, buy_amount, price)  # 5000元÷234÷100=200股(2手)
```

**execute_buy标准实现**：
```python
def execute_buy(C, account, target_weight):
    available = account.get_cash()
    total_value = account.get_total_value()
    if total_value <= 0:
        total_value = available

    for code, weight in target_weight.items():
        buy_amount = total_value * weight
        buy_amount = min(buy_amount, available * 0.95)
        if buy_amount < 5000:
            continue
        price = get_close_price(C, code)
        if price <= 0:
            continue
        account.buy_value(code, buy_amount, price)  # ← 必须用buy_value
```

## ⚠️ 仓位计算数学（2026-08-26 踩坑）

**5%仓位买不了高价股**：

```
10万资金 × 5% = 5000元/只
603986.SH 价格234.93元 → 5000÷234.93=21股 → 不够1手(100股) → 买入失败
```

**仓位与max_pos关系**：
```python
max_pos = 0.25  # 总仓位25%
max_holdings = 5
per_stock = max_pos / max_holdings  # = 0.05 = 5% per stock
```

| max_pos | max_holdings | 每只% | 10万/只 | 可买手数(234元股) |
|---------|-------------|-------|---------|-----------------|
| 0.25 | 5 | 5% | 5000 | 0手 ❌ |
| 0.50 | 5 | 10% | 10000 | 2手 ✅ |
| 0.50 | 3 | 16.7% | 16667 | 2手 ✅ |
| 1.00 | 5 | 20% | 20000 | 4手 ✅ |

**建议**：max_pos≥0.50，或先用价格过滤排除>50元的股票。

## ⚠️ 同日买入冷却（2026-08-26 新增）

per-stock rebalance模式下，如果第一次买入失败（shares=0），slots仍>0，每根bar都会重试选股+买入，浪费资源。

**解决方案**：记录上次买入尝试日期，同一天不重试。

```python
_last_buy_date = None

def handlebar(C):
    # ... 风控/per-stock超期 ...

    today = datetime.now().strftime('%Y-%m-%d')
    if _last_buy_date == today:
        return  # 同一天不重试

    # 选股+买入
    ...
    _last_buy_date = today
```

## ⚠️ 数据验证打印方案（2026-08-26 新增）

回测结果异常时，先验证QMT返回的数据是否正确。

**策略文件加`[DATA]`打印**（_select_stocks里）：
```python
if _DEBUG and len(turnover_scores) <= 5:
    print("[DATA] %s: price=%.2f vol_avg=%.0f float=%.0f turnover=%.4f%% mcap=%.1f" % (
        code, latest_close, avg_vol, fs,
        avg_turnover * 100, latest_close * fs / 1e8))
```

**data.py加`[KLINE]`打印**（get_kline_data_multi里）：
```python
if _dbg_count < 3:
    print("  [KLINE] %s: close=%.2f vol=%.0f amount=%.0f rows=%d" % (
        code, last_close, last_vol, last_amount, len(df)))
```

**关键判断**：
1. **vol_avg vs float**：如果`vol_avg/float`≈0.0005（0.05%），说明volume单位可能是手，实际换手率应为5%
2. **mcap vs 实际市值**：如603986.SH实际流通市值≈160亿，如果显示2644亿→FLOAT_SHARES数据有误
3. **最新bar的vol=0是正常现象**（2026-08-26 实测）：QMT回测中当天K线的volume/amount可能返回0（未收盘/数据延迟）。取均值时应排除最后1根bar

## ⚠️ QMT volume单位（已确认：股）

**结论**：QMT `get_market_data_ex` 返回的volume单位是**股**（不是手）。

**验证方法**（2026-08-26 实测）：
1. 本地SQLite用 `volume / float_shares` 计算换手率
2. QMT用同样公式，top10选股结果与本地一致（score和排名完全匹配）
3. 换手率值一致（如000001.SZ 0.0044%，603986.SH 0.0463%）
4. 如果volume是手，换手率应大100倍（4.6%），与本地结果不符

**⚠️ 最新bar的volume/amount可能为0**（2026-08-26 实测）：
QMT回测中，当天（最后一根K线）的volume和amount可能返回0（未收盘或数据延迟）。`get_kline_data_multi`取5天均值时应排除最后1根bar的0值。当前实现用 `vol[-n:]` 取最后n根，如果最后一根是0会拉低均值。**建议**：取倒数第2到第n+1根（排除最后一根）。

## ⚠️ Per-Stock Rebalance 模式（2026-08-26 新增）

**问题**：全局rebalance日（`is_rebalance_day`）有缺陷——风控卖出会重置整个组合的rebalance计时器，导致其他持仓被延迟调仓。

**场景推演**：
```
Day 0: 买入A、B、C
Day 3: A触发止损卖出 → last_trade=Day3 → 下次rebalance=Day8
Day 5: 本应rebalance，但被推迟到Day8
Day 8: B、C多持了3天才被调仓
```

**解决方案：per-stock rebalance**
- 每只股票独立跟踪hold_days
- 超过REBALANCE_DAYS自动卖出（不等全局rebalance日）
- 有空位就买，不等统一调仓日

```python
# handlebar流程（per-stock rebalance）
def handlebar(C):
    # 1. 每只股票 hold_days++
    for code in list(_hold_days.keys()):
        _hold_days[code] = _hold_days.get(code, 0) + 1

    # 2. 风控检查（SL/TP/HD）- 逐只触发
    qmt_runner.check_risk(C, _account, _hold_days, _risk_config)

    # 3. per-stock超期卖出
    holdings = _account.get_holdings()
    for p in holdings:
        if _hold_days.get(p['code'], 0) >= rebalance_days:
            _account.sell_all(p['code'])
            _hold_days.pop(p['code'], None)

    # 4. 有空位就买
    holdings = _account.get_holdings()
    slots = max_holdings - len([p for p in holdings if p['shares'] > 0])
    if slots <= 0:
        return
    selected = _select_stocks(C)
    # ... 买入逻辑
```

**与旧模式对比**：
| | 旧模式（全局rebalance） | 新模式（per-stock） |
|--|----------------------|-------------------|
| 卖出触发 | 只在rebalance日 | 风控随时 + 超期随时 |
| 买入触发 | 只在rebalance日 | 有空位就买 |
| 计时器 | 全组合共享（last_trade） | 每只股票独立 |
| 风控影响 | 重置全局计时器 | 不影响其他股票 |

## ⚠️ DEBUG开关模式（2026-08-26 新增，2026-08-31 更新）

**DEBUG开关必须放在入口文件的 CONFIG 块内**，用户直接改这一行即可。所有打印统一由外层DEBUG控制：

```python
# v61c_qmt.py（入口文件）
# ========== CONFIG ==========
MODE = 'LIVE'          # 'BACKTEST' or 'LIVE'
DEBUG = True           # master debug switch: controls ALL prints
TIMER_INTERVAL = 24 * 3600
TIMER_TIME = '145000'
# ===========================

from qmt_adapter.v61c_strategy import init as _init, on_signal as _on_signal, set_debug as _set_debug
from qmt_adapter.qmt_runner import set_risk_debug as _set_risk_debug

def init(C):
    _set_debug(DEBUG)         # → strategy._DEBUG
    _set_risk_debug(DEBUG)    # → trading._risk_debug
    _init(C)
```

**debug控制链路**：
```
入口文件 CONFIG: DEBUG=True
  ├── _set_debug(DEBUG)      → strategy._DEBUG
  ├── _set_risk_debug(DEBUG) → trading._risk_debug
  └── if DEBUG:              → 入口自身打印

qmt_diagnostic.py CONFIG: DEBUG=True
  └── if DEBUG:              → _diag_log (所有诊断输出)
```

**改一处 `DEBUG = False` 全部静默。**

**不要把DEBUG放在config.py**——config.py是共享配置，用户不该为了调试去改共享模块。入口文件是用户直接接触的文件。

**debug输出应覆盖的关键决策点**：
- init完成（pool大小、参数值、backtest模式检测）
- 风控触发（哪只股票、什么条件触发、持有天数）
- hold_days字典内容（increment后打印）
- 选股结果（候选数、top N及分数）
- 买入目标（代码、权重、lots计算结果）
- 广度/择时信号（v75j）
- 调仓/卖出触发（原因、天数）
- API查询结果（POSITION/ORDER/DEAL返回条数）
- get_holdings数据来源（API vs INTERNAL）

**所有debug输出必须包含bar日期**（2026-08-26 踩坑）：
```python
# ❌ 不知道是哪天的输出
print('[V61C] 5 slots available, selecting stocks...')

# ✅ 每条输出都带日期，方便对比QMT和本地
print('[%s][V61C] 5 slots available, selecting stocks...' % today)
```
格式：`[YYYY-MM-DD][策略名] 描述`

### ⚠️ buy_value静默失败（2026-08-26 踩坑）

`buy_value()`在shares=0时**不报错、不打印、直接返回**。调用方必须自己验证：

```python
# ❌ 静默失败，持仓永远为0
account.buy_value(code, 5000, 395.51)  # shares=0, nothing happens

# ✅ 先算lots，确认>=1再调用
lots = int(5000 / 395.51 / 100)  # = 0
if lots < 1:
    print('[BUY] SKIP %s: cannot afford 1 lot' % code)
else:
    account.buy_value(code, 5000, 395.51)
```

**execute_buy标准实现（含防重复下单+涨停过滤）：**
```python
def execute_buy(C, account, target_weight, bar_date='', capital=50000):
    """Common buy execution with duplicate prevention and limit-up filter."""
    bought = []
    available = account.get_cash()
    if available < 1000:
        return bought
    
    for code, weight in target_weight.items():
        # Duplicate order prevention
        for remark, o in _orders.items():
            if o['stock'] == code and o['status'] in ('pending', 'ordered', 'partial'):
                print('[BUY] SKIP %s: duplicate order pending' % code)
                break
        else:
            buy_amount = capital * weight
            buy_amount = min(buy_amount, available * 0.95)
            price = get_close_price(C, code, bar_date)
            lots = int(buy_amount / price / 100) if price > 0 else 0
            
            if buy_amount < 5000 or price <= 0 or lots < 1:
                continue
            
            account.buy_value(code, buy_amount, price)
            bought.append(code)
            available -= buy_amount
    
    return bought
```

**⚠️ 涨跌停过滤（2026-08-28 模拟盘对比发现）：**
模拟盘`account_runner.py`有涨停过滤逻辑：`close == high`时不买入。QMT版需要在`buy_list`生成阶段过滤：
```python
# Limit-up check: close == high means stock is at limit-up
# 在buy_list生成时过滤，不是在execute_buy
filtered = []
for c in selected:
    if c in held_codes:
        continue
    if c in kline_data:
        df = kline_data[c]
        if len(df) > 0:
            last_close = df['close'].iloc[-1]
            last_high = df['high'].iloc[-1]
            if last_close > 0 and last_high > 0 and last_close >= last_high:
                continue
    filtered.append(c)
buy_list = filtered[:slots]  # 无补位
```
QMT柜台会自动拒绝涨停买入，但提前过滤可以避免浪费仓位槽位。

### qmt_runner也需要独立debug开关

qmt_runner.py的`[RISK]`打印不应无条件输出，需要独立控制：
```python
# qmt_runner.py — 模块级开关
_risk_debug = False

def set_risk_debug(flag):
    global _risk_debug
    _risk_debug = flag

def check_risk(C, account, holding_days, risk_config=None):
    ...
    if _risk_debug:
        print('[RISK] check_risk: %d holdings' % len(positions))
    for p in positions:
        ...
        if _risk_debug:
            print('[RISK] %s: cost=%.2f cur=%.2f pnl=%.2f%% ...' % (...))
```

入口文件同时调用两个setter：
```python
from qmt_adapter.v61c_strategy import set_debug
from qmt_adapter.qmt_runner import set_risk_debug

def init(C):
    set_debug(DEBUG)
    set_risk_debug(DEBUG)
    _init(C)
```

**debug输出应覆盖的关键决策点**：
- init完成（pool大小、参数值、backtest模式检测）
- 风控触发（哪只股票、什么条件触发、持有天数）
- hold_days字典内容（increment后打印）
- 选股结果（候选数、top N及分数）
- 买入目标（代码、权重、lots计算结果）
- 广度/择时信号（v75j）
- 调仓/卖出触发（原因、天数）
- API查询结果（POSITION/ORDER/DEAL返回条数）
- get_holdings数据来源（API vs INTERNAL）

**所有debug输出必须包含bar日期**（2026-08-26 踩坑）：
```python
# ❌ 不知道是哪天的输出
print('[V61C] 5 slots available, selecting stocks...')

# ✅ 每条输出都带日期，方便对比QMT和本地
print('[%s][V61C] 5 slots available, selecting stocks...' % today)
```
格式：`[YYYY-MM-DD][策略名] 描述`

### 🔴 `get_market_data_ex`回测用法（2026-08-26 多轮诊断实验+官方文档确认）

**end_time语义：不传end_time返回最新价，传end_time返回指定日期收盘价。买入用最新价（不传end_time），风控PnL用bar日期收盘价（传end_time）。**

**`subscribe`参数的正确理解：**
- `subscribe=True`（默认）：订阅+读取，支持**任意股票**（包括非主图品种）
- `subscribe=False`：只读本地缓存，**仅主图品种有效**，非主图品种返回空

**官方示例**（ThinkTrader API docs §4.1）用的是`subscribe=False`，但那是针对主图品种（`C.stock`）。对于非主图品种（我们要交易的股票），必须用`subscribe=True`。

**正确用法（非主图品种）：**
```python
# subscribe=True (默认) + end_time=bar_date → 获取任意股票的bar日期价格
data = C.get_market_data_ex(
    ['close'], [code],
    period='1d', count=1,
    end_time=bar_date    # 关键：限制到当前bar日期
    # subscribe默认True，不需要显式传
)
```

**⚠️ get_close_price买入时不传end_time（2026-08-28 修复）：**
买入应使用最新价，不传end_time。风控PnL才传bar_date作为end_time获取收盘价。
```python
def get_close_price(C, code, bar_date=''):
    """Get latest price for buying (no end_time)."""
    try:
        C.subscribe_quote(code, period='1d', count=1)
        data = C.get_market_data_ex(['close'], [code], period='1d', count=1)
        if code in data and len(data[code]) > 0:
            return data[code]['close'].iloc[-1]
    except Exception as e:
        print('[DEBUG] get_close_price error: %s %s' % (code, e))
    return 0.0
```
**症状**：买入时price=0.00，lots=0，所有买入被跳过。

**⚠️ 两个策略共用_hold_days.json冲突（2026-08-28 发现）：**
v61c和v75j共用同一个`_hold_days.json`文件，但hold_days逻辑不同（v61c用rebalance_days，v75j用hold_days_max）。一个策略init()重置hold_days会影响另一个。
**修复方向**：分离为`_hold_days_v61c.json`和`_hold_days_v75j.json`。

**诊断实验过程（走了弯路）：**

| 尝试 | 参数 | 结果 | 原因 |
|------|------|------|------|
| 1 | 无end_time | 返回今天价格73.62 | ❌ 缺end_time |
| 2 | subscribe=False + 无end_time | 返回今天价格73.62 | ❌ 缺end_time |
| 3 | subscribe=False + end_time | 非主图品种返回0 | ❌ subscribe=False仅主图有效 |
| 4 | subscribe_quote + subscribe=False + end_time | 非主图品种返回0 | ❌ subscribe_quote在回测中不加载数据 |
| **5** | **subscribe=True + end_time** | **正确返回bar日期价格46.72** | ✅ |

**⚠️ 错误示范（被主公纠正）：**
```python
# ❌ 绕过方案：自己记录买入价覆盖m_dOpenPrice
