# 讯投QMT使用小技巧: account未定义 account not defined

> 来源: https://invest.wtsolutions.cn/posts/qmt-account-not-defined/

最近有朋友问我，为什么在使用QMT自带的一些策略进行回测或实盘时，会提示错误，比如account未定义，account未找到或者account not defined。

通常提出这些问题的朋友都是QMT刚刚入门的，对代码还并不熟悉。

以迅投QMT自带策略-双均线实盘示例PY策略为例，代码部分（节选）中：

```python
#encoding:gbk
import pandas as pd
import numpy as np
import datetime

"""
示例说明：双均线实盘策略，通过计算快慢双均线，在金叉时买入，死叉时做卖出
"""

class a():
    pass
A = a()  # 创建空的类的实例 用来保存委托状态

def init(C):
    A.stock = C.stockcode + '.' + C.market  # 品种为模型交易界面选择品种
    A.acct = account  # 账号为模型交易界面选择账号
```

其中：

```python
A.acct = account
```

出错就是在这一行。

account对于系统来说是没有定义的，不知道account具体是多少，所以系统会报错。

此处的account应该是你登录QMT软件的账号，你可以手动的把 account修改成你的实际账户，有的券商是纯数字，有的可能是带字母的，以券商提供给你的账户账号为准。

比如你的账户是888666333，那么你需要把account修改成带英文双引号的"888666333"，这样就可以了，修改后保存开启回测了。
