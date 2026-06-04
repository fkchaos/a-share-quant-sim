# HISTORY — 已解决的问题记录

> 从 MEMORY 迁移过来的历史排查结论。按需查阅。

---

## 2026-06-04: 模拟盘 plan 结构重构

### 背景
原来风控操作（止损/止盈/decay）单独放 `risk_plan` 字段，调仓操作放 `sell_plan`，逻辑分散。

### 修改
- 风控操作合并到 `sell_plan`，用 `reason` 字段区分（`止损`/`分级止盈`/`分级止盈清仓`/`持有期decay`/`非目标持仓`）
- plan 结构简化为 `sell_plan`/`hold_plan`/`buy_plan` 三个字段
- 风控在上午信号执行，下午只执行调仓（不重复风控）
- 非调仓日也生成 plan（sell/buy 为空，hold 包含全部持仓），覆盖旧数据防脏读

---

## 2026-06-04: 模拟盘 cron 报告格式规范化

### 上午信号报告
- 一、当前持仓（执行前）
- 二、操作计划（卖出/补仓/新买入/持有不动）
- 三、汇总（现金占比/预期持仓数/调仓日/ML状态）

### 下午执行报告
- 一、执行明细（卖出/买入/补仓，含状态）
- 二、执行后持仓
- 三、净值变化
- 四、异常/注意事项

### 收盘报告
- 一、今日操作汇总
- 二、当前持仓
- 三、净值
- 四、指数概况

---

## 2026-06-04: 非调仓日旧 plan 未清除导致误全量换仓

### 现象
调仓日 trade_count 更新为 21 后，上午信号 cron 识别为"非调仓日"返回 no_rebalance，但未清除旧的 trade_plan.json。下午执行 cron 加载了旧的调仓计划，执行了全量换仓。

### 根因
`step_generate_signal` 非调仓日分支直接返回，没有写入新的 plan 覆盖旧文件。

### 修复
非调仓日也写入 plan（sell/buy 为空，hold 包含全部持仓），覆盖下午检查为空则跳过。

### 教训
**任何可能产生副作用的文件（plan/state）写入必须是覆盖写，不能跳过。**

---

## 2026-06-04: sell log emoji 歧义

### 现象
卖出日志用 `❌` emoji，cron 报告解读为"失败"。

### 修复
改为 `📉`（表示"已卖出"），买入用 `✅`，持有用 `➡️`，补仓用 `🔺`。

---

## 2026-06-01: v9 短线策略验证失败

### 尝试
- 加入 3 个短线因子：gap_ratio(IC=+0.01)、high_low_range(IC=+0.02~0.06)、intraday_drift(IC≈0)
- 建 v9_short_term Profile: 8 因子（rev_3/rev_5/vol_ratio_5/high_low_range/mom_5/rsi_6/gap_ratio/vol_change），freq=5 天
- 全历史回测（2021-01 ~ 2026-06）

### 结果
| 指标 | v9_short_term | v6b 基准 |
|------|-------------|---------|
| 年化 | **-7.68%** | +23.81% |
| 夏普 | **-0.34** | +1.33 |
| 最大回撤 | **49.12%** | 21.18% |
| 交易次数 | 5179 | 5046（60 调仓） |

### 结论
- **RETIRED**: A 股 T+1 + 交易成本下，freq=5 天调仓频率不可行
- 只有 high_low_range 因子有正向 IC，但不足以覆盖摩擦成本
- 因子计算代码保留在 core/factors.py，但 v9 Profile 已注释

---

## 2026-06-01: v6b_8f_pos_ic 确定为最优策略

### 过程
完整回测 5 个策略 × 2 种执行模式（close/open），v6b 在两种模式下均领先。

### 结论

| 策略 | close夏普 | open夏普 |
|------|---------|---------|
| **v6b_8f_pos_ic** ⚡ | **1.33** | **1.05** |
| v8_all_icir | 1.25 | 0.99 |
| v5_tp_decay | 1.14 | 0.79 |

### 行动
- sim_daily_v7 默认策略切换为 v6b_8f_pos_ic
- cron job 从 dev/default 切到 main

---

## 2026-06-01: 回测基准数字三岔路（17% / 20.72% / 24.82%）

### 现象
同一策略跑出三个不同年化收益数字。

### 根因

| 数字 | 根因 | 状态 |
|------|------|------|
| **17%** | `calc_factors_panel(close_panel)` 没传 `volume_panel/amount_panel` → vol_ratio 因子全灭 → 评分失真 | ✅ 已修复 |
| **20.72%** | 用了 HS300 股票池 + 行业仓位上限 25% 约束，收益被压低 | 这是正确的行业限制版基准 |
| **24.82%** | 无行业限制 + vol_panel 正确传入，这才是无约束基准 | ✅ 以此为准 |

### 教训
- **静默失败比 crash 更危险**：vol_panel 缺失不报错，只是数字全零
- **参数必须可追溯**：任何回测数字必须记录调用参数，否则无法复现
- 已在 run_backtest.py 加入 warning：vol_panel 缺失时打印 ⚠️

---

## 2026-06-01: boll_width_10 因子幽灵

### 现象
factors.py 里有 `boll_width_10` 计算代码，但 `FACTOR_WEIGHTS` 里没有它的权重（总共30个计算 vs 29个权重）。

### 处理
直接删除 `boll_width_10`，并将 `boll_width_20` 权重从 0.02 调为 0.03，保持总权重归一。

---

## 2026-05-30: sim_daily 和 run_backtest 两套代码

### 现象
回测和模拟盘用不同的因子计算、评分、交易逻辑 → 回测结果无法代表模拟盘表现。

### 处理
统一到 core/ 引擎，两套脚本都通过 `from core.account import ...` 调用相同函数。
代码级验证：正则扫描两个文件调用的函数名集合完全一致。

---

## 2026-05-30: /root/core/ 旧版残留

### 现象
`/root/core/` 和 `/root/a-share-quant-sim/core/` 同时存在，sim_daily 的 `sys.path.insert(0, "/root")` 导致优先加载旧版。

### 处理
1. 删除 `/root/core/` 旧版
2. sim_daily 的 sys.path 改为 `/root/a-share-quant-sim`

---

## 2026-05-30: sim_daily 非调仓日 trade_count 返回 0

### 现象
`step_rebalance` 在非调仓日返回 `trade_count=0`（硬编码），而非实际累加值。

### 处理
改为返回实际 `trade_count` 值。

---

## 2026-05-30: holdings 中 tp_taken 字段每次加载后被重置

### 现象
`step_load_account` 从 JSON 恢复 holdings 时，没有加载 `tp_taken` 字段 → 分级止盈状态丢失。

### 处理
load_state 时自动补 `tp_taken: []`。

---

## 2026-06-04: 数据源 API 可用性测试与多源 fallback

### 测试结果

| API | 状态 | 说明 |
|-----|------|------|
| **腾讯 (qt.gtimg.cn)** | ✅ 稳定 | 实时行情 + K线 + 批量获取，限速 0.15s/批 |
| **AKShare** | ⚠️ 间歇 | 已安装但连接不稳定，需要重试机制 |
| **东方财富** | ❌ 被反爬 | 股票列表 API 严格封锁，K线 API 间歇性可用 |
| **Tushare** | ⚠️ 需 token | 已安装，需要注册 token |
| **新浪财经** | ❌ 需 cookie | 实时行情返回 Forbidden |
| **BaoStock/yfinance** | ❌ 未安装 |

### 方案
新增 `scripts/data_fetcher.py` 统一 fallback 模块：
- 主源：腾讯 API（实时行情 + K线 + 批量获取）
- 备源：AKShare（全量股票列表 + K线）
- 备源：东方财富（K线，间歇性可用）
- 备源：Tushare（需要 token）
- 本地缓存机制（24h 有效）

### 教训
**不要在项目里硬编码单一数据源。** 必须有多源 fallback + 缓存机制，防止单点故障导致整个系统不可用。
---

## 2026-06-03: 选股池构建 — 从280只到2838只

### 背景
原选股范围仅限沪深300成分股（280只），导致行业覆盖不全（B-03问题）。
目标：扩大到沪深主板+创业板全量股票。

### 数据源测试结果
| 数据源 | 状态 | 说明 |
|--------|------|------|
| AKShare stock_info_a_code_name | ✅ 5524只 | 唯一稳定的AKShare接口 |
| AKShare stock_zh_a_spot_em | ❌ 连接超时 | 东方财富封禁 |
| AKShare 财务数据接口 | ❌ NoneType | 所有财务API不可用 |
| 腾讯 qt.gtimg.cn 实时行情 | ✅ 批量80只 | 最稳定，字段[37]=成交额(万元), [44]=流通市值(亿), [45]=总市值(亿) |
| 腾讯 web.ifzq.gtimg.cn K线 | ✅ | 日K线稳定 |
| 新浪 hq.sinajs.cn | ❌ 403 | 需cookie |
| 东方财富 push2 | ❌ 502 | 间歇性 |
| 东方财富 财务数据API | ❌ 9501 | 报表配置不存在 |

### 字段解析关键发现
腾讯行情 `~` 分隔字段：
- [37] = 成交额（**万元**，不是元！）
- [44] = 流通市值（亿元）
- [45] = 总市值（亿元）
- [38] = 换手率(%)
- [39] = PE

**踩坑**：amount字段单位是万元，如果当元处理（乘10000）会导致阈值比较错误。

### 方案
新增 `scripts/stock_pool.py`，三层过滤：
1. **基础准入**（实时行情）：非ST、非停牌、市值≥50亿、成交额≥3000万
2. **垃圾股排除**（财务数据）：亏损+营收不足、净资产为负、商誉占比≥50%（待财务数据源接入）
3. **K线过滤**：上市满180天、日均成交额≥5000万（可选）

板块：包含沪主板(60)+深主板(00)+创业板(30)，排除科创板(688/689)+北交所(920/8/4)

### 结果
- 全量 5524 只 → 快速模式 2838 只（~30s）
- 板块分布：沪主板 1154、深主板 935、创业板 749
- 零残留：北交所/科创板/ST 全部排除

### 已知局限
- 财务数据不可用，第二层过滤暂时跳过
- 成交额用当日值近似日均，不如20日均值精确
- 创业板689009（九号公司）需特殊处理

### 教训
1. **ALL quantity fields' units must be verified** against the raw API output before setting thresholds
2. 成分股范围过小会导致行业覆盖不全，选股池必须足够大
3. 数据源稳定性 > 数据源数量，腾讯HTTP接口是目前最可靠的免费源

### 补充教训（2026-06-03 选股池扩大过程中）
4. **财务数据源不可靠时的工程决策**：AKShare stock_financial_report_sina 接口在并发下被限流到 ~9只/s，批量获取 2838 只财务数据不可行。此时不应阻塞系统开发，而应采用备选方案（中证500+沪深300成分股 632 只）作为 fallback，财务过滤后续迭代。
5. **PE 作为质量过滤的代理指标**：PE<0 近似亏损股（533/2838≈19%），PE>200 近似异常估值（256/2838≈9%）。在无法获取完整财务数据时，0<PE≤100 可有效排除大部分垃圾股，但会误伤周期性行业暂时亏损的优质公司。

---

## 2026-06-05: 统一评分引擎重构

### 改动
- `core/scoring.py`: 加 `ensemble_union_score()` (panel) + `ensemble_union_score_single()` (单股)
- `core/strategy.py`: `StrategyEngine` 支持 factor/ensemble/ml/hybrid 四种模式
- `core/config.py`: `StrategyConfig` 加 `ensemble_groups` + `ensemble_group_top_n`
- `run_backtest.py`: 评分构建统一走 `StrategyEngine.score_panel()`，WF 支持 `ensemble_groups`
- `core/__init__.py`: 导出新函数

### 结果
- v11b WF 验证通过：年化 63.7%, Sharpe 1.70, 正收益 11/16 (69%)
- 回测和模拟盘共用同一评分入口，消除不一致

---

## 2026-06-05: 收盘报告改为纯只读模式

### 背景
cron 收盘 job 调用 `run_day_end()` 执行完整流程（止损/止盈/decay/调仓），导致下午已执行的操作重复执行。

### 修复
- `run_day_end(report_only=True)`: 只加载账户 + 读本地价格 → 出报告，不修改 state
- CLI 新增 `report_only` 模式
- cron 收盘 job: `day_end` → `report_only`
- `step_report` 中 `nav_history.append()` 加 `if mode != "report_only"` guard

### 踩坑
1. 函数体重写用 `str.replace()` 多次替换容易遗漏旧代码 → 用 `re.DOTALL` 匹配整个函数体一次性替换
2. `step_update_data()` 在 report_only 分支被无条件调用 → 715只股票拉 API 卡死 → report_only 跳过数据更新
3. `zz800_constituents.csv` 列名是 `code`/`name`，不是 `品种代码`/`品种名称` → 股票名称全部显示为代码

### 教训
**大函数重构用完整替换，不零散 patch。report_only 模式不更新数据（用本地价格，净值误差一天可接受）。**

---

## 2026-06-05: 脚本归档清理

### 改动
- `scripts/`: 50 → 16 核心脚本
- 34 个临时研究脚本归档到 `scripts/archive/`（分 research/tune/wf/v11b_explore 子目录）
- `core/small_cap_timer.py` → `core/archive/`（小市值择时模块，已弃用）
- 修复 `log_backtest_result.py` 引用路径

---

## 2026-06-05: 远端分支清理

### 改动
- 删除远端 `feature/ml-rolling` 分支（功能已在 main 中）
- 删除本地 `dev-tmp`、`intraday-sim`、`unify-core-engine` 旧分支
- `release/default` 与 `main` 同步
