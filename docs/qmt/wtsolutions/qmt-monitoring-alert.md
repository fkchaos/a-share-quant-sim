# 讯投QMT使用小技巧: 运行状态监控与心跳报警

> 来源: https://invest.wtsolutions.cn/posts/qmt-monitoring-alert/

## 概述

策略上线后最怕两件事：一是 QMT 挂了你不知道，二是策略还在"运行"但其实已经不工作（断开连接、行情不更新、下单报错被吞）。等你晚上看盘发现时，可能已经错过一整天。

本文给出一个轻量的「心跳 + 巡检 + 报警」方案，让 QMT 出问题第一时间推送到手机。和邮件推送、崩溃自动恢复配合，组成完整的运维闭环。

## 一、监控什么

| 监控项 | 含义 | 异常表现 |
| --- | --- | --- |
| 心跳 | 策略是否还在跑 | 心跳时间戳长期不更新 |
| 行情新鲜度 | 最新价是否还在变 | 最新bar时间停在很久以前 |
| 账号登录态 | 交易通道是否在线 | get_trade_detail_data 报错或返回空 |
| 持仓异常 | 持仓是否合理 | 持仓为0但应有持仓、负持仓等 |
| 当日委托/成交 | 今天有没有异常废单 | pending 长时间不确认 |

## 二、心跳：最基础的存活检测

思路：策略定时把"当前时间"写到文件/数据库，外部（或另一个巡检任务）检查这个时间戳是否在更新。超过阈值就报警。

### 策略端：定时写心跳

```python
# -*- coding: utf-8 -*-
#encoding:gbk
import datetime
from datetime import date

class a(): pass
A = a()
A.heartbeat = ''   # 心跳时间戳，仅存内存

def init(C):
    A.acct = account
    A.acct_type = accountType
    # 每30秒写一次心跳
    C.run_time('heartbeat', '30nSecond', str(date.today())+' 09:00:00')
    # 每2分钟巡检一次
    C.run_time('patrol', '60nSecond', str(date.today())+' 09:00:00')

def heartbeat(C):
    """定时写心跳时间戳"""
    A.heartbeat = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # 保存到文件/数据库
    save(A.heartbeat)
```

## 三、巡检：在策略内部自检

光有心跳不够——策略可能还在跑，但行情断了或账号掉线了。需要在策略内定时做几项检查，发现异常就报警：

```python
def patrol(C):
    """定时巡检，异常即告警"""
    problems = []

    # 1. 行情新鲜度：最新bar时间是否过旧
    try:
        snap = C.get_market_data_ex(['last'], [A.stock], period='1d', count=1)
        last_bar_time = snap[A.stock].index[-1]
    except Exception as e:
        problems.append(f"取行情失败: {e}")

    # 2. 账号登录态：查账户能查到说明在线
    try:
        acc = get_trade_detail_data(A.acct, A.acct_type, 'account')
        if len(acc) == 0:
            problems.append(f"账号 {A.acct} 未登录或掉线")
    except Exception as e:
        problems.append(f"查账号异常: {e}")

    # 3. pending 订单超时
    st = load({})
    pending = st.get('pending', {})
    if len(pending) > 5:
        problems.append(f"未确认委托过多: {len(pending)}")

    if problems:
        msg = "\n".join(problems)
        print(f"[巡检异常] {msg}")
        notify("QMT巡检异常", msg)
```

## 四、报警通道

巡检发现异常，要能推到手机。最简单的是复用邮件推送：

```python
from email_utils import send_qmt_email

def notify(subject, content):
    try:
        send_qmt_email("[QMT]" + subject, content)
    except Exception as e:
        print(f"告警发送失败: {e}")

# 告警本身失败不能影响策略
```

关键告警（账号掉线、重复下单风险）建议用即时性更好的渠道，邮件可作为兜底。

## 五、外部存活检测：防止策略整体卡死

策略自己的心跳和巡检，在"策略进程还在"时才有效。如果整个策略卡死（死锁、QMT 假死），它就没法再写心跳了。所以需要一个**独立于策略**的外部检测：

用一个独立的定时任务（系统 crontab 或另一个极简策略）读取心跳文件，检查时间戳：

```python
# watchdog.py —— 可由系统计划任务每分钟跑一次
import datetime, os, json
from email_utils import send_qmt_email

STATE = './strategy_state.json'
THRESHOLD = 120  # 秒，超过2分钟没心跳就告警

def check():
    if not os.path.exists(STATE):
        send_qmt_email("[QMT]心跳丢失", "找不到状态文件，策略可能未启动")
        return
    with open(STATE, 'r', encoding='utf-8') as f:
        st = json.load(f)
    hb = st.get('heartbeat', '')
    if not hb:
        send_qmt_email("[QMT]心跳丢失", "无心跳记录")
        return
    last = datetime.datetime.strptime(hb, '%Y-%m-%d %H:%M:%S')
    age = (datetime.datetime.now() - last).total_seconds()
    if age > THRESHOLD:
        send_qmt_email("[QMT]心跳超时", f"上次心跳 {hb}，已超 {int(age)} 秒")

if __name__ == '__main__':
    check()
```

用 crontab 每分钟跑一次：

```
* * * * * cd /path/to/qmt/python && python watchdog.py
```

这样哪怕 QMT 整体假死，外部看门狗也能发现心跳停了，第一时间告警。

## 六、一份巡检报告：每天收盘推送

除了异常告警，每天收盘后推送一份「日报」也很有用：今日成交、当前持仓、账号市值、心跳状态。让你不用开电脑也能知道策略今天干了啥：

```python
def init(C):
    # 每天15:05推送日报
    C.run_time('daily_report', '1nDay', '20260101 15:05:00')

def daily_report(C):
    acc = get_trade_detail_data(A.acct, A.acct_type, 'account')[0]
    holdings = get_trade_detail_data(A.acct, A.acct_type, 'position')
    lines = [f"账号 {A.acct} 收盘报告",
             f"可用资金: {acc.m_dAvailable}",
             f"持仓数: {len(holdings)}"]
    for h in holdings:
        code = h.m_strInstrumentID + '.' + h.m_strExchangeID
        lines.append(f"  {code} {h.m_nVolume}股 浮盈{h.m_dProfit}")
    notify("收盘报告", "\n".join(lines))
```

## 七、注意事项

* `run_time` 定时任务和 `handlebar` 的触发机制不同，需要理解各自的行为。

## 总结

一个可靠的监控体系 = **策略内心跳 + 内部巡检 + 外部看门狗 + 报警通道 + 日报兜底**。

把这套和崩溃自动恢复一起部署，你的 QMT 实盘才真正算"无人值守也能睡个安稳觉"。
