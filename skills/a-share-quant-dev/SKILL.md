---
name: a-share-quant-dev
description: A股量化策略开发全流程。Use when developing new strategy.
---

# A股量化策略开发全流程

## 总览

```
输入标准化 → 调研 → 设计文档 → 因子开发 → IC验证 → WF回测 → 参数扫描 → 实盘集成 → 信号cron → 监控
```

**铁律：每步必须闭环后再进下一步，不跳步。**

---

## 第0阶段：输入项标准化（前置条件）

**所有策略研发必须从标准化输入项开始。不管输入从哪来（外部获取/内部生成/复盘发现），都要归一化到标准格式才能进入流程。**

### 0.1 三层输入定义

| 层级 | 输入类型 | 回答的问题 | 验证方法 |
|------|----------|------------|----------|
| 第一层 | 选股因子 | 买什么 | 截面IC/IR分析 |
| 第二层 | 择时信号 | 什么时候买/卖多少 | 阈值过滤回测 |
| 第三层 | 风控参数 | 亏多少止损/赚多少止盈 | 参数扫描回测 |

### 0.2 归一化流程

**Step 1: 识别输入类型**
- 判断是选股因子/择时信号/风控参数

**Step 2: 填充元数据**
- 按照标准格式填写所有必填字段（详见 `docs/experiments/2026-08-07_strategy_rnd_framework.md` 阶段0）

**Step 3: 数据验证**
- 检查数据完整性、格式正确性、数据源可追溯性

**Step 4: 生成输入ID**
- 唯一标识：`{type}_{name}_{version}`
- 示例：`stock_liquidity_v1`, `timing_breadth_v1`

**Step 5: 存储到标准位置**
- 统一存储：`alpha-research/inputs/`
- 文件：`stock_factors.json` / `timing_signals.json` / `risk_params.json`

### 0.3 阶段0检查清单（必须全部打勾才能进入阶段1）

- [ ] 输入类型已识别（stock/timing/risk）
- [ ] 元数据已填充（所有必填字段）
- [ ] 数据完整性检查通过（无缺失值）
- [ ] 数据格式检查通过（类型正确）
- [ ] 数据源可追溯（有明确来源）
- [ ] 输入ID已生成（唯一标识）
- [ ] 来源已标记（internal/external/复盘发现）

---

## 第1阶段：调研与设计

### 1.1 调研
- 广泛搜索多社区（聚宽/9db/知乎/雪球/Reddit），不闭门造车
- 调研文档存 `docs/experiments/`
- 外部项目非侵入式集成，不改原工程

### 1.2 设计文档
必须输出 `docs/experiments/YYYY-MM-DD_<topic>_design.md`：
- 背景与动机
- 方案对比（至少2-3个方向）
- 因子定义与计算逻辑
- 预期IC/IR目标
- WF验证条件
- **必须回答："什么条件下不交易？"**（空仓逻辑）

---

## 第2阶段：因子开发与IC验证

### 2.1 因子开发规范
- 因子是纯计算单元，策略是组合层，两者解耦
- `calc_factors_*()` 签名必须接受 `extra_data=None`（account_runner传7个参数）
- **⚠️ calc_factors必须从params参数读取动态参数（权重/窗口等），不能读模块级DEFAULT_PARAMS！**（adapter传入的params是运行时参数，DEFAULT_PARAMS是静态默认值，两者不同）
- 新因子放 `core/strategy_map.py` 注册，不硬编码
- 因子返回格式：dict `{"strategy_name": pd.Series}`

### 2.2 IC优先验证
| 指标 | 阈值 | 判定 |
|------|------|------|
| \|IC Mean\| > 0.03 且 \|IR\| > 0.3 | 有效，进入WF |
| \|IC Mean\| < 0.01 或 \|IR\| < 0.1 | 证伪，不进入WF |
| 0.01-0.03 | 微弱信号，不值得投入WF时间 |

**教训：IC强≠WF强。** v74a行业动量IC很强（IC=0.17, IR=0.97）但WF失败（Sharpe -0.855）。

---

## 第3阶段：WF回测

### 3.1 标准条件（极其重要！）
- `train=252, test=126, step_days=63`
- `start='2021-01-01', end='2026-05-31'`
- `pool=zz1800`
- 标杆策略: `v39g`（Sharpe 1.297, 16 folds）

### 3.2 WF运行注意事项
- **不能并行跑两个WF**：OOM被系统杀（每个≈2GB），必须串行
- **结果存文件**：不用StringIO抑制输出
- **长任务输出必须重定向到文件**

---

## 第4阶段：三轮参数扫描（按顺序执行）

**扫描顺序**：因子权重 → 择时参数 → 风控参数。每轮固定前一轮最优值。

### 4.1 因子权重扫描（WF通过后第一步）
- **时机**：WF通过后，先扫因子权重再扫其他
- **方法**：网格粗扫（权重步长0.10）→ 组合精扫（±0.05）
- **用全量回测（full=True）做初筛**，快20倍
- **示例**：突破/放量/流动性三因子，先扫[0.3/0.3/0.4]~[0.5/0.3/0.2]共6组，再精扫±0.05
- **输出**：最优权重 + 对应Sharpe + 对比默认权重的提升幅度

### 4.2 择时参数扫描（权重确定后）
- **时机**：因子权重确定后，扫择时/过滤阈值
- **示例**：广度过滤的BREADTH_HIGH/BREADTH_LOW、波动率缩放的VOL_WINDOW等
- **方法**：单参数扫趋势 → 组合精扫

### 4.3 风控参数扫描（权重+择时都确定后）
- **时机**：因子权重和择时参数都确定后，最后扫风控
- **方法**：先单参数扫趋势，再组合精扫
- **参数**：止损SL、止盈TP、持仓天数HOLD_DAYS_MAX

### 4.4 通用原则
1. **先单参数扫趋势，再组合精扫**（不要上来就全网格）
2. **用全量回测（full=True）做初筛**，快20倍
3. **输出必须存文件**
4. **每轮固定前一轮最优值**，不要三轮一起扫（组合爆炸）

### 4.5 已知陷阱
- `run_wf()`不支持`params_override`参数，必须通过adapter._risk_params直接修改
- `run_wf(full=True)`返回DataFrame不是dict，用`result['test_sharpe'].iloc[0]`取值
- **绝对不要用StringIO抑制所有输出**——必须存文件
- **⚠️ WF内部DEBUG输出必须重定向到/dev/null，不是文件！**（`RCV DBG`每组8000+行，写文件=I/O阻塞，每组从30秒膨胀到50分钟）
- 因子权重必须归一化（和=1.0），否则改变权重总和会影响选股结果
- 权重扫描不要跳步：扫完A因子最优值后，固定A再扫B
- **断电续跑**：结果每完成一组立即写文件，重启时自动跳过已完成组
- **⚠️ 全量回测Sharpe高≠WF高**：全量回测容易过拟合，WF才是唯一可信评价标准
- **⚠️ calc_factors必须接受动态权重参数**：否则WF验证时权重不生效，扫描结果无法验证
- **⚠️ calc_factors必须从params参数读取动态参数（权重/窗口等），不能读模块级DEFAULT_PARAMS**
- **⚠️ adapter调用calc_factors时必须传入merged_params**（否则动态参数不生效）
- **⚠️ 弱信号因子优化=拟合噪声**（IC<0.03的因子做权重/窗口优化没有意义）
- **⚠️ regime选择比因子选择更重要**（广度过滤是regime选择器，不是风险控制）
- **⚠️ 因子衰减是真实存在的**（中国A股技术因子半衰期约18个月）
- **⚠️ 分regime IC分析比全局IC更重要**（regime-dependent因子在全局IC下可能是弱信号）

---

## 第5阶段：实盘集成

### 5.1 代码层
- [ ] `calc_factors_*` 必须接受 `extra_data=None`
- [ ] `select_stocks_*` 必须支持 `return_all=False`
- [ ] DEFAULT_PARAMS 包含所有风控参数

### 5.2 注册层
- [ ] `core/strategy_map.py` 注册策略
- [ ] `strategy_adapter.py` 添加 `_select_fns/_risk_params/_vXX_select()`
- [ ] adapter._vXX_select() 必须传 `return_all`

### 5.3 格式层
- [ ] `format_report.py` 添加策略公式说明
- [ ] 广度/市场情绪在Top10前显示
- [ ] return_all从adapter一路传到策略层

### 5.4 文档层
- [ ] CLAUDE.md 更新策略参数表
- [ ] RESULTS_LOG.md 追加WF结果

---

## 第6阶段：信号Cron集成

### 6.1 信号报告必须包含
- ✅ 现金 + 持仓数
- ✅ 广度/市场情绪（含公式和说明）
- ✅ 选股Top10得分（含策略公式 + 因子解释）
- ✅ 卖出/买入计划
- ✅ 持仓明细

### 6.2 选股过滤（打分前执行）
- 科创板过滤（688/689）——排序前过滤
- 涨停过滤——打分不排除，买入计划排除
- 跨账户去重
- 卖出又买入优化

---

## 执行前检查清单（关键操作前必须逐条确认）

### WF回测检查清单
- [ ] 已加载skill确认具体步骤
- [ ] 输出已重定向到文件或/dev/null（不是StringIO）
- [ ] 已确认不是并行运行（OOM风险）
- [ ] 已确认quiet=True（默认抑制DEBUG输出）
- [ ] 已确认参数传递正确（adapter→calc_factors）

### 输入标准化检查清单（阶段0）
- [ ] 输入类型已识别（stock/timing/risk）
- [ ] 元数据已填充（所有必填字段）
- [ ] 数据完整性检查通过（无缺失值）
- [ ] 数据格式检查通过（类型正确）
- [ ] 数据源可追溯（有明确来源）
- [ ] 输入ID已生成（唯一标识）
- [ ] 来源已标记（internal/external/复盘发现）
- [ ] 已存储到 `alpha-research/inputs/` 标准位置

### 参数扫描检查清单
- [ ] 已确认扫描顺序：因子权重→择时参数→风控参数
- [ ] 已确认每轮固定前一轮最优值
- [ ] 已确认结果每完成一组立即写文件（断电续跑）
- [ ] 已确认用全量回测（full=True）做初筛

### 策略集成检查清单
- [ ] calc_factors_*接受extra_data=None和weights/windows参数
- [ ] select_stocks_*支持return_all=False
- [ ] strategy_map.py已注册
- [ ] strategy_adapter.py已添加适配器
- [ ] format_report.py已添加策略公式说明
- [ ] CLAUDE.md已更新策略参数表

---

## 常见错误清单

| 错误 | 后果 | 预防 |
|------|------|------|
| calc_factors不接受extra_data | account_runner传7参数报错 | 签名必须有extra_data=None |
| return_all没从adapter传到策略 | top_scores只返回Top3 | 逐层检查参数传递 |
| format_report没有策略公式 | 信号只显示"综合评分" | 每加策略必须同步format_report |
| 科创板过滤在排序后 | Top10数量不足 | 排序前过滤 |
| 参数扫描用StringIO | 无法事后诊断 | 输出必须存文件 |
| WF DEBUG输出写文件 | I/O阻塞，每组从30秒变50分钟 | 重定向到/dev/null，不是文件 |
| 参数扫描断电 | 结果全丢 | 每组完成立即写文件，重启跳过已完成组 |
| calc_factors读DEFAULT_PARAMS | 扫描参数不生效，所有组结果相同 | calc_factors必须从params参数读取动态值 |
| adapter不传params给calc_factors | 同上，参数修改无法传递 | adapter调用calc_factors时必须传入merged_params |
| 两个WF并行 | OOM被杀 | 串行执行 |
