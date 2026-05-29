# A股量化模拟交易系统

> 基于多因子评分的A股量化模拟交易系统，使用腾讯行情接口获取数据。
> 回测引擎与模拟盘共享同一套交易逻辑（`core/`），策略更改一处生效，杜绝回测/实盘不一致。

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## 架构概览

```
┌──────────────────┐          ┌──────────────────┐
│  sim_daily_v6.py │          │  run_backtest.py │
│  (模拟盘调度)     │          │  (回测引擎)       │
└────────┬─────────┘          └────────┬─────────┘
         │ 调用                         │ 调用
         ▼                             ▼
┌─────────────────────────────────────────────┐
│                  core/ (共享引擎)             │
│  config.py  ← 加载 config.yaml (typed)       │
│  factors.py ← calc_factors_single / _panel   │
│  account.py ← PortfolioState + buy/sell/     │
│               check_stop_loss / portfolio_value│
│  scoring.py ← Z-score + 加权评分              │
└─────────────────────────────────────────────┘
         ▲                             ▲
         │ 数据                         │ 数据
┌────────┴─────────┐          ┌────────┴─────────┐
│ update_daily_    │          │ data/daily/       │
│ data.py          │          │ (本地 CSV 面板)    │
│ (腾讯 API → CSV) │          └──────────────────┘
└──────────────────┘
```

## 特性

- **多因子策略**: 29个技术因子（动量、反转、成交量、波动率、RSI、MACD、布林带、偏度、峰度、ATR、VWAP、相对强度等），权重在 `config.yaml` 中配置
- **共享交易逻辑**: `sim_daily_v6.py`（模拟盘）和 `run_backtest.py`（回测）共用 `core/account.py` 的 `buy()` / `sell()` / `check_stop_loss()` — 修一处 bug 两边同时生效
- **风控机制**: 单只止损 -20%，每20个交易日调仓，单一行业 ≤25%，日换手率 ≤30%
- **完整交易模拟**: 佣金(0.03%)、印花税(0.1%)、滑点(0.1%)，100股整数倍，加权平均成本
- **数据质量门禁**: 数据过期/空值/异常涨跌/复权跳变四维检查
- **A股交易约束**: 涨跌停检查、T+1 检查、停牌检查
- **自动报告**: 每日生成持仓报告 + 明日操作计划 + 行业分布 + 指数趋势
- **统一回测工具**: `run_backtest.py` — 多策略对比、IC 因子分析、Markowitz 优化、参数网格扫描
- **定时执行**: 每工作日 18:00 自动运行

## 策略表现（沪深300成分股回测 2021-01 ~ 2026-05）

> 基于 core/ 统一引擎（29因子 + FACTOR_WEIGHTS 加权）

| 指标 | v3_optimized | v3_baseline | markowitz |
|------|-------------|-------------|-----------|
| 年化收益率 | 20.72% | 4.19% | 5.20% |
| 夏普比率 | 0.97 | 0.58 | 0.50 |
| 最大回撤 | -27.01% | -12.74% | -20.13% |
| 持仓数 | 12只 | 20只 | 10只 |
| 调仓频率 | 20天 | 5天 | 20天 |
| 波动率缩放 | ✅ | ❌ | ❌ |

最优参数: `top_n=12, rebalance_freq=20, stop_loss=0.20, vol_scaling=True, industry_cap=25%, turnover_limit=0`

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
# 需要: pandas, numpy, pyyaml, scipy
```

### 初始化并运行

```bash
# 1. 首次运行：初始化日K线数据（需要网络访问 gtimg.cn）
python scripts/update_daily_data.py

# 2. 运行模拟盘（使用 v6 core-based 版本）
python scripts/sim_daily_v6.py

# 3. 运行回测
python scripts/run_backtest.py --ic-analysis
```

### 配置驱动使用

**策略参数** — 编辑 `config.yaml`：

```yaml
# 因子权重（正 = 正向期望，负 = 反向）
factor_weights:
  mom_20: 0.10     # 可调
  vol_20: -0.05    # 可调

# 风控参数 (也可以在 scripts 中通过命令行覆盖)
risk:
  stop_loss: 0.20
  top_n: 10
  rebalance_freq: 20
```

优先级: `命令行参数 > config.yaml > 内置默认值`

### 回测命令速查

```bash
python scripts/run_backtest.py --ic-analysis           # 全策略 + IC 分析
python scripts/run_backtest.py --strategy v3_baseline  # 仅指定策略
python scripts/run_backtest.py --scan                  # 参数网格扫描
python scripts/run_backtest.py --report-markdown > report.md  # Markdown 报告
python scripts/run_backtest.py --config my_config.yaml # 自定义配置
```

### 测试

```bash
python scripts/tests/test_backtest_smoke.py       # 冒烟测试 (~20s)
python scripts/tests/test_backtest_edge_cases.py  # 边界测试
```

### 配置定时任务

```bash
# 每工作日 18:00 自动执行
crontab -e
# 添加: 0 18 * * 1-5 cd /path/to/project && python scripts/sim_daily_v6.py
```

## 项目结构

```
a-share-quant-sim/
├── core/                           # ⭐ 共享引擎 (回测 + 模拟盘共用)
│   ├── __init__.py                 # 统一导出
│   ├── config.py                   # Config dataclass + config.yaml loader
│   ├── position.py                 # Position 领域模型 (替代裸 dict)
│   ├── factors.py                  # 因子计算 (单股模式 + 面板模式)
│   ├── account.py                  # PortfolioState + buy/sell/check_stop_loss
│   └── scoring.py                  # Z-score 标准化 + 复合评分
├── scripts/
│   ├── sim_daily_v6.py             # ⭐ 每日模拟盘 (v6, core-based)
│   ├── run_backtest.py             # 统一回测引擎 (delegates to core/)
│   ├── update_daily_data.py        # 数据更新: 腾讯 API → 本地 CSV
│   ├── constraints.py              # P0-1: A股交易约束
│   ├── data_quality.py             # P0-2: 数据质量门禁
│   ├── portfolio_controls.py       # P0-3: 换手率上限
│   ├── industry.py                 # P1-1: 行业分类 + 仓位上限
│   ├── indices.py                  # P1-2: 指数趋势
│   ├── hs300_constituents.csv      # 沪深300成分股
│   ├── tests/                      # 回测测试套件
│   │   ├── test_backtest_smoke.py
│   │   └── test_backtest_edge_cases.py
│   └── archive/                    # 废弃旧脚本（参考用）
├── config.yaml                     # ⭐ 所有可调参数
├── data/
│   ├── daily/                      # 日K线数据 (~280 CSV)
│   │   ├── 000001.csv
│   │   └── ...
│   ├── portfolio/                  # 账户状态
│   │   ├── account.json
│   │   ├── trade_count.txt
│   │   └── daily_YYYYMMDD.json     # 每日报告
│   └── signals/                    # 因子信号缓存
├── docs/
│   ├── architecture.md             # 架构详解
│   ├── backtest-readme.md          # 回测文档
│   └── research-report.md          # 调研报告
├── references/
│   └── api-notes.md                # API 接口笔记
├── requirements.txt
├── LICENSE
└── README.md
```

## 数据格式

### 日K线 CSV (`data/daily/{code}.csv`)

```csv
date,open,high,low,close,volume,amount,outstanding_share,turnover
2026-01-04,10.50,10.80,10.30,10.65,1234567,1.31e+09,,
```

### 账户状态 (`data/portfolio/account.json`)

```json
{
  "cash": 487033.86,
  "initial_capital": 1000000,
  "holdings": {
    "603986": {"shares": 100, "cost_price": 514.18, "entry_date": "2026-05-27"}
  },
  "trade_log": [],
  "nav_history": []
}
```

## 因子列表（29个，权重见 `config.yaml` → `factor_weights`）

| 类别 | 因子 | 说明 |
|------|------|------|
| 动量 | mom_5, mom_10, mom_20, mom_60, mom_120 | 今价/N日前价的涨幅 |
| 反转 | rev_3, rev_5, rev_10 | 动量的反义词（超跌反弹信号） |
| 波动率 | vol_10, vol_20, vol_60, vol_change | 收益率标准差及变化率 |
| 成交量 | vol_ratio_5, vol_ratio_20, amount_ratio | 今日量/均量 |
| RSI | rsi_6, rsi_14, rsi_28 | 相对强弱指标 |
| 趋势 | macd_12_26, macd_5_35, boll_pos_10, boll_pos_20, boll_width_20 | MACD + 布林带 |
| 统计 | skew_20, kurt_20, atr_14, vwap_mom | 偏度、峰度、ATR、VWAP 动量 |
| 相对强度 | rel_strength_20, rel_strength_60 | 相对横截面均值 |

## 自定义

**不需要修改代码**。编辑 `config.yaml` 中的：

- `factor_weights:` — 调整因子权重
- `risk:` — 止损、调仓频率、持仓数
- `costs:` — 佣金、印花税、滑点率
- `strategies:` — 添加/修改策略预设

## 分支策略

```
dev/default      开发分支 — 日常开发、测试在这里进行
release/default  发布分支 — 稳定版本，每日 cron job 从这里拉取脚本执行

开功能:  dev/default → git checkout -b feature/xxx → 开发完后 merge 进 dev/default
发版:    dev/default 测试通过后 → git push origin release/default
回退:    直接 reset release/default 到上一个稳定 commit
```

## 每日报告示例

```
======================================================================
v6 模拟交易 - 2026-05-28 18:00
======================================================================

📥 更新行情数据...
  ✅ 数据更新完成

  ============================账户状态============================
  现金:       ¥   487,034
  持仓市值:   ¥   510,740
  总净值:     ¥   997,774
  总收益率:       -0.22%
  持仓数量:   7 只
  已交易次数: 14
  调仓计数:   19/20

  ⚠️  止损风险预警:
    ✅ 所有持仓安全，无止损风险

  📊 收盘报告
  日期:       2026-05-28
  总净值:     ¥997,774
  今日收益:   -0.16%
  总收益率:   -0.22%
  持仓数量:   7 只
  现金占比:   48.8%

  📋 行业分布
  电子         35.2% ████████████████
  半导体       28.1% █████████████
======================================================================
```

## 注意事项

- **仅供学习研究，不构成投资建议**
- 模拟交易，不涉及真实资金
- 数据源为腾讯行情接口，可能存在延迟
- 因子策略基于历史数据回测，不代表未来收益

## License

MIT License — 详见 [LICENSE](LICENSE)
