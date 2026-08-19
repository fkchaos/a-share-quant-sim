# 使用DeepSeek R1大模型编写迅投 QMT 的量化交易 Python 代码

> 来源: https://invest.wtsolutions.cn/posts/write-qmt-code-with-deepseek/

随着人工智能技术的迅猛发展，利用AI工具提升工作效率已成为现代开发者的重要手段。

在使用deepseek官方网页生成迅投QMT代码的时候，deepseek给出的代码是xtquant代码，也就是miniqmt代码，并不是我们传统意义上说的大QMT可用的代码。

因此，我们需要自建一个知识库，让deepseek根据我的知识库里面的知识，去帮我生成大QMT可用的交易代码。

## 一、建立迅投QMT的知识库

建立迅投QMT的知识库的方法比较简单，我们就直接访问迅投QMT的官方网站，把网站上的网页内容打印成PDF文件。

下载链接：[网盘链接](https://pan.quark.cn/s/a68a9e9ce7d7)

## 二、利用腾讯ima.copilot建立与deepseek的知识库

### 1、下载与安装 IMA.Copilot

### 2. 导入PDF文件到个人知识库

## 三、使用 IMA.Copilot 编写量化交易 Python 代码

在个人知识库页面，下面有一个输入窗口，可以在此处提问，此处提问可以是直接基于知识库知识来回答的。

举例说明："请根据迅投QMT的PDF文档，编写一个计算股票移动平均线的Python函数。"

IMA.Copilot 将结合 DeepSeek R1 大模型和你的知识库内容，自动生成相应的Python代码：

```python
#coding:gbk
import numpy as np

def calculate_ma(ContextInfo, stock_code, window_size):
    """
    计算指定股票的移动平均线
    :param ContextInfo: QMT上下文对象
    :param stock_code: 股票代码（格式：'000001.SZ'）
    :param window_size: 均线周期
    :return: MA值（浮点数）
    """
    # 获取本地收盘价数据
    local_data = ContextInfo.get_market_data_ex(
        ['close'], 
        [stock_code],
        period='1d',
        count=window_size,
        subscribe=False
    )
    
    # 提取收盘价列表
    try:
        close_prices = local_data[stock_code]['close'].values
    except KeyError:
        print(f"未找到股票{stock_code}的收盘数据")
        return None
    
    # 计算移动平均
    if len(close_prices) >= window_size:
        ma_value = np.mean(close_prices[-window_size:])
        return round(ma_value, 2)
    else:
        print(f"数据不足，当前数据量{len(close_prices)}，需要{window_size}根K线")
        return None

# 使用示例（在策略中调用）
def handlebar(C):
    stock = "600000.SH"
    ma10 = calculate_ma(C, stock, 10)  # 计算10日均线
    ma20 = calculate_ma(C, stock, 20)  # 计算20日均线
    print(f"{stock} 10日均线：{ma10}，20日均线：{ma20}")

def init(C):
    # 下载所需历史数据（建议在策略初始化时执行）
    C.download_history_data("600000.SH", "1d", "", "")
```

## 四、检查测试

生成的代码可能需要根据具体需求进行调整和优化。

**特别注意：AI生成的内容，不能保证是100%准确的，大家务必人工检查确认代码的正确性。**

通过本文的介绍，如何使用腾讯 IMA.Copilot 结合 DeepSeek R1 大模型，从个人知识库中的迅投QMT PDF文件编写量化交易的Python代码。这一流程不仅提高了代码编写的效率，还大大提升了代码的质量和准确性。
