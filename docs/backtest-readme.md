# 回测系统

详细架构见 [architecture.md](architecture.md)，回测命令速查见 [README](../README.md)。

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--strategy` | `all` | 策略名或 `all` 跑全部 |
| `--start` | `2021-01-01` | 回测起始日期 |
| `--end` | 今天 | 回测结束日期 |
| `--top-n` | (策略预设) | 持仓数量 |
| `--rebalance-freq` | (策略预设) | 调仓频率（交易日） |
| `--stop-loss` | (策略预设) | 止损比例 |
| `--exec-timing` | `close` | `close`=收盘价(理想) / `open`=开盘价(接近实盘) |
| `--scan` | off | 参数网格扫描 |
| `--walk-forward` | off | Walk-Forward 过拟合检测 |
| `--ic-analysis` | off | IC 因子分析 |
| `--report-markdown` | off | 输出 Markdown 报告 |

## 策略列表

10+ 预置策略，参数在 `core/config.py` 的 `STRATEGY_PROFILES` 字典中定义。

**当前重点策略：**

| 策略 | 因子 | 权重方法 | top_n | 行业限制 | 状态 |
|------|------|---------|-------|---------|------|
| **v11b_zz800_union** ⚡ | 3组×4因子 | ensemble | 12 | ≤25% | WF 验证最优 |
| v6b_hlr | 9 | equal | 12 | ≤25% | 基准 |
| v6b_8f_pos_ic | 8 | equal | 12 | ≤25% | 基准 |
| v10c_zz800_balanced | 13 | equal | 12 | ≤25% | 回测优秀 |

完整策略列表+回测结果见 [STRATEGY_REGISTRY.md](STRATEGY_REGISTRY.md)。

## 快速验证

```bash
# 安装
pip install -r requirements.txt

# 首次运行需下载数据
python scripts/update_daily_data.py

# Golden Tests (< 1s)
python -m pytest tests/ -v

# 回测最优策略
python scripts/run_backtest.py --strategy v11b_zz800_union

# Walk-Forward 过拟合检测
python scripts/run_backtest.py --strategy v11b_zz800_union --walk-forward

# 开盘执行模式（接近实盘）
python scripts/run_backtest.py --strategy v11b_zz800_union --exec-timing open
```

## 与模拟盘一致性

```
run_backtest.py   ──▶ core.account.buy / sell / check_stop_loss
sim_daily_v7.py   ──▶ core.account.buy / sell / check_stop_loss
                        ↑↑↑ 完全相同的函数 ↑↑↑
```

这是整个项目最重要的设计决策 — 回测 bug fix 同时作用于模拟盘。

## 防错机制

- **vol_panel 缺失 warning**：`calc_factors_panel(close_panel)` 不传 vol_panel 时触发
- **stock_names 加载**：失败时有 warning，不静默影响行业限制
- **启动配置摘要**：每次回测打印 ind_cap/decay/open-close 状态
- **结果自检**：负收益/超大回撤/零止损触发时主动告警
- **整手约束**：buy() 强制 `int(shares/100)*100`
