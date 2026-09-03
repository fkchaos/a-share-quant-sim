# CLAUDE.md — A股量化模拟盘项目

> 本文件在每次会话开始时自动注入，必须遵守。

---

## 项目概况

- A股量化模拟盘，GitHub: fkchaos/a-share-quant-sim
- 账户1 = v61c（低换手小票+到期续持优化，overlay模式，REBALANCE_DAYS=5），10万，运行中
- 账户2 = v75j（科技趋势+流动性单因子+广度过滤，Sharpe 2.043），10万，运行中
- 股票池：zz1800
- DB: data/quant_stocks.db + data/quant_accounts.db

### 当前策略参数

| 参数 | v61c（账户1） | v75j（账户2） | v75h（实验） | v39g（基准） |
|------|--------------|-------------|-------------|-------------|
| STOP_LOSS | -0.08 | -0.08 | -0.08 | — |
| TAKE_PROFIT | 0.25 | 0.25 | 0.30 | — |
| HOLD_DAYS_MAX | 5 | 20 | 15 | — |
| MAX_DAILY_BUY | 5 | 3 | 3 | — |
| MAX_POSITION | 0.25 | 0.35 | 0.35 | — |
| MAX_HOLDINGS | 5 | 3 | 3 | — |
| REBALANCE_DAYS | 5 | 10 | 10 | — |
| BREADTH_MA | — | 20 | 20 | — |
| BREADTH_HIGH | — | 0.50 | 0.50 | — |
| BREADTH_LOW | — | 0.30 | 0.30 | — |
| W_BREAKOUT | — | 0.45 | 0.35 | — |
| W_VOL_SURGE | — | 0.30 | 0.30 | — |
| W_LIQUIDITY | — | 0.25 | 0.35 | — |

### 回测/WF 结果

| 策略 | 全量回测收益 | 年化 | Sharpe | 回撤 | WF 收益/fold | WF Sharpe | 正fold率 |
|------|------------|------|--------|------|-------------|-----------|---------|
| v61b | +935% | 73.81% | 1.551 | -33.8% | +31.60% | 2.152 | 87.5% |
| v61c | — | — | — | — | +37.6% | 2.530 | 93.8% |
| v68 | +152.4% | — | 0.675 | -28.3% | +10.08% | 1.429 | 81% |
| v75a | — | — | — | — | +39.59% | 1.642 | 80% |
| v75j | +335.5% | — | 1.048 | -38.8% | +62.12% | 2.043 | 81.25% |
| v75h | — | — | 0.803 | -43.0% | +39.31% | 1.612 | 93% |

---

## � 工作原则（主公明确要求，违反即错）

### 1. 先设计后实施
非 trivial 任务必须先写设计文档（`docs/experiments/YYYY-MM-DD_<topic>_design.md`），包含：背景、方案对比、实验步骤、回测条件。

### 2. 回测条件标准化（极其重要！）
所有 WF 对比必须在**完全相同**的条件下进行：
- train=252, test=126, step=63
- start=2021-01-01, end=2026-06-30（固定截止日，不得随意更改）
- pool=zz1800（除非特别说明）
- 标杆策略: v39g（全策略 Sharpe 1.297, 16 folds）

**禁止**在不同时间区间/不同 folds 数/不同 pool 的结果之间做比较。

### 3. IC 优先验证原则
新因子必须先做 IC/IR 分析：
- |IC Mean| > 0.03 且 |IR| > 0.3 → 有效，可进入 WF
- |IC Mean| < 0.01 或 |IR| < 0.1 → 证伪，不进入 WF
- 微弱信号（0.01-0.03）→ 不值得投入 WF 时间

### 4. 架构解耦与可扩展
- **任何修改都要考虑架构解耦和可扩展**
- 因子是纯计算单元，策略是组合层，两者解耦
- 新因子放 `core/strategy_map.py` 注册，不要硬编码
- 选股/风控/执行/组合各层独立

### 5. 搜索调研交叉进行
每个关键步骤前搜索最新资料（web_search / ddgr / firecrawl），不要闭门造车。外部获取的信息要存档到 `alpha-research/reports/`。

### 6. 文档同步更新
代码改动后**立即**同步以下文件（不等提醒）：
- `docs/TODO.md`
- `docs/strategy/RESULTS_LOG.md`
- `docs/strategy/STRATEGIES_DISCARDED.md`（证伪策略）
- `docs/experiments/YYYY-MM-DD_<topic>_results.md`

### 7. 长任务用 stream 模式
超过 30s 的任务必须用 `terminal(background=true)` + `process(action='poll')`，不要用 execute_code 或前台长命令。

### 8. 科学严谨 / 实验完整性
- 单个实验必须有完整记录（设计→IC→WF→结论）
- 不能因"感觉没希望"就跳过记录，失败实验同样有价值
- 每次实验的因子/参数/结果/教训都要落文档

---

## 🔴 已知陷阱（已踩坑，不要再踩）

1. **改参数必须同时改 strategy_map.py 和策略文件 DEFAULT_PARAMS**
2. **DB amount 单位是元，换数据源必须确认单位**
3. **load_panel_from_db 返回顺序**: [close, vol,amt, open, high, low]
4. **SQLite 多线程写入要每线程独立连接**
5. **SQLite WAL 模式**: executemany + 前后 COUNT 差值，不用 total_changes
6. **account.strategy 字段可能静默丢失**：每次手动 DB 操作后必须验证
7. **cron prompt 必须用 switch && run**，不依赖 DB 已有状态
8. **execute_code 在 cron 模式下被禁止**
9. **wf_runner.py 默认 test=252**，需要手动指定 test=126 step=63 才是标准 16 folds
10. **strategy_adapter.get_risk_params() 返回副本**，直接改 adapter._risk_params['v39g']
11. **⚠️ 腾讯K线API volume 单位是手（1手=100股），不是股！** 计算换手率需 `volume * 100 / float_shares`，否则偏差100倍
12. **⚠️ 迅投因子看板IC数据不能直接复现** — 与我们的IC差10倍以上，原因未明（API加密、计算方法不透明）。外部因子数据仅作方向参考，必须在自己池子重验
13. **⚠️ miniQMT已停服（2026.7.6）** — 只能用大QMT内置Python 3.6.8，或等券商确认xtquant外部调用是否可用
14. **⚠️ Python 3.6.8兼容性** — 不能用 `:=` walrus、`dict | dict`、f-string `=`号、`pd.DataFrame.map()`，新代码必须兼容3.6.8
15. **⚠️ 科创板(688/689)过滤分层** — 数据源层已放开（`get_tradeable_codes`返回全量），WF层默认过滤（`--no-exclude-star`可放开），策略层v75a硬编码过滤（新策略v75n绕过）。回测含科创板时需同时满足：①WF不过滤 ②策略不排除 ③数据已回补
16. **⚠️ QMT adapter重写不能简化策略逻辑** — 重写qmt_adapter时必须完整移植原有选股逻辑（v61c低换手+小市值、v75j科技趋势+流动性+广度过滤），不能只用市值/流动性排序简化版。参考原始策略：`scripts/strategies/v61_turnover_size.py`和`v75j_liquidity_only.py`
17. **⚠️ QMT回测看不到交易记录** — passorder调用成功但回测界面无记录，可能是回测初始资金/账户配置问题，需在QMT界面确认。详见`docs/qmt/QMT_ADAPTER_TODO.md`
18. **⚠️ QMT get_market_data_ex end_time** — 不传end_time返回最新价，传end_time返回指定日期收盘价。买入用最新价（不传end_time），风控PnL用bar日期收盘价（传end_time）
19. **⚠️ QMT get_market_data_ex subscribe参数** — `subscribe=False`仅主图品种有效，非主图品种返回空。交易非主图股票必须用`subscribe=True`（默认值）
20. **⚠️ QMT passorder quickTrade参数** — 回测必须用`quickTrade=0`（逐K线模式），不能用1（仅最新K线触发）
22. **⚠️ QMT m_dOpenPrice是真实成本** — 不要自己记录买入价覆盖m_dOpenPrice。如果m_dOpenPrice与预期不符，先检查end_time参数，再检查账户状态
23. **⚠️ v61c SELL_OUT_OF逻辑** — v61c的核心是到期续持优化（持有5天到期→检查Top15→在则续持）。QMT版必须实现此逻辑，否则退化为v61b
24. **⚠️ get_close_price必须先subscribe** — `subscribe=False`仅主图品种有效。`get_close_price`/`get_close_prices_batch`获取非主图股票价格前必须先`subscribe_quote()`，否则返回0导致无法买入
25. **⚠️ QMT策略init()中today变量** — `_get_bar_date(C)` 必须在init()内显式调用赋值给`_today_init`，不能依赖后续函数的局部变量。否则NameError→hold_days被重置
27. **⚠️ QMT订单状态机** — 用`userOrderId`（remark）跟踪订单状态：pending→ordered→filled。`order_callback`返回`m_strOrderSysID`用于撤单。部分成交用`filled += dealInfo.m_nVolume`累计
28. **⚠️ QMT撤单超时** — 60秒无`order_callback`→废单（rejected）。`ordered`状态超时→调用`cancel`撤单。撤单前必须`can_cancel_order`检查
29. **⚠️ QMT持仓JSON更新** — 必须在`deal_callback`（成交确认）后更新，不能在`buy`/`sell`调用时立即更新
30. **⚠️ QMT选股阶段过滤** — 涨停过滤（close==high）和资金容量过滤（买不起1手）必须在选股阶段（buy_list生成）执行，不是买入执行阶段
31. **⚠️ QMT风控T+1** — 持有首日（hold_days<1）跳过风控检查，不卖
32. **⚠️ QMT涨停不卖止盈** — 涨停时不卖（止盈保留），防止卖出后无法买回
33. **⚠️ QMT HOLD_DAYS_EXTEND** — 盈利>阈值时可延期持有，用`hold_days_extend`参数
34. **⚠️ QMT MAX_STOCK_PRICE** — 排除超过价格上限的股票，参数在config.py中配置
35. **⚠️ v61e排名掉出Top15不是启动信号** — v61e假设"排名掉出Top15=启动信号"，WF回测Sharpe 0.913远低于v61c的2.530。排名掉出=换手率升高/市值变大=资金在撤，不是流入。如需寻找启动信号，建议研究资金流向、板块轮动等更直接的指标

26. **⚠️ QMT策略文件必须GBK编码且不用中文** — 所有.py文件第一行必须`#coding:gbk`，文件必须实际保存为GBK编码。字符串常量用英文（如TECH_SECTORS用'Electronics'不用'电子'），UTF-8编辑器修改GBK文件=中文变"锟斤拷"

---

## 项目结构

```
core/           — 共享引擎（account, db, strategy_map, factors, trading）
  providers/      — 交易Provider（sim_provider=模拟盘, qmt_provider=QMT实盘）
  trading.py      — 交易门面层（直接模式/Provider模式切换）
  trading_provider.py — Provider基类接口
  provider_factory.py — Provider工厂函数
qmt_adapter/    — QMT适配层（大QMT Python 3.6环境）
  data.py         — QMT行情→我们格式的转换器
  trading.py      — passorder封装+账户查询封装
  strategy_skeleton.py — init()+handlebar()策略骨架
scripts/strategies/ — 策略文件：
  - v61_turnover_size.py      # v61b基础（低换手小票因子）
  - v61b_turnover_size.py     # v61b叠加信号
  - v61c_turnover_size.py     # v61c到期续持优化
  - v61d_turnover_size.py     # v61d含科创板（搭配--no-exclude-star）
  - v68.py                    # v68（v67优化版，情绪择时）
  - v39g_optimized.py         # v39g基准策略
  - v39c_pv_resonance.py      # v39c-g/i共用因子
  - v58a_breakout.py          # 突破策略
  - v75j_liquidity_only.py    # v75j流动性单因子（账户2策略）
  - v75n_no_star_filter.py    # v75n含科创板（搭配--no-exclude-star）
  - ...（更多见 scripts/strategies/）
scripts/backtest/   — WF框架：
  - wf_runner.py              # Walk-Forward运行器 + --full全量回测
  - strategy_adapter.py       # 策略适配器（选股+风控，overlay注册）
  - v61b_risk_scan.py         # v61b overlay信号/回测入口
scripts/sim/        — 模拟盘执行（account_runner.py）
scripts/factors/    — 因子研究脚本（v82/v83 IC分析）
scripts/tools/      — 工具（init_project.py, update_daily_data_async.py, format_report.py）
docs/               — 正式文档
docs/experiments/   — 实验文档（设计+结果+计划）
docs/qmt/knowledge/ — QMT API知识库（16个文件，交易/行情/枚举/示例/FAQ）
data/               — SQLite 数据库（quant_stocks.db + quant_accounts.db）
alpha-research/     — 因子研究（独立目录，外部研究工具，不移植代码）
  reports/xuntou/   — 迅投因子看板数据存档
```

## 实验编号规则

- v39x: 基线策略系列（v39g为标准基准）
- v40-55x: 历史实验
- v58x: 外部策略验证（BigQuant/短线策略）
- v59x: Alpha158/Alpha191 新算子验证
- v60x: 中性化/优化方向
- v61x: 低换手小票系列（v61b为当前账户1策略）
- v62-68x: 中短线策略探索（v68为当前账户2策略）
- v69+: ETF轮动/行业动量等新方向
- v82x: 迅投因子看板因子验证（8因子全部无效）
- v83x: 迅投IC差异根因排查（R1-R3排除5个可能）
- v84x: 科创板实验（v84a冗余=v75n）
- v85x: 板块动量/动态仓位实验（v85a失败, v85b微升）
- v86x: Factor Factory第二批因子验证（v86a/v86b WF全部失败）
- 所有有价值的实验方向对应一个 todo 项

## 汇报风格

- 加 "主公，有事向您 直接给结论，不要绕弯子
- 涉及策略结论时同时给出数字和判断
