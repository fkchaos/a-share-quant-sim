# 迅投QMT量化交易系统-国债逆回购-闲钱理财

> 来源: https://invest.wtsolutions.cn/posts/qmt-debt-reverse-repurchase/

## 国债逆回购

资金账户里面的闲钱，有时候我们希望可以购买国债逆回购。在QMT里面，我们可以通过代码定时实现国债逆回购。

## 国债逆回购的代码

代码仅供参考，不构成投资建议，请谨慎操作。

```python
account = 'xxxxx'

def DRR(ContextInfo):
    afund = get_trade_detail_data(account,'stock','account')[0].m_dAvailable
    avolume = int((afund )/1000)*10
    if avolume >= 10:
        passorder(24,1101,account,'204001.SH',14,-1,avolume,'DRR',2,'DRR',ContextInfo)
        print('DRR ' + str(avolume) )
    else:
        print('DRR skipped')
```

如上定义了个DRR函数，通过定时函数，在盘中的14:57分运行。

**2025.05.26更新：**

这段代码中，使用的是对手价下单的，如果想要使用限价等其他形式，需要相应修改。  
当采用对手价下单的时候，如果金额不多，通常在几万元的话，是可以立即成交的，如果金额多的话，考虑换其他下单类型。
