---
name: wf-param-sweep
description: WF风控参数扫描标准流程。Use when optimizing stop_loss or take_profit.
---

# WF参数扫描标准流程

## 核心原则
1. **先单参数扫趋势，再组合精扫**（不要上来就全网格）
2. **输出必须存文件**（/tmp/v75f_sweep.log + /tmp/v75f_sweep.txt）
3. **抑制DEBUG输出到单独文件**（stdout重定向到log，结果写txt）
4. **不要杀正在跑的进程**（除非用户明确要求）

## 流程

### 第1步：单参数粗扫（9组，~2小时）
- 止损：-5%/-8%/-10%/-12%（固定TP=0.30, HD=15）
- 止盈：15%/25%/30%/40%（固定SL=-0.08, HD=15）
- 持仓天数：5/10/15/20（固定SL=-0.08, TP=0.30）
- 每轮找最优值，下一轮固定已优参数

### 第2步：组合精扫（3-5组，~1小时）
- 用单参数最优值 ± 一档做组合
- 3-5组验证即可

### 第3步：记录结果
- 结果写入 docs/experiments/YYYY-MM-DD_<topic>_results.md
- 同步更新 RESULTS_LOG.md

## 脚本模板
```python
import sys, warnings, io, contextlib, time
warnings.filterwarnings('ignore')
sys.path.insert(0, '/root/a-share-quant-sim')

from scripts.backtest.wf_runner import run_wf
from scripts.backtest.strategy_adapter import get_adapter

OUT = '/tmp/v75f_sweep.txt'
LOG = '/tmp/v75f_sweep_debug.log'

BASE = {"STOP_LOSS": -0.08, "TAKE_PROFIT": 0.30, "HOLD_DAYS_MAX": 15}

def run_one(name, overrides, base=BASE):
    params = dict(base)
    params.update(overrides)
    adapter = get_adapter()
    adapter._risk_params["v75f"].update(params)
    with open(LOG, 'a') as logf, contextlib.redirect_stdout(logf), contextlib.redirect_stderr(logf):
        result = run_wf("v75f", 252, 126, 63)
    return {
        "name": name, "params": params,
        "sharpe": result.get("sharpe", 0),
        "avg_return": result.get("avg_return", 0),
        "positive_pct": result.get("positive_pct", 0),
        "max_dd": result.get("max_dd", 0),
        "passed": result.get("passed", False),
    }
```

## 已知陷阱
- `run_wf()`不支持`params_override`参数，必须通过adapter._risk_params直接修改
- 每组WF约10-12分钟（15 folds × 40s/fold）
- 27组全网格约5-6小时，9组单参数约2小时
- 绝对不要用StringIO抑制所有输出——必须存文件
