---
name: wf-param-sweep
description: "WF三轮参数扫描：因子权重→择时参数→风控参数。Use when optimizing factor weights, timing thresholds, or risk params."
category: data-science
---

# WF三轮参数扫描标准流程

## 扫描顺序（严格按序）

```
第1轮：因子权重扫描 → 第2轮：择时参数扫描 → 第3轮：风控参数扫描
         ↑固定前轮最优值        ↑固定前轮最优值
```

**每轮固定前一轮最优值，不要三轮一起扫（组合爆炸）。**

## 第1轮：因子权重扫描

### 时机
WF通过后第一步，先扫因子权重再扫其他。

### 方法
1. **网格粗扫**（步长0.10）：确定大致方向
2. **组合精扫**（±0.05）：微调最优值
3. 因子权重必须归一化（和=1.0），否则改变权重总和会影响选股结果

### 示例（v75a三因子）
```python
# 粗扫：6组
WEIGHT_GRID = [
    {"W_BREAKOUT": 0.30, "W_VOL_SURGE": 0.30, "W_LIQUIDITY": 0.40},  # 流动性主导
    {"W_BREAKOUT": 0.35, "W_VOL_SURGE": 0.35, "W_LIQUIDITY": 0.30},  # 均衡偏量
    {"W_BREAKOUT": 0.40, "W_VOL_SURGE": 0.30, "W_LIQUIDITY": 0.30},  # 突破主导
    {"W_BREAKOUT": 0.45, "W_VOL_SURGE": 0.30, "W_LIQUIDITY": 0.25},  # 默认（基准）
    {"W_BREAKOUT": 0.50, "W_VOL_SURGE": 0.25, "W_LIQUIDITY": 0.25},  # 突破更高
    {"W_BREAKOUT": 0.55, "W_VOL_SURGE": 0.25, "W_LIQUIDITY": 0.20},  # 突破最高
]
# 精扫：粗扫最优值±0.05，3组
```

### 关键陷阱
- `calc_factors`里用的是`DEFAULT_PARAMS`，扫描时必须通过策略函数的params参数覆盖
- 权重变化会影响rank的相对排序，结果可能非单调
- 因子间高度相关时（如突破和放量r>0.5），权重调整效果有限

---

## 第2轮：择时参数扫描

### 时机
因子权重确定后，扫择时/过滤阈值。

### 示例（v75f广度阈值）
```python
BREADTH_GRID = [
    {"BREADTH_HIGH": 0.45, "BREADTH_LOW": 0.25},  # 更宽松
    {"BREADTH_HIGH": 0.50, "BREADTH_LOW": 0.30},  # 默认
    {"BREADTH_HIGH": 0.55, "BREADTH_LOW": 0.35},  # 更严格
    {"BREADTH_HIGH": 0.60, "BREADTH_LOW": 0.30},  # 高门槛
    {"BREADTH_HIGH": 0.50, "BREADTH_LOW": 0.20},  # 低门槛兜底
]
```

---

## 第3轮：风控参数扫描

### 时机
因子权重和择时参数都确定后，最后扫风控。

### 方法
1. **单参数粗扫**（9组，~2小时）：
   - 止损：-5%/-8%/-10%/-12%
   - 止盈：15%/25%/30%/40%
   - 持仓天数：5/10/15/20
2. **组合精扫**（3-5组，~1小时）：单参数最优值±1档

---

## 通用原则

1. **先单参数扫趋势，再组合精扫**（不要上来就全网格）
2. **用全量回测（full=True）做初筛**，快20倍
3. **输出必须存文件**（/tmp/vXX_sweep.log + /tmp/vXX_sweep.txt）
4. **每轮固定前一轮最优值**
5. **抑制DEBUG输出到单独文件**（stdout重定向到log，结果写txt）
6. **不要杀正在跑的进程**（除非用户明确要求）

## 脚本模板

```python
import sys, warnings, io, contextlib, time
warnings.filterwarnings('ignore')
sys.path.insert(0, '/root/a-share-quant-sim')

from scripts.backtest.wf_runner import run_wf
from scripts.backtest.strategy_adapter import get_adapter

OUT = '/tmp/vXX_sweep.txt'
LOG = '/tmp/vXX_sweep_debug.log'

BASE = {"STOP_LOSS": -0.08, "TAKE_PROFIT": 0.30, "HOLD_DAYS_MAX": 15}

def run_one(name, overrides, base=BASE):
    params = dict(base)
    params.update(overrides)
    adapter = get_adapter()
    adapter._risk_params["vXX"].update(params)
    with open(LOG, 'a') as logf, contextlib.redirect_stdout(logf), contextlib.redirect_stderr(logf):
        result = run_wf("vXX", 252, 126, 63)
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
- 因子权重必须归一化（和=1.0），否则改变权重总和会影响选股结果
- 权重扫描不要跳步：扫完A因子最优值后，固定A再扫B
- 因子权重影响calc_factors返回值，不能只改DEFAULT_PARAMS——需要让calc_factors读取动态权重
