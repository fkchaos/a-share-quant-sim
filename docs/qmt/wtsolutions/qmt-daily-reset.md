# 迅投QMT交易终端小技巧-每天代码重置与定时任务设置

> 来源: https://invest.wtsolutions.cn/posts/qmt-daily-reset/

## 为什么需要每天代码重置

在QMT量化交易中，很多策略需要在每个交易日开始时进行初始化操作。这可能包括：

如果不进行每日重置，可能会导致数据累积错误、状态混乱等问题，影响策略的正常运行。

## QMT的自动初始化机制

QMT提供了系统级的自动初始化功能，可以在系统设置中配置每天固定时间自动重启终端。但这种方式会导致整个终端重启，可能会影响正在运行的策略。

更好的做法是在策略代码中实现每日重置逻辑，这样可以更精细地控制初始化过程。

## 实现每天执行一次的函数

### 使用定时任务（推荐）

QMT支持使用`run_time`来设置定时任务，可以在指定时间执行函数。

run_time函数的具体使用参数和解释说明，可以参考[官方文档（pdf 版本）](../qmt-knowledge-base)

```python
from datetime import datetime, date

def init(ContextInfo):
    # 设置每天9:25执行重置
    ContextInfo.run_time('daily_reset','1nDay', str(date.today())+ ' 09:25:00')
    # 立即执行一次初始化
    daily_reset(ContextInfo)

def daily_reset(ContextInfo):
    ContextInfo.log("执行每日重置...")
    
    # 重置每日统计变量
    ContextInfo.user_data['daily_trades'] = 0
    ContextInfo.user_data['daily_volume'] = 0
    ContextInfo.user_data['daily_profit'] = 0
    
    # 重置交易状态
    ContextInfo.user_data['has_opened_position'] = False
    ContextInfo.user_data['today_target_position'] = {}
    
    # 重新计算当日参数
    calculate_daily_parameters(ContextInfo)
    
    ContextInfo.log("每日重置完成")

def calculate_daily_parameters(ContextInfo):
    # 根据当日行情计算参数
    # 例如：根据开盘价调整目标仓位
    ContextInfo.user_data['today_target_position'] = {
        '000001.SZ': 0.1,
        '600000.SH': 0.15
    }

def handlebar(ContextInfo):
    # 策略主逻辑
    # ...
```

## 注意事项

### 1. 避免重复重置

使用日期判断或定时任务时，要确保每天只执行一次重置。

### 2. 处理节假日

如果使用固定时间的定时任务，需要考虑节假日的情况：
（如何判断当天是不是交易日，可以参考：[讯投 QMT 使用小技巧 -如何判断今天是不是交易日](../qmt-is-today-a-trading-day)）

```python
def daily_reset(ContextInfo):
    # 检查今天是否是交易日
    if not is_trading_day():
        ContextInfo.log("今日非交易日，跳过重置")
        return
    
    # 正常重置逻辑
    # ...

def is_trading_day():
    """判断今天是否是交易日"""
    today = datetime.date.today()
    
    # 周末不是交易日
    if today.weekday() >= 5:
        return False
    
    # 可以添加节假日判断
    holidays = [
        datetime.date(2024, 1, 1),  # 元旦
        datetime.date(2024, 2, 10), # 春节
        # ...
    ]
    
    return today not in holidays
```

### 3. 日志记录

在重置函数中添加日志记录，方便排查问题：

```python
def daily_reset(ContextInfo):
    ContextInfo.log(f"每日重置开始 - {datetime.datetime.now()}")
    
    try:
        # 重置逻辑
        ContextInfo.user_data['daily_trades'] = 0
        ContextInfo.log("每日重置完成")
    except Exception as e:
        ContextInfo.log(f"每日重置失败: {str(e)}")
        # 发送错误通知
        send_notification(f"每日重置失败: {str(e)}")
```

### 4. 发送通知到手机

每天重置完成了之后，可以发送邮件或其他提醒到手机上，具体参考：[QMT 与手机通信 - 邮件推送实现方法](../qmt-email-notification)，或用即时性更好的 [策略消息推送到微信的几种方案](../qmt-wechat-notification)。

## 总结

通过设置每日重置函数，可以确保策略在每个交易日开始时都处于一个干净、已知的状态。这对于维护策略的稳定性和准确性非常重要。

推荐使用定时运行的函数方式设置定时任务，这样可以精确控制执行时间，避免在交易过程中频繁检查日期。
