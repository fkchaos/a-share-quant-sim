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
3. **输出必须存文件**（/tmp/vXX_sweep.txt）
4. **每轮固定前一轮最优值**
5. **⚠️ 抑制WF的DEBUG输出到/dev/null，不是文件！**（见下方说明）
6. **不要杀正在跑的进程**（除非用户明确要求）
7. **断电续跑**（见下方流程）

---

## 断电续跑流程（每次扫描必实现）

参数扫描每组可能跑30秒~50分钟，8组总计1~2小时。断电/进程被杀后必须能从断点继续，不能从头跑。

### 实现模式：JSONL逐行追加 + 启动时去重

**文件格式**：结果文件用JSONL（每行一个JSON对象），`append`模式写入。

**启动时**：
```python
def load_done():
    """读取已有结果，按name去重"""
    done = {}
    if os.path.exists(OUT):
        with open(OUT, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('{'):
                    try:
                        r = json.loads(line)
                        done[r['name']] = r
                    except: pass
    return done

done = load_done()  # {name: result_dict}
```

**主循环**：
```python
for i, (name, *weights) in enumerate(grid):
    if name in done:
        print(f"[{i+1}] {name} — 已完成，跳过 (Sharpe={done[name].get('sharpe','?')})")
        continue
    # ... 执行扫描 ...
    r = run_one(name, *weights)
    # 立即写入
    with open(OUT, 'a') as f:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')
```

### 关键点
- **每组完成立即`append`写入**，不是最后一次性写入
- **用`name`字段去重**，不依赖文件顺序或索引
- **重启时自动跳过已完成组**，不改任何代码
- **错误组也写入**（带`error`字段），避免反复重试已知失败项

### 结果文件格式
```
{"name":"流动性主导","sharpe":1.6523,"return_pct":142.35,"max_dd_pct":38.21,"elapsed_s":28.3}
{"name":"均衡偏量","sharpe":1.7102,"return_pct":155.80,"max_dd_pct":35.67,"elapsed_s":31.1}
{"name":"突破主导","error":"run_wf returned None"}
```

---

## DEBUG输出抑制（铁律，违反必卡）

WF runner内部会输出大量`RCV DBG risk_check`等调试信息（每组8000+行）。
**重定向到log文件≠抑制**——写8000行到文件同样I/O阻塞，每组从30秒膨胀到50分钟。

**正确做法**：
```python
# ❌ 错误：重定向到文件（I/O阻塞）
with open(LOG, 'a') as logf, contextlib.redirect_stdout(logf):
    result = run_wf(...)

# ✅ 正确：重定向到/dev/null（完全抑制I/O）
with open(os.devnull, 'w') as devnull:
    old_out, old_err = sys.stdout, sys.stderr
    sys.stdout = devnull
    sys.stderr = devnull
    try:
        result = run_wf(...)
    finally:
        sys.stdout, sys.stderr = old_out, old_err

# ✅ 正确：只打印扫描结果，不打印WF内部输出
print(f"  Sharpe={result['sharpe']:.4f}")  # 这行正常输出
```

**原理**：扫描脚本自己的进度信息正常打印，WF runner内部的DEBUG输出彻底静默。两者分离。

## 脚本模板

```python
import sys, os, time, json, warnings, logging
warnings.filterwarnings('ignore')
logging.disable(logging.CRITICAL)  # 抑制logging输出
os.environ['PYTHONWARNINGS'] = 'ignore'
sys.path.insert(0, '/root/a-share-quant-sim')

from scripts.backtest.wf_runner import run_wf

OUT = '/tmp/vXX_sweep.txt'

# ── 断电续跑：读取已完成结果 ──
def load_done():
    done = {}
    if os.path.exists(OUT):
        with open(OUT, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('{'):
                    try:
                        r = json.loads(line)
                        done[r['name']] = r
                    except: pass
    return done

def run_one(name, overrides, base):
    """修改参数，跑全量回测，返回结果"""
    # ... 修改 adapter._risk_params 或 DEFAULT_PARAMS ...
    
    try:
        t0 = time.time()
        # ⚠️ 关键：抑制WF的DEBUG输出到/dev/null
        with open(os.devnull, 'w') as devnull:
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout = devnull
            sys.stderr = devnull
            try:
                result = run_wf("vXX", full=True)
            finally:
                sys.stdout, sys.stderr = old_out, old_err
        elapsed = time.time() - t0

        if result is not None and hasattr(result, 'iloc'):
            r = {
                "name": name, "params": overrides,
                "sharpe": round(float(result['test_sharpe'].iloc[0]), 4),
                "return_pct": round(float(result['test_ret'].iloc[0]) * 100, 2),
                "max_dd_pct": round(float(result['test_dd'].iloc[0]) * 100, 2),
                "elapsed_s": round(elapsed, 1),
            }
        else:
            r = {"name": name, "error": "run_wf returned None"}
    finally:
        # ... 恢复参数 ...
        pass
    
    # ⚠️ 立即追加写入（断电不丢）
    with open(OUT, 'a') as f:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')
    
    # 打印结果（正常输出，不受devnull影响）
    if 'error' in r:
        print(f"  ❌ {r['error']}")
    else:
        print(f"  Sharpe={r['sharpe']:.4f}  return={r['return_pct']:.2f}%  dd={r['max_dd_pct']:.2f}%")
    return r

# ── 主循环（断电续跑） ──
def main():
    done = load_done()
    total = len(GRID)
    for i, (name, *args) in enumerate(GRID):
        if name in done:
            print(f"[{i+1}/{total}] {name} — 已完成，跳过 (Sharpe={done[name].get('sharpe','?')})")
            continue
        print(f"[{i+1}/{total}] {name}")
        run_one(name, *args)
```

## 已知陷阱

- `run_wf()`不支持`params_override`参数，必须通过adapter._risk_params直接修改
- 每组WF约10-12分钟（15 folds × 40s/fold）
- 27组全网格约5-6小时，9组单参数约2小时
- 绝对不要用StringIO抑制所有输出——必须存文件
- 因子权重必须归一化（和=1.0），否则改变权重总和会影响选股结果
- 权重扫描不要跳步：扫完A因子最优值后，固定A再扫B
- 因子权重影响calc_factors返回值，不能只改DEFAULT_PARAMS——需要让calc_factors读取动态权重
