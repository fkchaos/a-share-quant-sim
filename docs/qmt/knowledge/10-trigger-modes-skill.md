## QMT 触发模式（2026-08-27 新增）

QMT 有 3 种触发方式，策略代码通过不同的回调函数被调用：

### 1. handlebar（K线回调）— 当前在用

```python
def init(C):
    C.set_universe(stock_list)  # 必须！

def handlebar(C):
    # 每根K线收盘后触发一次
    # 周期由QMT界面设置（tick≈3秒 / 1m / 5m / 1d）
    ...
```

- ✅ 回测和实盘都可用
- ❌ 日线策略 = 全天不出信号（除非 quickTrade=1 或 do_order()）
- ❌ 集合竞价阶段（9:15-9:25）tick没来，handlebar不触发

### 2. schedule_run（定时器）— 仅实盘（推荐）

```python
def init(C):
    from datetime import datetime, timedelta
    now = datetime.now()
    target = datetime.strptime(now.strftime('%Y%m%d') + '093000', '%Y%m%d%H%M%S')
    interval = timedelta(seconds=5)
    C.schedule_run(on_timer, target, repeat_times=-1,
                    interval=interval, name='my_timer')

def on_timer(C):
    # 按interval间隔触发
    ...
```

**参数**：
| 参数 | 类型 | 说明 |
|------|------|------|
| `func` | Callable | 回调函数，入参ContextInfo |
| `time_point` | **str** | 首次触发时间，**必须是字符串** `yyyymmddHHMMSS`（如 `'20260827145000'`），不是 datetime 对象 |
| `repeat_times` | int | 触发次数，`-1`=无限 |
| `interval` | timedelta | 重复间隔 |
| `name` | str | 任务组名，同名不覆盖，按组名取消 |

**返回值**：int，任务号（可用于 `cancel_schedule_run(task_id)` 取消）

- ✅ 实盘可用，支持集合竞价
- ✅ 支持任务分组（`name`）和取消（`cancel_schedule_run`）
- ❌ **回测无效**（模型回测时schedule_run不触发）
- ⚠️ 定时器随策略结束而结束

**旧版 `run_time()` 仍可用但不推荐**：
```python
# 旧版（3参数，无取消/分组）
C.run_time('on_timer', '5nSecond', '2026-08-27 09:30:00')
```

### 3. subscribe_quote（订阅行情回调）— 仅实盘

```python
def init(C):
    C.subscribe_quote('600000.SH', period='tick', callback=on_tick)

def on_tick(C):
    # 数据一到就触发，不等K线走完
    ...
```

还有批量版本：`subscribe_whole_quote(['SH', 'SZ'], callback=on_tick)`

- ✅ tick级实时，最低延迟
- ❌ 回测中不生效（subscribe_quote在回测中不加载数据）

### ⚠️ 核心矛盾：回测和实盘触发方式不同

| | handlebar | run_time | subscribe |
|--|----------|----------|-----------|
| 回测 | ✅ | ❌ | ❌ |
| 实盘 | ✅ | ✅ | ✅ |

**能回测的只有 handlebar。** run_time/subscribe 只能在实盘验证。

**当前方案**：handlebar + 外部 cron 模拟定时（9:40/11:40/14:40 信号cron）。

**统一框架方向**：策略逻辑只写一份 `on_signal()`，回测引擎通过handlebar调用，QMT实盘通过schedule_run调用。设计文档：`.hermes/plans/2026-08-27_qmt_unified_framework.md`

### ✅ 双触发模式（已实现 2026-08-27）

**核心思路**：触发方式不同，业务逻辑走同一份代码。

**改动量极小**（只改4个文件，业务逻辑一行不动）：

**策略文件** (`vxx_strategy.py`)：
```python
# on_signal() is the sole business logic entry point
def on_signal(C):
    """Core business logic - shared by both handlebar and run_time triggers."""
    # ... all business logic ...

# NO handlebar() in strategy file - it's dead code
# Entry file's handlebar() calls _on_signal(C) directly
```

**入口文件** (`vxx_qmt.py`)：
```python
# ========== CONFIG ==========
MODE = 'BACKTEST'       # 'BACKTEST' or 'LIVE'
TIMER_INTERVAL = 24 * 3600  # seconds (1 day = 86400)
TIMER_TIME = '145000'  # HHMMSS format
# =============================

# Validate MODE — raise on invalid to avoid silent no-op
_VALID_MODES = ('BACKTEST', 'LIVE')
if MODE not in _VALID_MODES:
    raise ValueError("MODE must be %s, got %r" % (' or '.join(_VALID_MODES), MODE))

from qmt_adapter.xxx_strategy import (
    init as _init, on_signal as _on_signal, set_debug as _set_debug,
)
from qmt_adapter.qmt_runner import set_risk_debug as _set_risk_debug

def init(C):
    _set_debug(DEBUG)
    _set_risk_debug(DEBUG)
    _init(C)
    if MODE == 'LIVE':
        from datetime import datetime, timedelta
        now = datetime.now()
        today_str = now.strftime('%Y%m%d')
        target = datetime.strptime(today_str + TIMER_TIME, '%Y%m%d%H%M%S')
        # No need for +1 day — schedule_run fires immediately if target is in the past
        interval = timedelta(seconds=86400)
        C.schedule_run(on_timer, target, repeat_times=-1,
                        interval=interval, name='signal_timer')

def handlebar(C):
    """Backtest mode: triggered on each bar close."""
    if MODE == 'BACKTEST':
        _on_signal(C)

def on_timer(C):
    """Live mode: triggered by schedule_run timer."""
    if MODE == 'LIVE':
        _on_signal(C)
```

**MODE 常量必须大写**（2026-08-27 主公要求）：`'BACKTEST'` / `'LIVE'`，不用小写。

**必须加校验**（空值/非法值会静默跳过所有逻辑，不报错）：
```python
_VALID_MODES = ('BACKTEST', 'LIVE')
if MODE not in _VALID_MODES:
    raise ValueError("MODE must be %s, got %r" % (' or '.join(_VALID_MODES), MODE))
```

**Timer interval 可选值**：
| 参数 | 含义 | 适用场景 |
|------|------|---------|
| `500nMilliSecond` | 每500毫秒 | 超高频 |
| `5nSecond` | 每5秒 | 盘中监控 |
| `1nMinute` | 每1分钟 | 多次信号/盘中巡检 |
| `1nHour` | 每1小时 | 少量定时任务 |
| `1nDay` | 每1天 | 日频调仓（当前默认） |

**⚠️ schedule_run 支持取消，run_time 不支持**（2026-08-27 确认）：
```python
# schedule_run（推荐）— 支持取消
C.schedule_run(on_timer, target, repeat_times=-1,
                interval=timedelta(days=1), name='signal_timer')
# 取消
C.cancel_schedule_run('signal_timer')  # 按组名
C.cancel_schedule_run(1)              # 按任务号

# run_time（旧版）— 没有取消方法
C.run_time('on_timer', '1nDay', startTime)
# 只能等策略停止，没有手动停止方法
```

**一天多次触发**：用方案B（注册一个高频定时器，策略里自己过滤时间）：
```python
# 入口文件：注册一个每分钟触发的定时器
if MODE == 'LIVE':
    from datetime import datetime, timedelta
    now = datetime.now()
    target = datetime.strptime(now.strftime('%Y%m%d') + '093000', '%Y%m%d%H%M%S')
    # No need for +1 day — schedule_run fires immediately if target is in the past
    interval = timedelta(seconds=86400)
                    interval=timedelta(minutes=1), name='signal_timer')

# 策略文件：自己判断当前时间是否在信号时间
def on_signal(C):
    now = datetime.now().strftime('%H%M')
    if now not in ('0940', '1140', '1440'):
        return  # not signal time, skip
    # actual business logic...
```

**切换方式**：只改入口文件顶部 `MODE = 'BACKTEST'` → `MODE = 'LIVE'`

**注意**：schedule_run/run_time 回测不可验证，只能实盘验证。handlebar 是唯一能回测的触发方式。

### 🔴 schedule_run time_point 必须是字符串（2026-08-27 踩坑）

`schedule_run` 的 `time_point` 参数**必须是字符串** `'yyyymmddHHMMSS'`，不是 datetime 对象。传 datetime 对象不会报错，但定时器行为异常（可能不触发或 interval 错误）。

```python
# ❌ 传 datetime 对象 → 定时器不触发
from datetime import datetime, timedelta
target = datetime.strptime(now.strftime('%Y%m%d') + '145000', '%Y%m%d%H%M%S')
C.schedule_run(on_timer, target, ...)  # time_point=datetime对象

# ✅ 传字符串
C.schedule_run(on_timer, target.strftime('%Y%m%d%H%M%S'), ...)
```

**回测中 schedule_run 不触发**，只能实盘验证。

### ⚠️ 定时器配置放外层（2026-08-27 确认）

**Timer interval/start time 放入口文件（外层），不放策略文件（内层）。**

原因：
- 定时是触发机制，不是策略逻辑
- 同一策略不同部署可能要不同频率
- 入口文件已经是配置层（MODE、DEBUG 在那里）

**一天多次触发的处理**：策略层自己判断时间（方案B），不要注册多个同名 run_time。

```python
# 入口文件：注册一个每分钟触发的定时器
if MODE == 'live':
    C.run_time('on_timer', '1nMinute', today + ' 09:30:00')

# 策略文件：自己判断当前时间是否在信号时间
def on_signal(C):
    now = datetime.now().strftime('%H%M')
    if now not in ('0940', '1140', '1440'):
        return  # not signal time, skip
    # actual business logic...
```

**schedule_run同名不覆盖，归入同一组**（官方文档确认），可用 `cancel_schedule_run('name')` 批量取消。

**一天多次触发推荐方案B**：注册一个高频定时器（如 `1nMinute`），策略里自己过滤时间。这样不用纠结同名注册问题：
```python
# 入口文件：注册一个每分钟触发的定时器
if MODE == 'LIVE':
    from datetime import datetime, timedelta
    now = datetime.now()
    target = datetime.strptime(now.strftime('%Y%m%d') + '093000', '%Y%m%d%H%M%S')
    # No need for +1 day — schedule_run fires immediately if target is in the past
    interval = timedelta(seconds=86400)
                    interval=timedelta(minutes=1), name='signal_timer')

# 策略文件：自己判断当前时间是否在信号时间
def on_signal(C):
    now = datetime.now().strftime('%H%M')
    if now not in ('0940', '1140', '1440'):
        return  # not signal time, skip
    # actual business logic...
```

### schedule_run 典型用法

```python
# 每天9:25集合竞价下单
def init(C):
    from datetime import datetime, timedelta
    now = datetime.now()
    target = datetime.strptime(now.strftime('%Y%m%d') + '092500', '%Y%m%d%H%M%S')
    # No need for +1 day — schedule_run fires immediately if target is in the past
    interval = timedelta(seconds=86400)
                    interval=timedelta(days=1), name='auction')

def auction_buy(C):
    # 集合竞价逻辑
    pass

# 每5秒巡检
def init(C):
    from datetime import datetime, timedelta
    now = datetime.now()
    target = datetime.strptime(now.strftime('%Y%m%d') + '093000', '%Y%m%d%H%M%S')
    C.schedule_run(patrol, target, repeat_times=-1,
                    interval=timedelta(seconds=5), name='patrol')

def patrol(C):
    # 定时巡检逻辑
    pass

# 取消定时器
def stop_strategy(C):
    C.cancel_schedule_run('auction')  # 按组名取消
    C.cancel_schedule_run(1)          # 按任务号取消
```

