# A股量化回测系统 (Backtest Engine)

## 概述

独立的回测工具，用于 A 股量化策略的历史数据验证。支持多策略对比、参数网格扫描、IC 因子分析。

与 `sim_daily.py`（每日模拟盘运行脚本）配合使用：回测验证通过 → 更新模拟盘脚本。

## 快速开始

```bash
# 完整回测（全部策略 + IC 分析）
python run_backtest.py --ic-analysis

# 仅跑 v3 baseline
python run_backtest.py --strategy v3_baseline

# IC 分析 + 参数扫描
python run_backtest.py --ic-analysis --scan

# 指定参数回测
python run_backtest.py --strategy v3_baseline --top-n 15 --rebalance-freq 10 --stop-loss 0.20

# 指定回测区间
python run_backtest.py --start 2023-01-01 --end 2024-12-31

# 输出 Markdown 报告
python run_backtest.py --ic-analysis --report-markdown > report.md
```

## 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--strategy` | `all` | 策略列表（空格分隔）：`v3_baseline`, `v3_optimized`, `ic_ir_weighted`, `ic_selected`, `markowitz` |
| `--start` | `2021-01-01` | 回测起始日期 |
| `--end` | 今天 | 回测结束日期 |
| `--top-n` | (策略预设) | 持仓数量 |
| `--rebalance-freq` | (策略预设) | 调仓频率（交易日） |
| `--stop-loss` | (策略预设) | 止损比例 |
| `--max-position` | `0.10` | 单只最大仓位 |
| `--scan` | off | 启用参数网格扫描 |
| `--ic-analysis` | off | 输出 IC 因子分析 |
| `--report-markdown` | off | 输出 Markdown 报告到 stdout |

## 测试逻辑

### 1. 回测引擎正确性

```bash
# 测试1：净值守恒（无交易时净值不变）
python -c "
import pandas as pd, numpy as np
from run_backtest import *
close = pd.DataFrame({'TEST': [100.0]*200}, 
                     index=pd.date_range('2023-01-01', periods=200))
vol = pd.DataFrame({'TEST': [1e6]*200}, index=close.index)
amt = pd.DataFrame({'TEST': [1e9]*200}, index=close.index)
factors = calc_factors(close, vol, amt)
score = composite_score_equal(factors)
m, nav, trades = run_backtest(close, score, top_n=1, rebalance_freq=999)
assert abs(m['total_cost']) < 1, '无交易时不应有成本'
print('✅ 净值守恒测试通过')
"

# 测试2：止损触发
python -c "
import pandas as pd, numpy as np
from run_backtest import *
dates = pd.date_range('2023-01-01', periods=200)
prices = [100.0 - i*0.3 for i in range(200)]  # 持续下跌
close = pd.DataFrame({'DROP': prices}, index=dates)
vol = pd.DataFrame({'DROP': [1e6]*200}, index=dates)
amt = pd.DataFrame({'DROP': [1e9]*200}, index=dates)
factors = calc_factors(close, vol, amt)
score = composite_score_equal(factors)
m, nav, trades = run_backtest(close, score, top_n=1, rebalance_freq=5, stop_loss=0.15)
sl_df = trades[trades['action'] == 'STOP_LOSS']
assert len(sl_df) > 0, '应触发止损'
print(f'✅ 止损测试通过 (触发{len(sl_df)}次止损)')
"

# 测试3：交易成本扣除
python -c "
import pandas as pd, numpy as np
from run_backtest import *
dates = pd.date_range('2023-01-01', periods=200)
# 震荡行情，确保有交易
import math
prices = [100 + 5*math.sin(i/10) for i in range(200)]
close = pd.DataFrame({'WAVE': prices}, index=dates)
vol = pd.DataFrame({'WAVE': [1e6]*200}, index=dates)
amt = pd.DataFrame({'WAVE': [1e9]*200}, index=dates)
factors = calc_factors(close, vol, amt)
score = composite_score_equal(factors)
m, nav, trades = run_backtest(close, score, top_n=1, rebalance_freq=5)
assert m['total_cost'] > 0, '有交易时应有成本'
print(f'✅ 成本测试通过 (总成本=¥{m[\"total_cost\"]:.2f}, {m[\"total_trades\"]}笔交易)')
"
```

### 2. 与模拟盘一致性验证

```bash
from run_backtest import *
from sim_account import SimAccount

# 回测 2024 全年，与模拟盘同期表现对比
metrics, nav, trades = run_backtest(close_panel, score_equal,
    top_n=10, rebalance_freq=20, stop_loss=0.20,
    label='rebalance_check')

# 读模拟盘 2024 年末净值
with open('data/portfolio/account.json') as f:
    acct = json.load(f)
    sim_nav = acct['nav_history'][-1]['nav']

# 回测净值应接近模拟盘（允许交易成本差异）
backtest_nav = nav.iloc[-1]
diff_pct = abs(backtest_nav - sim_nav) / sim_nav * 100
assert diff_pct < 1.0, f'差异 {diff_pct:.2f}% 过大'
print(f'✅ 一致性验证通过: 回测={backtest_nav:,.0f}, 模拟盘={sim_nav:,.0f}, 差异={diff_pct:.2f}%')
```

### 3. 边界条件测试

| 场景 | 预期行为 | 验证方式 |
|------|---------|---------|
| 数据不足 120 日 | 跳过，不交易 | `close_panel < 120 行` |
| 全部股票停牌 | 保持空仓，净值=现金 | `price_data 全 NaN` |
| 调仓日无有效评分 | 维持现有持仓 | `score 全 NaN` |
| 现金不足买 1 手 | 跳过买入 | `cash < 100 * price` |
| 首日即调仓日 (i=120) | 正常建仓 | `(120-120) % freq == 0` |

### 4. 运行正面测试

```bash
python run_backtest.py --ic-analysis
```

### 5. 边界/异常测试

```bash
tests/test_backtest_edge_cases.py
```

### 6. 脚本 smoke test

```bash
tests/test_backtest_smoke.py
```
