# 迅投QMT量化交易系统-服务器连接 中断与再连接

> 来源: https://invest.wtsolutions.cn/posts/qmt-server-connection/

## 行情和交易服务器的中断和再连接

在QMT程序化交易系统的右下角，能看到两个标签【行情】【交易】，如果是绿色的，则代表行情和交易服务器是连接正常的，如果变成了红色，则代表那个服务器连接中断了。  
如果是红色的，则需要用鼠标点击红色部分，会弹出一个服务器的选择窗口，你需要手动的进行相应的切换，直到右下角的标签变成了绿色的。

## 服务器再连接

需要注意的是，有的券商的再连接发生时，当前的所有的交易数据，券商会完全再给你推送一次，所有的成交回报，所有的下单回报等等，大家一定要特别注意，不能相信券商给你推送的交易回报, 它可能是重复发送的（如deal_callback等等）。

## 服务器中断的识别

很多时候，我们的QMT程序化交易系统是24小时运行的，那么我们其实希望能够识别到当前【行情】【交易】服务器连接是否正常。我自己所采用的方法比较简单，就是设置一个定时函数，在盘前的一个时间去通过获取行情来判断是否存在服务器中断。如果判断中断了，则给我发送一个消息。当然如果我在设定的时间完全没有收到任何消息的话，则说明整个QMT程序存在不正常运行的状况，需要人工干预。

我使用的方法非常简单，就是在9点15运行如下代码：

```python
def DailySettings(ContextInfo):
    if getLastClose('510300.SH',ContextInfo) != False:
        sendMsg("morning")

def getLastClose(stock, ContextInfo):
    stockList = [stock]
    tick = ContextInfo.get_full_tick(stock_code = stockList)
    if stock in tick:
        if "lastClose" in tick[stock]:
            return tick[stock]["lastClose"]
    sendMsg("行情数据可能存在问题")
    return False
```

如果我收到了morning，则通常QMT行情服务器连接正常。  
sendMsg函数，大家可以自定义，比如发送短信，发送邮件，发送飞书消息，自己能接收到就行。
