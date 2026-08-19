# 讯投QMT使用小技巧: QMT推送发送消息到微信的几种方案

> 来源: https://invest.wtsolutions.cn/posts/qmt-wechat-notification/

## 概述

策略上线后，最朴素的诉求就是"出事的时候手机能响一下"。邮件方案在 [邮件推送](../qmt-email-notification) 里讲过了，但邮件有个问题：到达手机的即时性依赖邮箱 App 的推送策略，有时会延迟几分钟甚至几十分钟。而微信几乎是国人打开频率最高的 App，把告警直接送到微信，体验会好很多。

本文整理 QMT 策略中可用的几条微信推送通道——Server酱、企业微信群机器人、WxPusher、PushPlus，给出统一封装的工具类和 QMT 集成示例，并和 [运行状态监控与心跳报警](../qmt-monitoring-alert) 配合，构成完整的告警链路。

## 一、方案选型

| 方案 | 触达位置 | 注册门槛 | 频率限制 | 适合场景 |
| --- | --- | --- | --- | --- |
| Server酱 | 个人微信「Server酱Turbo」应用 | 扫码登录拿 SendKey | 免费版 5 条/天，Turbo 200 条/天 | 个人单策略、低频告警 |
| 企业微信群机器人 | 企业微信群 | 建群+添加机器人 | 20 条/分钟 | 多策略集中告警、团队协作 |
| WxPusher | 个人微信「WxPusher」公众号 | 注册+关注公众号 | 免费版较宽松 | 一对多推送、带 UI 管理 |
| PushPlus | 个人微信「PushPlus推送加」公众号 | 注册+关注公众号 | 免费版 200 条/天 | 个人推送、支持模板 |

> 选型建议：**个人单策略选 Server酱或 PushPlus，多策略/团队选企业微信群机器人**。企业微信群机器人不需要个人开通企业微信会员，建个只有自己的群也能用。

## 二、Server酱：最简单的方案

Server酱（sct.ftqq.com）走的是「一个 HTTP 请求 = 一条微信消息」的模式，整个接入只需要一个 SendKey。

### 步骤

1. 访问 [sct.ftqq.com](https://sct.ftqq.com/)，微信扫码登录
2. 在「SendKey」页面拿到 `SCT****` 开头的 key
3. 在「微信推送」页面扫码绑定要接收消息的微信号
4. 调用接口

### 代码

```python
# wechat_utils.py
# -*- coding: utf-8 -*-
import requests

SERVERCHAN_KEY = "SCT****your_send_key****"

def send_serverchan(title, content=""):
    """
    通过 Server酱 推送到微信
    :param title: 消息标题（必填，最长 32）
    :param content: 消息内容，支持 Markdown
    :return: 是否发送成功
    """
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_KEY}.send"
    try:
        r = requests.post(url, data={"title": title, "desp": content}, timeout=10)
        return r.json().get("code") == 0
    except Exception as e:
        print(f"Server酱推送失败: {e}")
        return False
```

> Server酱免费版每天 5 条，对监控告警来说偏少。要么升级 Turbo 版，要么配合下面的节流策略只用它发关键告警。

## 三、企业微信群机器人：多策略群告警

企业微信群机器人不需要企业认证，建一个群、加个机器人就能拿到 Webhook URL，最适合把多个策略的告警集中到一个群里。

### 步骤

1. 在企业微信里建一个群（自己一个人的群也行）
2. 群设置 → 群机器人 → 添加机器人 → 起个名字
3. 复制 Webhook 地址，形如 `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx`

### 文本消息

```python
import requests

WECOM_WEBHOOK = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=your_key"

def send_wecom_text(content, mentioned_list=None):
    """
    发送文本消息
    :param content: 文本内容
    :param mentioned_list: 需要@的用户ID列表，['@all'] 表示@所有人
    """
    data = {
        "msgtype": "text",
        "text": {
            "content": content,
            "mentioned_list": mentioned_list or [],
            "mentioned_mobile_list": []
        }
    }
    try:
        r = requests.post(WECOM_WEBHOOK, json=data, timeout=10)
        return r.json().get("errcode") == 0
    except Exception as e:
        print(f"企业微信推送失败: {e}")
        return False
```

### Markdown 消息

告警内容用 Markdown 排版会清晰很多：

```python
def send_wecom_markdown(content):
    """
    发送 Markdown 消息（企业微信群机器人支持有限 Markdown 语法）
    """
    data = {
        "msgtype": "markdown",
        "markdown": {"content": content}
    }
    try:
        r = requests.post(WECOM_WEBHOOK, json=data, timeout=10)
        return r.json().get("errcode") == 0
    except Exception as e:
        print(f"企业微信推送失败: {e}")
        return False

# 使用示例
msg = """
## QMT 告警

> **策略**: 双均线
> **账号**: 600000
> **时间**: 2026-08-04 14:30
> **事件**: 账号掉线

请及时检查
"""
send_wecom_markdown(msg)
```

> 企业微信群机器人支持 `<font color="warning">文字</font>` 给文字上色，warning 是橙红色，比较醒目。

## 四、WxPusher / PushPlus

两者都是「关注公众号 → 注册拿 token → HTTP 调接口」的模式，用法和 Server酱 类似，只是接口参数不同。

### WxPusher

```python
WXPUSHER_TOKEN = "your_app_token"

def send_wxpusher(title, content):
    url = "https://wxpusher.????.com/api/send/message"
    data = {
        "appToken": WXPUSHER_TOKEN,
        "content": content,
        "summary": title,  # 消息摘要（必填，否则推送列表里显示空白）
        "contentType": 1,  # 1=文本, 2=html, 3=markdown
        "topicIds": [],    # 主题ID，可留空
        "uids": ["your_uid"]  # 目标用户UID，在公众号「我的」里查
    }
    try:
        r = requests.post(url, json=data, timeout=10)
        return r.json().get("success")
    except Exception as e:
        print(f"WxPusher 推送失败: {e}")
        return False
```

### PushPlus

```python
PUSHPLUS_TOKEN = "your_token"

def send_pushplus(title, content, template="html"):
    url = "http://www.pushplus.plus/send"
    data = {
        "token": PUSHPLUS_TOKEN,
        "title": title,
        "content": content,
        "template": template  # html / json / markdown
    }
    try:
        r = requests.post(url, json=data, timeout=10)
        return r.json().get("code") == 200
    except Exception as e:
        print(f"PushPlus 推送失败: {e}")
        return False
```

## 五、总结

把消息推到微信，QMT 策略就有了"会叫的watchdog"：

* 个人单策略用 **Server酱**，多策略/团队用 **企业微信群机器人**，需要一对多用 **WxPusher/PushPlus**。
* 统一封装到 `notify.py`，支持渠道切换、节流、容错。
* 集成到 `init`（启动通知）、`deal_callback`（成交回报）、`patrol`（巡检告警）、`daily_report`（收盘日报）四个时机。
* 关键告警走多通道，实时走微信、留档走邮件。

配合运行状态监控与心跳报警的心跳/巡检/watchdog体系，整套 QMT 运维闭环就齐了——出事手机能响，没事每天一报，安心睡觉。
