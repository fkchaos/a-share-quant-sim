# 讯投 QMT 使用小技巧 -如何判断今天是不是交易日

> 来源: https://invest.wtsolutions.cn/posts/qmt-is-today-a-trading-day/

在讯投QMT中判断今天是否为交易日。

在讯投QMT平台中，你可以通过以下几种方法来判断当天是否为交易日：

## 方法一：使用ContextInfo.get_trading_dates()函数

```python
def is_trading_day():
    # 获取最近一段时间的交易日历
    trading_dates = ContextInfo.get_trading_dates('SH', 0, 10)  # 获取从今天开始的10个交易日
    
    # 获取当前日期
    today = datetime.datetime.now().strftime('%Y%m%d')
    
    # 判断今天是否在交易日列表中
    return today in trading_dates
```

## 方法二：使用xtdata模块（如果可用）

```python
import xtdata

def is_trading_day():
    # 获取今天的日期
    today = datetime.datetime.now().strftime('%Y%m%d')
    
    # 查询上证指数的交易日历
    trading_dates = xtdata.get_trading_dates('SH', today, today)
    
    return len(trading_dates) > 0
```

## 方法三：使用系统时间函数

```python
def is_trading_day():
    # 获取当前时间
    now = datetime.datetime.now()
    
    # 判断是否为工作日(周一到周五)
    if now.weekday() >= 5:  # 5=周六,6=周日
        return False
    
    # 进一步检查是否是节假日(需要维护节假日表)
    # 这里可以添加你的节假日判断逻辑
    
    return True
```

## 注意事项

你可以根据你的QMT版本和可用模块选择最适合的方法。第一种方法通常是首选，因为它直接查询交易所的交易日历。
