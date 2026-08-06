---
name: wf-param-sweep-v2
description: WF风控参数扫描标准流程V2。Use when optimizing stop_loss or take_profit.
---

# WF参数扫描标准流程V2

## 核心原则
1. **先单参数扫趋势，再组合精扫**（不要上来就全网格）
2. **输出必须存文件**（/tmp/xxx_sweep.txt + /tmp/xxx_sweep_debug.log）
3. **抑制DEBUG输出到单独文件**（stderr重定向到log，结果写txt）
4. **不要杀正在跑的进程**（除非用户明确要求）
5. **全量回测比WF快**——参数扫描用full=True，WF只做最终验证

## 流程

### 第0步：选择回测模式
- **参数扫描用全量回测（full=True）**，不用WF——全量回测单组约9分钟，WF单组约10-12分钟且需要训练窗口
- WF只在最终验证最优参数组合时使用
- 全量回测返回DataFrame（不是dict），取值用 `result.iloc[0]['test_sharpe']` 等

### 第1步：单参数粗扫（12组，全量回测约2小时）
- 止损：-5%/-8%/-10%/-12%（固定TP=0.30, HD=15）
- 止盈：15%/25%/30%/40%（固定SL=-0.08, HD=15）
- 持仓天数：5/10/15/20（固定SL=-0.08, TP=0.30）
- 每轮找最优值，下一轮固定已优参数

### 第2步：组合精扫（3-5组，~45分钟）
- 用单参数最优值 ± 一档做组合
- 3-5组验证即可

### 第3步：WF验证最优组合（1组，~12分钟）
- 用最优参数跑一次标准WF确认通过

### 第4步：记录结果
- 结果写入 docs/experiments/YYYY-MM-DD_<topic>_results.md
- 同步更新 RESULTS_LOG.md

## 脚本模板（全量回测版）
```python
#!/usr/bin/env python3
"""参数扫描（全量回测版）"""
import sys, warnings, io, contextlib, time
warnings.filterwarnings('ignore')
sys.path.insert(0, '/root/a-share-quant-sim')

from scripts.backtest.wf_runner import run_wf
from scripts.backtest.strategy_adapter import get_adapter

OUT = '/tmp/sweep_results.txt'
LOG = '/tmp/sweep_debug.log'

BASE = {"STOP_LOSS": -0.08, "TAKE_PROFIT": 0.30, "HOLD_DAYS_MAX": 15}

def run_one(name, overrides, strategy="v75f", base=BASE):
    params = dict(base)
    params.update(overrides)
    adapter = get_adapter()
    adapter._risk_params[strategy].update(params)
    with open(LOG, 'a') as logf, contextlib.redirect_stderr(logf):
        t0 = time.time()
        result = run_wf(strategy, full=True)  # 全量回测，不是WF
    elapsed = time.time() - t0
    # 全量回测返回DataFrame，不是dict
    r = result.iloc[0] if hasattr(result, 'iloc') else result
    return {
        "name": name, "params": params,
        "sharpe": r.get("test_sharpe", 0),
        "return": r.get("test_ret", 0),
        "max_dd": r.get("test_dd", 0),
        "elapsed": round(elapsed),
    }

# 扫描网格
results = []
for sl in [-0.05, -0.08, -0.10, -0.12]:
    r = run_one(f"SL={sl}", {"STOP_LOSS": sl})
    results.append(r)
    with open(OUT, 'a') as f:
        f.write(f"{r['name']}: Sharpe={r['sharpe']:.3f} Return={r['return']*100:.1f}% DD={r['max_dd']*100:.1f}% ({r['elapsed']}s)\n")
```

## 已知陷阱
- `run_wf(full=True)`返回DataFrame不是dict——用`result.iloc[0]['test_sharpe']`取值
- `run_wf()`不支持`params_override`参数，必须通过adapter._risk_params直接修改
- 全量回测单组约9分钟（比WF快一点点，但不需要训练窗口更稳定）
- 绝对不要用StringIO抑制所有输出——必须存文件
- 不要杀正在跑的进程——等它跑完或并行跑新的