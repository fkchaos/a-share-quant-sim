# qmt-knowledge-skill 把 QMT 知识库装进 AI Agent 让大模型直接写大 QMT 代码

> 来源: https://invest.wtsolutions.cn/posts/qmt-knowledge-skill/

## ⚠️ 免责声明

**量化交易存在巨大风险**。本项目仅提供 QMT API 文档的知识结构化整理与 AI Agent 辅助能力：

QMT、迅投 为迅投公司或其关联公司的商标。本项目仅为社区用户自发整理的非官方知识库。

## 一、为什么要做这个 Skill

我之前分享过两篇相关文章。最早的做法是把 QMT 官方文档打印成 PDF，再投喂给腾讯 IMA.Copilot、DeepSeek 等大模型，让 AI 帮我写代码。这种方法能用，但有几个明显痛点：

- `xtquant` 代码是 miniQMT 的，不是大QMT的
- `init / handlebar` 结构经常写错
- `opType`、`prType`、`orderType` 等枚举值经常臆造
- `# coding:gbk` 编码声明经常漏掉
- Python 3.6 不支持 `dataclass` 等新语法

为了解决这些问题，我把整理好的 QMT 官方文档做成了一个标准化的 **Skill 包**：[qmt-knowledge-skill](https://github.com/he-yang/qmt-knowledge-skill)。装上之后，AI Agent 就自带一份结构化、可检索的 QMT 知识库，写出来的代码天然符合大 QMT 的编码规范。

## 二、qmt-knowledge-skill 是什么

一句话：**给 AI Agent 用的 QMT 极速策略交易系统智能知识技能包**。

它把 QMT 官方 Python 3.6 API 文档按「入门 → API → 数据与枚举 → 示例与 FAQ」四大类整理成 16 份 markdown 文档，再加一份 `SKILL.md` 作为 Agent 行为规范。

| 场景 | 示例问题 |
| --- | --- |
| 📖 **新手入门** | 「QMT 怎么运行第一个策略？」「回测和实盘模型有什么区别？」 |
| 💻 **代码生成** | 「帮我写一个双均线策略」「写一个获取全市场 Tick 的脚本」 |
| 🔎 **API 查询** | 「passorder 的参数有哪些？」「get_market_data_ex 怎么用？」 |
| 🎯 **枚举参数** | 「opType 股票买入是多少？」「prType 最新价对应值？」 |
| 🧱 **数据结构** | 「Tick 对象有哪些字段？」「Bar 数据包含什么？」 |
| ⚠️ **错误排查** | 「为什么下单是废单？」「取行情数据为空怎么办？」 |
| 📊 **指标公式** | 「MA、MACD、RSI、KDJ 怎么引用？」 |
| 🛠️ **环境配置** | 「如何下载历史数据？」「模拟账号和实盘账号区别？」 |

## 三、项目结构

```
qmt-knowledge-skill/
├── SKILL.md              # ⭐ Skill 主入口（Agent 行为规范 + 索引 + 约束）
├── README.md             # 项目说明
└── knowledge/            # QMT 官方知识库（按四大类共 16 份文档）
    ├── 01-入门/
    │   ├── 快速开始.md
    │   ├── QMT新人上手教程.md
    │   ├── 迅投研新手指南.md
    │   ├── 使用须知.md
    │   ├── 变量约定.md
    │   └── 界面操作.md
    ├── 02-API/
    │   ├── 交易函数.md
    │   ├── 行情函数.md
    │   ├── 引用函数.md
    │   ├── 系统函数.md
    │   ├── 成交回报实时主推函数.md
    │   └── 绘图函数.md
    ├── 03-数据与枚举/
    │   ├── 枚举常量.md
    │   └── 数据结构.md
    └── 04-示例与FAQ/
        ├── 完整示例.md
        └── 常见问题.md
```

## 四、安装

**命令 1：**
```bash
openclaw skills install @he-yang/qmt-knowledge-skill
```

**命令 2：**
```bash
npx skills add https://clawhub.ai/he-yang/skills/qmt-knowledge-skill
```

## 五、Skill 内置的代码硬约束（重点）

| 规则 | 说明 |
| --- | --- |
| 🔤 **GBK 编码** | 所有脚本第一行必须写 `# coding:gbk`，否则中文乱码 |
| 🪝 **双钩子结构** | 必须有 `def init(ContextInfo):` + `def handlebar(ContextInfo):` |
| 🐍 **Python 3.6** | 不支持 3.7+ 语法特性 |
| 📊 **第三方库** | 默认自带 NumPy/Pandas/TA-Lib/SciPy，其他库需要券商开通白名单 |
| 🔢 **枚举值** | opType/orderType/prType 等必须使用合法数值，禁止臆造 |
| ⚠️ **实盘风险** | 所有生成的 passorder 代码自动附带风险提示语 |

### 标准代码模板

```python
# coding:gbk

"""
功能：双均线策略
模式：回测模型 / 实盘模型
机制：逐K线 handlebar / 订阅 subscribe / 定时 run_time
"""

account = "test"  # 策略交易界面运行时会自动替换为配置账号

def init(ContextInfo):
    """初始化，策略启动时仅执行一次"""
    pass

def handlebar(ContextInfo):
    """逐K线回调，核心策略逻辑"""
    pass
```

## 六、和「PDF 投喂」方案的对比

| 对比项 | PDF 投喂给 DeepSeek | qmt-knowledge-skill |
| --- | --- | --- |
| 上手成本 | 每次对话都要重新上传 | 装一次，长期生效 |
| 生成的代码类型 | 默认 miniQMT / xtquant 代码 | 大 QMT 原生 init/handlebar 代码 |
| GBK 编码 | 经常漏掉 | 强制写入 |
| 枚举值准确性 | 凭记忆可能臆造 | 强制查阅枚举常量 |
| Python 3.6 兼容 | 可能用上 3.7+ 语法 | 硬性约束禁用 |
| 实盘风险提示 | 无 | 自动附带 |

简单说：**PDF 投喂是「一次性参考」，Skill 是「常驻专家」**。

## 七、配合我整理的 QMT 知识库使用

这个 Skill 里的 `knowledge/` 目录，就是我在 QMT 知识库使用文档那篇文章里持续维护的 markdown 版本的子集。

由于现在券商逐步在关闭 miniQMT，所以这个 Skill 后续也只更新大 QMT 的内容，不再维护 xtquant 部分。

**特别说明**：资料是从 QMT 官方网站通过 AI 爬取的，大家使用前可以自己和官方的文档对比一下，具体以官方文档为准。

项目地址：[https://github.com/he-yang/qmt-knowledge-skill](https://github.com/he-yang/qmt-knowledge-skill)，MIT 协议开源。
