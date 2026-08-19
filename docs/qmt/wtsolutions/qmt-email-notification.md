# QMT 与手机通信 - 邮件推送实现方法

> 来源: https://invest.wtsolutions.cn/posts/qmt-email-notification/

## QMT 邮件推送的重要性

在使用 QMT 量化交易系统时，我们经常需要将交易信号、错误信息或系统状态及时通知到手机，以便及时处理异常情况。邮件推送是一种简单、可靠且免费的通信方式，通过 Python 的 SMTP 模块可以轻松实现。

## 实现邮件推送的准备工作（发件邮箱）

### 步骤 1：选择合适的邮箱服务

推荐使用以下邮箱服务，它们都支持 SMTP 协议。

### 步骤 2：开启 SMTP 服务并获取授权码

以 QQ 邮箱为例。

## 实现邮件推送的准备工作（收件邮箱）

选择一个收件邮箱还是很重要的，最好是当你邮箱有邮件收到的时候，手机上有提醒，比如你可以在手机上安装对应的app获取实时推送。

我这边用的是QQ邮件，然后微信绑定了QQ邮箱，当我的QQ邮箱收到了一个邮件的时候，微信就会收到一个提醒，说进来一封新邮件。类似微信消息，这个几乎是实时的。

## Python 实现 SMTP 邮件发送

### 基本邮件发送代码

```python
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

# 邮件配置
SMTP_SERVER = "smtp.qq.com"  # SMTP 服务器地址
SMTP_PORT = 587  # SMTP 端口
SENDER_EMAIL = "[email protected]"  # 发件人邮箱
SENDER_PASSWORD = "your_authorization_code"  # 授权码（不是密码）
RECEIVER_EMAIL = "[email protected]"  # 收件人邮箱

def send_email(subject, content):
    """
    发送邮件函数
    :param subject: 邮件主题
    :param content: 邮件内容
    :return: 是否发送成功
    """
    try:
        message = MIMEMultipart()
        message['From'] = Header("QMT 通知", 'utf-8')
        message['To'] = Header("用户", 'utf-8')
        message['Subject'] = Header(subject, 'utf-8')
        
        message.attach(MIMEText(content, 'plain', 'utf-8'))
        
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
        server.starttls()  # 开启 TLS 加密
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, message.as_string())
        server.quit()
        
        print("邮件发送成功")
        return True
    except Exception as e:
        print(f"邮件发送失败: {str(e)}")
        return False

# 测试邮件发送
if __name__ == "__main__":
    send_email("QMT 测试通知", "这是一封测试邮件，用于验证 QMT 邮件推送功能是否正常。")
```

### 邮件发送函数封装

为了方便在 QMT 策略中使用，可以将邮件发送功能封装成一个独立的模块：

```python
# email_utils.py
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

class EmailSender:
    def __init__(self, smtp_server, smtp_port, sender_email, sender_password, receiver_email):
        self.smtp_server = smtp_server
        self.smtp_port = smtp_port
        self.sender_email = sender_email
        self.sender_password = sender_password
        self.receiver_email = receiver_email

    def send(self, subject, content):
        """发送邮件"""
        try:
            message = MIMEMultipart()
            message['From'] = Header("QMT 通知", 'utf-8')
            message['To'] = Header("用户", 'utf-8')
            message['Subject'] = Header(subject, 'utf-8')
            
            message.attach(MIMEText(content, 'plain', 'utf-8'))
            
            server = smtplib.SMTP(self.smtp_server, self.smtp_port)
            server.starttls()
            server.login(self.sender_email, self.sender_password)
            server.sendmail(self.sender_email, self.receiver_email, message.as_string())
            server.quit()
            
            return True
        except Exception as e:
            print(f"邮件发送失败: {str(e)}")
            return False

# 创建全局邮件发送实例
email_sender = EmailSender(
    smtp_server="smtp.qq.com",
    smtp_port=587,
    sender_email="[email protected]",
    sender_password="your_authorization_code",
    receiver_email="[email protected]"
)

def send_qmt_email(subject, content):
    """发送 QMT 相关邮件"""
    return email_sender.send(subject, content)
```

封装后的email.utils.py应当与其他的策略的py文件放在同一个文件夹下，通常是在安装目录下的python文件夹。

## 在 QMT 策略中集成邮件推送

### 示例 1：策略启动通知

```python
from email_utils import send_qmt_email

def init(ContextInfo):
    ContextInfo.set_account("your_account")
    send_qmt_email("QMT 策略启动通知", "策略已成功启动，开始运行。")

def handle_bar(ContextInfo):
    pass
```

### 示例 2：交易信号通知

```python
from email_utils import send_qmt_email

def handle_bar(ContextInfo):
    code = "600000.SH"
    price = ContextInfo.get_market_data([code], "close")[code]
    
    if price > 10.0:
        send_qmt_email("QMT 交易信号通知", f"买入信号：{code} 当前价格：{price}\n建议：买入")
```

### 示例 3：错误信息通知

```python
from email_utils import send_qmt_email

def handle_bar(ContextInfo):
    try:
        code = "600000.SH"
        price = ContextInfo.get_market_data([code], "close")[code]
    except Exception as e:
        send_qmt_email("QMT 策略错误通知", f"策略运行出错：{str(e)}\n请及时检查。")
```

## 故障排除

### 常见问题及解决方案

**SMTP 服务器连接失败** - 检查服务器地址和端口是否正确。

**认证失败** - 确认使用的是授权码而非邮箱密码。

**邮件被标记为垃圾邮件** - 检查邮件内容和发件人设置。

## 总结

通过 Python 的 SMTP 模块，我们可以轻松实现 QMT 与手机的邮件通信，及时获取交易信号和系统状态。

如果对即时性要求更高，可以把告警直接推送到微信，参考策略消息推送到微信的几种方案。邮件走留档，微信走实时，两者互补效果更好。
