# 讯投 QMT 使用小技巧：判断当前时段是交易/非交易/竞价/盘后等时间段

> 来源: https://invest.wtsolutions.cn/posts/qmt-is-now-a-trading-hour/

## A股的交易时间段

A股的交易时段分为**开盘集合竞价、连续竞价（早盘、午盘）和收盘集合竞价**三个阶段。

### 交易时段明细

| 时段 | 时间 | 备注 |
| --- | --- | --- |
| **开盘集合竞价** | 9:15 - 9:25 | 9:15-9:20：可申报或撤单。9:20-9:25：可申报，不可撤单。 |
| **早盘连续竞价** | 9:30 - 11:30 | 正常交易时段，可自由买卖。 |
| **午间休市** | 11:30 - 13:00 | 不接受委托，未成交的订单仍有效。 |
| **午盘连续竞价** | 13:00 - 14:57 | 正常交易时段。 |
| **收盘集合竞价** | 14:57 - 15:00 | 可申报，不可撤单，以最大成交量撮合收盘价。 |

## QMT中的函数判断

以下是一个 Python 函数，用于判断当前时间属于 A 股的哪个交易时段（包括科创板和创业板的盘后交易），如果不是交易时段则返回 **"非交易时段"**：

```python
import datetime

def get_a_share_trading_period():
    now = datetime.datetime.now()
    current_time = now.time()
    weekday = now.weekday()  # 0-4 是周一到周五

    # 检查是否为交易日（周一至周五，非节假日）
    if weekday >= 5:  # 周六、周日
        return "非交易时段（周末休市）"

    # 定义交易时段
    trading_periods = [
        {"name": "开盘集合竞价", "start": "09:15:00", "end": "09:25:00", "can_cancel": True},
        {"name": "早盘连续竞价", "start": "09:30:00", "end": "11:30:00", "can_cancel": True},
        {"name": "午间休市", "start": "11:30:00", "end": "13:00:00", "can_cancel": False},
        {"name": "午盘连续竞价", "start": "13:00:00", "end": "14:57:00", "can_cancel": True},
        {"name": "收盘集合竞价", "start": "14:57:00", "end": "15:00:00", "can_cancel": False},
    ]

    # 科创板和创业板的盘后交易（15:05-15:30）
    kechuang_cyb_period = {"name": "科创/创业板盘后交易", "start": "15:05:00", "end": "15:30:00", "can_cancel": False}

    # 检查当前是否在交易时段
    for period in trading_periods:
        start_time = datetime.datetime.strptime(period["start"], "%H:%M:%S").time()
        end_time = datetime.datetime.strptime(period["end"], "%H:%M:%S").time()
        if start_time <= current_time <= end_time:
            return f"{period['name']}（可撤单: {'是' if period['can_cancel'] else '否'}）"

    # 检查是否在科创/创业板盘后交易时段
    kechuang_start = datetime.datetime.strptime(kechuang_cyb_period["start"], "%H:%M:%S").time()
    kechuang_end = datetime.datetime.strptime(kechuang_cyb_period["end"], "%H:%M:%S").time()
    if kechuang_start <= current_time <= kechuang_end:
        return f"{kechuang_cyb_period['name']}（按收盘价交易，不可撤单）"

    # 不在任何交易时段
    return "非交易时段"

# 测试
print(get_a_share_trading_period())
```

### 输出示例

`开盘集合竞价（可撤单: 是）`
`午间休市（可撤单: 否）`
`科创/创业板盘后交易（按收盘价交易，不可撤单）`
`非交易时段`

### 功能说明

如果需要进一步扩展（如节假日判断），当天是不是交易日，请参考：如何判断今天是不是交易日。
