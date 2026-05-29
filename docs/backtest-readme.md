# A股量化回测系统 (Backtest Engine)

## 概述

独立的回测工具，用于 A 股量化策略的历史数据验证。

**与模拟盘的关系**：`run_backtest.py` 和 `sim_daily_v6.py` 共用 `core/account.py` 中的 `buy()` / `sell()` / `check_stop_loss()` 交易函数，保证回测行为与模拟盘 100% 一致。一个 bug fix 两边同时生效。

## 快速开始

```bash
# 完整回测（全部策略 + IC 分析）
python scripts/run_backtest.py --ic-analysis

# 仅跑 v3 baseline
python scripts/run_backtest.py --strategy v3_baseline

# IC 分析 + 参数扫描
python scripts/run_backtest.py --ic-analysis --scan

# 指定参数回测
python scripts/run_backtest.py --strategy v3_baseline --top-n 15 --rebalance-freq 10 --stop-loss 0.20

# 指定回测区间
python scripts/run_backtest.py --start 2023-01-01 --end 2024-12-31

# 自定义配置
python scripts/run_backtest.py --config my_config.yaml

# 输出 Markdown 报告
python scripts/run_backtest.py --ic-analysis --report-markdown > report.md
```

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--strategy` | `all` | 策略列表：`v3_baseline`, `v3_optimized`, `ic_ir_weighted`, `ic_selected`, `markowitz` |
| `--start` | `2021-01-01` | 回测起始日期 |
| `--end` | 今天 | 回测结束日期 |
| `--top-n` | (策略预设) | 持仓数量 |
| `--rebalance-freq` | (策略预设) | 调仓频率（交易日） |
| `--stop-loss` | (策略预设) | 止损比例 |
| `--max-position` | `0.10` | 单只最大仓位 |
| `--scan` | off | 启用参数网格扫描 |
| `--ic-analysis` | off | 输出 IC 因子分析 |
| `--config` | `config.yaml` | 配置文件路径 |
| `--report-markdown` | off | 输出 Markdown 报告到 stdout |

## 策略说明

| 策略 | 权重方法 | 核心差异 |
|------|---------|---------|
| v3_baseline | weighted | FACTOR_WEIGHTS 加权，top_n=20, freq=5, sl=15% |
| v3_optimized | weighted | FACTOR_WEIGHTS 加权 + vol_scaling，top_n=12, freq=20, sl=20% |
| ic_ir_weighted | ic_ir | 按 IC-IR 绝对值分配因子权重 |
| ic_selected | ic_ir | IC-IR 加权 + 淘汰无效因子（\|IC_IR\|<0.03） |
| markowitz | equal+opt | 等权因子评分 + Markowitz 均值-方差优化 |

## 测试逻辑

### 1. 回测引擎正确性测试

```bash
# 测试1：净值守恒（无交易时不变）
python -c "
import pandas as pd, numpy as np
from core.factors import calc_factors_panel
from core.scoring import composite_score_equal
from scripts.run_backtest import run_backtest
close = pd.DataFrame({'TEST': [100.0]*200}, index=pd.date_range('2023-01-01', periods=200))
factors = calc_factors_panel(close, close*0+1e6, close*1e6)
score = composite_score_equal(factors)
m, nav, trades = run_backtest(close, score, top_n=1, rebalance_freq=999)
assert m['total_cost'] == 0, '无交易时不应有成本'
print('PASS: 净值守恒')
"

# 测试2：止损触发
python -c "
import pandas as pd, numpy as np
from core.factors import calc_factors_panel
from core.scoring import composite_score_equal
from scripts.run_backtest import run_backtest
dates = pd.date_range('2023-01-01', periods=200)
prices = [100.0 - i*0.3 for i in range(200)]
close = pd.DataFrame({'DROP': prices}, index=dates)
factors = calc_factors_panel(close, close*0+1e6, close*1e6)
score = composite_score_equal(factors)
m, nav, trades = run_backtest(close, score, top_n=1, rebalance_freq=5, stop_loss=0.15)
sl = [t for t in trades.to_dict('records') if t['action'] == 'STOP_LOSS']
assert len(sl) > 0, '应触发止损'
print(f'PASS: 止损触发 {len(sl)} 次')
"

# 测试3：交易成本扣除
python -c "
import pandas as pd, numpy as np, math
from core.factors import calc_factors_panel
from core.scoring import composite_score_equal
from scripts.run_backtest import run_backtest
dates = pd.date_range('2023-01-01', periods=200)
prices = [100 + 5*math.sin(i/10) for i in range(200)]
close = pd.DataFrame({'WAVE': prices}, index=dates)
factors = calc_factors_panel(close, close*0+1e6, close*1e6)
score = composite_score_equal(factors)
m, nav, trades = run_backtest(close, score, top_n=1, rebalance_freq=5)
assert m['total_cost'] > 0, '有交易时应有成本'
print(f'PASS: 成本扣除 总成本=¥{m[\"total_cost\"]:.2f}')
"
```

### 2. 与模拟盘一致性验证

```python
from core.account import PortfolioState, portfolio_value
from scripts.run_backtest import run_backtest

# 回测 2024 全年
metrics, nav, trades = run_backtest(close_panel, score_equal,
    top_n=10, rebalance_freq=20, stop_loss=0.20, label='consistency_check')

# 读模拟盘 2024 年末净值
import json
with open('data/portfolio/account.json') as f:
    acct = json.load(f)

# 回测净值应接近模拟盘（允许交易成本差异）
diff_pct = abs(nav.iloc[-1] - sim_nav) / sim_nav * 100
assert diff_pct < 1.0, f'差异 {diff_pct:.2f}% 过大'
print(f'PASS: 回测={nav.iloc[-1]:,.0f}, 模拟盘={sim_nav:,.0f}, 差异={diff_pct:.2f}%')
```

### 3. 边界条件测试

| 场景 | 预期行为 |
|------|---------|
| 数据不足 120 日 | 跳过，不交易 |
| 全部股票停牌 | 保持空仓，净值=现金 |
| 调仓日无有效评分 | 维持现有持仓 |
| 现金不足买 1 手 | 跳过买入 |
| 首日即调仓日 (i=120) | 正常建仓 |

### 4. 运行测试套件

```bash
python scripts/tests/test_backtest_smoke.py       # 6 个冒烟测试 (~20s)
python scripts/tests/test_backtest_edge_cases.py  # 6 个边界测试
```

### 5. 输出位置

```
data/backtest_results/YYYYMMDD_HHMMSS/
├── summary.json          # 全部策略绩效指标
├── comparison.csv        # 策略对比表
├── nav_v3_baseline.csv   # 各策略净值曲线
├── trades_v3_baseline.csv # 交易记录 (core.account 格式)
├── param_scan.json       # 参数扫描 Top 20
├── param_scan.csv
└── report.md             # Markdown 回测报告
```

## 与 core/ 的关系

`run_backtest.py` 完全委托 `core/`：

| 调用 | 来自 core |
|------|----------|
| `calc_factors_panel(close, volume, amount)` | `factors.py` — 29 因子面板计算 |
| `composite_score(factors)` | `scoring.py` — FACTOR_WEIGHTS 加权评分 |
| `composite_score_equal(factors)` | `scoring.py` — 等权评分 |
| `PortfolioState()` | `account.py` — 账户状态 |
| `buy/sell/check_stop_loss(state, ...)` | `account.py` — 纯函数式交易 |
| `portfolio_value(state, ...)` | `account.py` — 净值计算 |
| `config.factor_weights` | `config.py` — 29 因子权重 |
| `config.costs` / `config.risk` | `config.py` — 成本/风控参数 |

**零本地重复逻辑** — 所有交易细节（滑点、佣金、100股整数倍、加权平均成本、现金检查）全部由 `core/account.py` 处理。
