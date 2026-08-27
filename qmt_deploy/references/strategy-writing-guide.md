# QMT 策略编写指南

> 基于 fkchaos/a-share-quant-sim 项目实战经验。
> QMT 内置 Python 3.6.8，所有代码必须兼容。

---

## 目录

1. [策略文件结构](#1-策略文件结构)
2. [核心API速查](#2-核心api速查)
3. [仓位管理](#3-仓位管理)
4. [风控逻辑](#4-风控逻辑)
5. [选股逻辑](#5-选股逻辑)
6. [配置管理](#6-配置管理)
7. [新增策略步骤](#7-新增策略步骤)
8. [单位与数据源](#8-单位与数据源)
9. [调试方法](#9-调试方法)
10. [踩坑清单](#10-踩坑清单)

---

## 1. 策略文件结构

每个策略由两个文件组成：

### 入口文件 `v{xx}_{name}_qmt.py`

负责定时触发和模式切换：

```python
#coding:gbk
"""v61c QMT Entry - dual trigger (backtest + live)."""
from qmt_adapter.v61c_strategy import on_signal

MODE = 'BACKTEST'  # BACKTEST=回测, LIVE=实盘

def init(C):
    if MODE == 'BACKTEST':
        pass  # handlebar 由QMT自动调用
    elif MODE == 'LIVE':
        from datetime import timedelta
        TIMER_TIME = '14:50:00'
        TIMER_INTERVAL = '1nDay'  # 或 timedelta(days=1)
        from datetime import datetime
        target = datetime.now().strftime('%Y%m%d') + '145000'
        C.schedule_run(on_timer, target, repeat_times=-1,
                       interval=TIMER_INTERVAL, name='signal_timer')

def on_timer(C):
    on_signal(C)

def handlebar(C):
    on_signal(C)
```

### 策略逻辑文件 `v{xx}_strategy.py`

核心业务逻辑，与触发方式解耦：

```python
#coding:utf-8
"""v61c Strategy - Low turnover + small cap."""
import numpy as np
import pandas as pd

_stock_pool = None
_stock_list = None
_account = None
_hold_days = {}
# ... 其他全局状态

def init(C):
    """初始化：加载股票池、构建缓存、读取持久化状态。"""
    global _stock_pool, _stock_list, _account
    from .qmt_data import ZZ1800_STOCKS
    from .trading import QmtAccount
    from .config import get_strategy_params, ACCOUNT_CONFIG

    _params = get_strategy_params('v61c')
    qmt_runner.qmt_init(C)

    _stock_pool = ZZ1800_STOCKS
    _stock_list = _stock_pool
    _account = QmtAccount(C)

    # 恢复持久化的 hold_days
    import json, os
    _persist_path = os.path.join(os.path.dirname(__file__), '_hold_days.json')
    try:
        with open(_persist_path, 'r') as f:
            _data = json.load(f)
            _hold_days = _data.get('hold_days', {})
    except Exception:
        _hold_days = {}

def on_signal(C):
    """每次触发的核心逻辑：风控→选股→下单。"""
    # 1. 增加持有天数
    for code in list(_hold_days.keys()):
        _hold_days[code] = _hold_days.get(code, 0) + 1

    # 2. 风控检查（止盈/止损/超时）
    from . import qmt_runner
    sold = qmt_runner.check_risk(C, _account, _hold_days, _risk_config, bar_date=today)
    for code in sold:
        _hold_days.pop(code, None)

    # 3. 选股
    selected = _select_stocks(C)

    # 4. 计算仓位并买入
    target = {}
    for code in buy_list:
        target[code] = max_per_stock  # 单只股票的目标权重
    qmt_runner.execute_buy(C, _account, target, bar_date=today, capital=_params['capital'])

    # 5. 持久化 hold_days
    import json, os
    _persist_path = os.path.join(os.path.dirname(__file__), '_hold_days.json')
    with open(_persist_path, 'w') as f:
        json.dump({'hold_days': _hold_days, 'last_date': today}, f)

def handlebar(C):
    """向后兼容：handlebar → on_signal。"""
    on_signal(C)
```

---

## 2. 核心API速查

### 交易函数

#### passorder - 下单

```python
passorder(
    opType,        # 操作类型: 23=股票买入, 24=股票卖出
    orderType,     # 下单方式: 1101=单股按数量, 1102=单股按金额
    accountid,     # 资金账号
    orderCode,     # 品种代码, 如 '000001.SZ'
    prType,        # 报价类型: 14=对手价, 5=最新价, 11=指定价
    price,         # 价格 (prType=14时传-1)
    volume,        # 下单量（单位由orderType末位决定: 1=股, 2=手, 3=元）
    strategyName,  # 策略名称（用于区分不同策略的委托）
    quickTrade,    # 快速下单: 0=逐K线等待(回测), 1=立即触发(实盘)
    userOrderId,   # 投资备注
    ContextInfo    # 策略上下文
)
```

**常用组合：**
```python
# 买入（对手价，按股数）
passorder(23, 1101, account, '000001.SZ', 14, -1, 1000, '策略名', 0, '备注', C)

# 卖出（对手价，按股数）
passorder(24, 1101, account, '000001.SZ', 14, -1, 1000, '策略名', 0, '备注', C)
```

> ⚠️ **回测必须用 quickTrade=0**，实盘用 quickTrade=1

#### get_trade_detail_data - 查询交易数据

```python
# 查询持仓
positions = get_trade_detail_data(accountID, 'stock', 'position')

# 查询委托
orders = get_trade_detail_data(accountID, 'stock', 'order')

# 查询成交
deals = get_trade_detail_data(accountID, 'stock', 'deal')

# 查询账户
accounts = get_trade_detail_data(accountID, 'stock', 'account')
```

**Position 对象字段：**
| 字段 | 类型 | 说明 |
|------|------|------|
| `m_strInstrumentID` | string | 股票代码 |
| `m_strInstrumentName` | string | 证券名称 |
| `m_nVolume` | int | 持仓量（**股**） |
| `m_nCanUseVolume` | int | 可用数量（**股**） |
| `m_dOpenPrice` | float | 成本价（**元**） |
| `m_dInstrumentValue` | float | 市值（**元**） |
| `m_dPositionCost` | float | 持仓成本（**元**） |
| `m_dPositionProfit` | float | 浮动盈亏（**元**） |

**Account 对象字段：**
| 字段 | 类型 | 说明 |
|------|------|------|
| `m_dBalance` | float | 总资产（**元**） |
| `m_dAvailable` | float | 可用金额（**元**） |
| `m_dAssureAsset` | float | 净资产（**元**） |
| `m_dInstrumentValue` | float | 总市值（**元**） |

### 行情函数

#### get_market_data_ex - 获取K线数据

```python
data = C.get_market_data_ex(
    fields=['open', 'high', 'low', 'close', 'volume', 'amount'],
    stock_code=['000001.SZ'],
    period='1d',         # 1d=日线, 1m=分钟
    start_time='',       # 起始时间
    end_time='20260827', # ⚠️ 必须传！不传返回今天价格
    count=10,            # K线数量, -1=全部
    dividend_type='front',  # 前复权
    subscribe=True        # ⚠️ 非主图品种必须True
)
# 返回: {code: DataFrame(index=date, columns=[open,high,low,close,volume,amount])}
```

> ⚠️ **必须传 end_time**，否则返回今天价格而非bar日期价格
> ⚠️ **非主图品种必须 subscribe=True**，否则返回空

#### get_market_data - 简化版行情

```python
# 获取单只股票最新价
data = C.get_market_data(['close'], ['000001.SZ'], period='1d', count=1)
price = data['close'].iloc[-1]
```

### 系统函数

#### get_bar_timetag - 获取bar时间戳

```python
# 获取当前bar的日期（避免用datetime.now()）
timetag = C.get_bar_timetag(C.barpos)
# 返回毫秒时间戳，需转换
from datetime import datetime
date = datetime.fromtimestamp(timetag / 1000).strftime('%Y%m%d')
```

#### schedule_run - 定时器（实盘模式）

```python
from datetime import datetime, timedelta

# target 必须是字符串 'YYYYMMDDHHMMSS'
target = datetime.now().strftime('%Y%m%d') + '145000'

C.schedule_run(
    on_timer,           # 回调函数
    target,             # 首次触发时间（字符串）
    repeat_times=-1,    # -1=无限重复
    interval=timedelta(days=1),  # 间隔（也支持 '1nDay' 字符串）
    name='signal_timer' # 定时器名称
)
```

> ⚠️ **target 必须是字符串**，不是 datetime 对象
> ⚠️ **schedule_run 对过去时间会立即触发**，不需要 +1 天逻辑

#### get_instrument_detail - 获取股票信息

```python
detail = C.get_instrument_detail('000001.SZ')
# 返回 dict，包含:
# 'IndustryClassification': 行业分类
# 'FloatVolume': 流通股本
# 'Volume': 总股本
# ...
```

---

## 3. 仓位管理

### 配置参数

```python
# config.py
STRATEGIES = {
    'v61c': {
        'capital': 100000,       # 策略独立资金池（元）
        'max_holdings': 5,       # 最多持有几只
        'max_per_stock': 0.25,   # 单只最大仓位占比
        'max_daily_buy': 5,      # 每日最多买入几只
    },
}
```

### 买入金额计算

```python
# qmt_runner.py execute_buy()
buy_amount = capital * weight    # 目标金额 = 资金池 × 权重
buy_amount = min(buy_amount, available * 0.95)  # 不超过可用现金的95%
lots = int(buy_amount / price / 100) * 100       # 取整到手（100股）
```

### Per-Strategy 持仓隔离

```python
# config.py
PER_STRATEGY_POSITIONS = True   # 开启每策略独立持仓

# 开启时：
# - 买入/卖出同时更新 QMT账户 + 本地JSON
# - check_risk / max_holdings 只看自己的JSON
# - 文件: _positions_{strategy}.json
# - JSON从空开始，只记录自己的买卖
```

**持仓JSON格式：**
```json
{
  "positions": {
    "000030.SZ": {"shares": 1000, "cost_price": 4.24, "buy_date": "20260827"}
  }
}
```

---

## 4. 风控逻辑

### 标准风控（check_risk）

```python
# qmt_runner.py check_risk()
risk_config = {
    'stop_loss': -0.08,     # 止损线: -8%
    'take_profit': 0.25,    # 止盈线: +25%
    'hold_days_max': 5,     # 最大持有天数
}

# 检查逻辑:
# 1. 当天买入不检查（T+1）
# 2. PnL <= stop_loss → 卖出
# 3. PnL >= take_profit → 卖出（涨停不卖）
# 4. hold_days >= hold_days_max → 卖出
```

### v61c 到期续持逻辑

```python
# v61c 特有：到期不直接卖，查排名决定续/卖
if hold_days >= rebalance_days:
    # 计算全市场排名（低换手+小市值）
    ranked_codes = _calc_ranking(C)
    if code in ranked_codes:
        # 还在排名内 → 续持，重置天数
        _hold_days[code] = 0
    else:
        # 掉出排名 → 卖出
        _account.sell_all(code)
```

### v75j 广度过滤

```python
# v75j 特有：根据市场广度调整仓位
breadth = _calc_breadth(C)  # 科技股中收盘价>MA20的比例

if breadth < 0.30:
    return  # 空仓不买
elif breadth < 0.50:
    # 线性缩减仓位
    scaled_slots = max(1, int(max_holdings * breadth / 0.50))
    slots = min(slots, scaled_slots)
```

---

## 5. 选股逻辑

### v61c：低换手+小市值

```python
def _select_stocks(C):
    # 1. 获取K线数据
    _kl = get_kline_data_multi(C, _stock_list, count=7)

    # 2. 计算因子
    for code in _stock_list:
        fs = FLOAT_SHARES[code]
        vol = df['volume'].values  # QMT volume = 股（不是手！）
        close = df['close'].values

        # 换手率 = volume(股) / float_shares(股)
        _turn[code] = np.mean(vol[-5:]) / fs if fs > 0 else 999

        # 市值 = close * float_shares
        _mcap[code] = close[-1] * fs if close[-1] > 0 else float('inf')

    # 3. Rank评分（等权50/50）
    _scores = pd.Series(0.0, index=codes)
    _scores += (1 - _ts.rank(ascending=True, pct=True))  # 低换手高分
    _scores += (1 - _ms.rank(ascending=True, pct=True))  # 小市值高分

    # 4. 取Top N
    return _scores.sort_values(ascending=False).head(max_holdings).index.tolist()
```

### v75j：科技趋势+流动性

```python
def _select_stocks(C, breadth):
    # 1. 过滤非科技股
    tech_stocks = [c for c in _stock_list if c in _tech_codes]

    # 2. 计算流动性因子（float_shares越大越流动）
    for code in tech_stocks:
        fs = FLOAT_SHARES[code]
        if fs <= 0:
            continue
        _liq[code] = fs

    # 3. 按流动性排序
    selected = sorted(_liq.keys(), key=lambda c: _liq[c], reverse=True)

    # 4. 过滤已持仓 + 股价上限
    held_codes = set(p['code'] for p in holdings)
    buy_list = [c for c in selected
                if c not in held_codes
                and _get_price(C, c) < 300]

    return buy_list[:slots]
```

---

## 6. 配置管理

所有参数集中到 `config.py`，按策略名分组：

```python
# qmt_adapter/config.py

STRATEGIES = {
    'v61c': {
        # 风控
        'stop_loss': -0.08,
        'take_profit': 0.25,
        'hold_days_max': 5,
        # 仓位
        'capital': 100000,
        'max_holdings': 5,
        'max_per_stock': 0.25,
        # v61c特有
        'rebalance_days': 5,
        'sell_out_of': 15,
        'max_daily_buy': 5,
    },
    'v75j': {
        'stop_loss': -0.08,
        'take_profit': 0.25,
        'hold_days_max': 20,
        'capital': 100000,
        'max_holdings': 3,
        'max_per_stock': 0.35,
        # v75j特有
        'breadth_high': 0.50,
        'breadth_low': 0.30,
        'max_daily_buy': 3,
    },
}
```

**读取方式：**
```python
from .config import get_strategy_params
_params = get_strategy_params('v61c')
max_holdings = _params.get('max_holdings', 5)
```

---

## 7. 新增策略步骤

### 1. 创建策略文件

```
qmt_adapter/v{xx}_strategy.py    # 策略逻辑
v{xx}_qmt.py                      # 入口文件
```

### 2. 在 config.py 注册

```python
STRATEGIES = {
    'v{xx}': {
        'stop_loss': -0.08,
        'take_profit': 0.25,
        'hold_days_max': 10,
        'capital': 100000,
        'max_holdings': 3,
        'max_per_stock': 0.35,
        # 你的特有参数
    },
}
```

### 3. 实现策略逻辑

参照已有策略文件，实现：
- `init(C)` - 初始化
- `on_signal(C)` - 核心逻辑
- `_select_stocks(C)` - 选股
- `handlebar(C)` - 向后兼容

### 4. 注册到诊断脚本（可选）

```python
# qmt_diagnostic.py
STRATEGIES_TO_TEST = ['v{xx}']
```

### 5. 测试验证

```bash
# 回测模式
python3 v{xx}_qmt.py --mode BACKTEST --start 20260801 --end 20260827

# 实盘模式（需QMT环境）
python3 v{xx}_qmt.py --mode LIVE
```

---

## 8. 单位与数据源

### QMT 内部单位（统一）

| 数据 | 单位 | 说明 |
|------|------|------|
| passorder volume | 股 | orderType=1101时 |
| get_market_data_ex volume | 股 | K线成交量 |
| Position m_nVolume | 股 | 持仓量 |
| Position m_dOpenPrice | 元 | 成本价 |
| Account m_dAvailable | 元 | 可用金额 |

### 与模拟盘数据源差异

| 数据源 | volume单位 | 换手率计算 |
|--------|-----------|-----------|
| 腾讯API（模拟盘DB） | **手** | `volume * 100 / float_shares` |
| QMT | **股** | `volume / float_shares` |
| BaoStock | **股** | `volume / float_shares` |

> ⚠️ **腾讯K线API volume单位是手（1手=100股），不是股！**
> 计算换手率需 `volume * 100 / float_shares`，否则偏差100倍

---

## 9. 调试方法

### 添加调试输出

```python
_DEBUG = True  # 开关

if _DEBUG:
    print('[BAR] date=%s' % today)
    print('[V61C] slots=%d, holdings=%d' % (slots, current_count))
    print('[BUY] %s: amount=%.0f price=%.2f' % (code, buy_amount, price))
```

### QMT诊断脚本

```bash
python3 qmt_diagnostic.py
```

输出：
- 账户状态（现金/持仓）
- K线数据可用性
- 持仓天数检查
- 止盈止损状态

### 检查持仓JSON

```python
import json
with open('_positions_v61c.json', 'r') as f:
    print(json.dumps(json.load(f), indent=2))
```

### 检查QMT持仓

```python
positions = get_trade_detail_data(account, 'stock', 'position')
for p in positions:
    print(f'{p.m_strInstrumentID}: {p.m_nVolume}股 @ {p.m_dOpenPrice:.2f}')
```

---

## 10. 踩坑清单

### 环境相关

1. **Python 3.6.8** — 不能用 `:=` walrus、`dict | dict`、f-string `=`号
2. **GBK编码** — 策略文件第一行必须 `#coding:gbk` 或 `#coding:utf-8`
3. **QMT环境不能import sqlite** — 改用 `C.get_instrument_detail()` 获取股票信息

### 行情相关

4. **get_market_data_ex 必须传 end_time** — 不传返回今天价格而非bar日期价格
5. **非主图品种必须 subscribe=True** — 否则返回空
6. **QMT volume = 股** — 不是手（与腾讯API不同）

### 交易相关

7. **passorder quickTrade=0** — 回测必须用0，实盘用1
8. **prType=14 对手价** — 最常用，price传-1
9. **orderType=1101** — 单股按数量（股），1102=按金额（元）
10. **passorder 是全局函数** — 不是 ContextInfo 的方法
11. **get_trade_detail_data 也是全局函数** — 同上

### 时间相关

12. **不要用 datetime.now()** — 回测中所有bar返回同一个系统时间
13. **用 get_bar_timetag(C.barpos)** — 获取当前bar的真实日期
14. **schedule_run 的 target 必须是字符串** — `'YYYYMMDDHHMMSS'` 格式
15. **schedule_run 对过去时间立即触发** — 不需要 +1 天逻辑

### 持仓相关

16. **m_dOpenPrice 是真实成本** — 不要自己记录买入价覆盖它
17. **m_nVolume 是总持仓** — 包含冻结部分
18. **m_nCanUseVolume 是可用数量** — T+1后才可用

### 架构相关

19. **策略参数放 config.py** — 不要硬编码在策略文件里
20. **每策略独立资金池** — 不要用账户总资产算仓位
21. **PER_STRATEGY_POSITIONS** — 多策略共享账户时开启隔离
22. **持仓JSON从空开始** — 不要从账户同步，避免混淆

---

## 附录：文件结构

```
qmt_deploy/
├── v61c_qmt.py              # v61c 入口
├── v75j_qmt.py              # v75j 入口
├── qmt_diagnostic.py        # 诊断脚本
├── qmt_adapter/
│   ├── __init__.py
│   ├── config.py            # 集中配置
│   ├── data.py              # 行情数据获取
│   ├── trading.py           # passorder封装
│   ├── qmt_runner.py        # 通用执行器（check_risk, execute_buy）
│   ├── qmt_data.py          # 股票池、流通股数据
│   ├── v61c_strategy.py     # v61c 策略逻辑
│   ├── v75j_strategy.py     # v75j 策略逻辑
│   ├── _hold_days.json      # v61c 持有天数持久化
│   ├── _positions_v61c.json # v61c 持仓隔离（PER_STRATEGY_POSITIONS）
│   └── _positions_v75j.json # v75j 持仓隔离
└── references/
    └── strategy-writing-guide.md  # 本文件
```
