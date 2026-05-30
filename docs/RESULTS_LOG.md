# 回测结果记录

> 每次跑完回测后，将结果追加到此文件。
> 自动化脚本 `scripts/log_backtest_result.py` 可一键追加。

## 记录格式说明

| 字段 | 含义 |
|------|------|
| **Date** | 回测执行时间（YYYY-MM-DD HH:MM） |
| **Commit** | git commit hash（前8位） |
| **Data Range** | 数据起止日期 + 股票池大小 |
| **Strategy** | 策略标签（如 v4_baseline, v5_tp_decay） |
| **Parameters** | top_n / rebal_freq / stop_loss / weight_method / industry_cap / turnover_cap / tp_tiers / decay |
| **Annual Return** | 年化收益 |
| **Sharpe** | 夏普比率 |
| **MaxDD** | 最大回撤 |
| **Calmar** | Calmar 比率（年化收益 / |最大回撤|） |
| **Trade Count** | 总交易次数 |
| **Notes** | 备注：改进点、发现的问题等 |

---

## 基准测试集 Golden Tests

### GT-1: v4_baseline（无行业限制）
- **目的**：验证 core/ 引擎计算正确性基准
- **固定参数**：`top_n=12, rebal_freq=20, stop_loss=0.20, stock_names=None, max_industry_weight=0, max_daily_turnover=0`
- **预期结果**：年化 24.82% / 夏普 1.11 / 回撤 -28.87%（允许 ±1% 偏差）
- **数据**：全量数据（2021-01-01 ~ 最新），vol_panel 必须传入

### GT-2: v4_baseline + 行业限制
- **目的**：验证行业仓位上限逻辑
- **固定参数**：`top_n=12, rebal_freq=20, stop_loss=0.20, stock_names=hs300, max_industry_weight=0.25, max_daily_turnover=0`
- **预期结果**：年化 20.72% / 夏普 0.97 / 回撤 -27.01%（允许 ±1% 偏差）

---

## 结果记录

| Date | Commit | Data Range | Strategy | Parameters | Ann.Ret | Sharpe | MaxDD | Calmar | #Trades | Notes |
|------|--------|-----------|----------|------------|---------|--------|-------|--------|---------|-------|
| 2026-06-02 | dev | 2021-01-01~2026-05-29 (285只) | v4_baseline | top12,rf20,sl0.20,no_ind_cap | 24.82% | 1.11 | -28.87% | 0.86 | - | 基准（无行业限制），stock_names=None |
| 2026-06-02 | dev | 2021-01-01~2026-05-29 (285只) | v4+ind_cap | top12,rf20,sl0.20,ind_cap25% | 20.72% | 0.97 | -27.01% | 0.77 | - | 行业限制25%，stock_names=hs300 |
| 2026-06-02 | dev | 2021-01-01~2026-05-29 (285只) | v5_tp_decay | top12,rf20,sl0.20,TP+decay,no_ind_cap | 23.97% | 1.37 | -20.05% | 1.20 | - | 分级止盈+持有期decay，无行业限制 |
