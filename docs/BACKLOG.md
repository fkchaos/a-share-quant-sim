# Backlog — A股量化模拟交易系统待办事项

> 记录未解决的问题、待做的优化、搁置的想法。
> 已完成的移到 `docs/HISTORY.md`。

## 🔴 高优先级

### B-01: sim_daily 和 run_backtest 因子/买入逻辑统一
- **状态**: 部分完成
- **描述**: run_backtest 用 `calc_factors_panel` + `composite_score`，sim_daily 用 `calc_factors_single` + `score_all_stocks`，两套独立代码路径
- **目标**: 同一接口，同一实现
- **备注**: 2026-05-30 已统一 core/ 引擎层，但入口函数仍有两套

### B-02: 废弃脚本清理
- **状态**: 待做
- **描述**: `alpha_backtest_v*.py` 系列已废弃，当前在 `scripts/archive/`，确认后删除
- **备注**: 归档后可删，Git 历史保留

## 🟡 中优先级

### B-03: 行业仓位上限 + 指数趋势展示
- **状态**: 部分完成
- **描述**: config 中有行业映射（hs300_constituents.csv）但覆盖不全
- **目标**: 完整的行业分类（全 A 股，不只 HS300）

### B-04: 持仓文件加锁防并发冲突
- **状态**: 待做
- **描述**: 手动跑回测和 cron 18:00 模拟盘可能同时读写 portfolio state
- **目标**: 加 `flock` 或改 cron 触发逻辑
- **备注**: 目前人为避免

## 🔵 低优先级

### B-05: boll_width_10 有计算但 FACTOR_WEIGHTS 没权重
- **状态**: 已解决（2026-05-30 删除 boll_width_10，总权重重归一化）

### B-06: review-response-20260601.md 在 repo 里重复
- **状态**: 待清理

### B-07: 回测引擎接入 ATR 止损
- **状态**: 待做
- **description**: `use_atr_stop` 和 `atr_k` 已在 STRATEGY_PROFILES 中预留参数，核心逻辑未实现

### B-08: Walk-Frame 分析完善
- **状态**: 待做
- **描述**: 多次回测拼接净值曲线展示，当前只支持单次回测

## ❌ 已舍弃

- 新闻情感因子
- IC-IR 加权
- 系统择时
- 增强量价因子
- ML 策略
