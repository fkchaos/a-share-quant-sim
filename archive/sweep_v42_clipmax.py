#!/usr/bin/env python3
"""
scripts/backtest/sweep_v42_clipmax.py — v42 clip_max 扫描
====================================================
"""
import os, sys, re, time, subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WF_RUNNER = os.path.join(PROJECT_ROOT, "scripts", "backtest", "wf_runner.py")
STRATEGY_PATH = os.path.join(PROJECT_ROOT, "scripts", "strategies", "v42_turnover_research.py")

def run_wf(strategy, start='2021-01-01', end='2026-06-24'):
    """运行 wf_runner (WF 多fold 模式) 并解析结果"""
    cmd = f'python3 {WF_RUNNER} --strategy {strategy} --train 252 --test 126 --step 63 --start {start} --end {end}'
    t0 = time.time()
    output = subprocess.check_output(cmd, shell=True, text=True)
    elapsed = time.time() - t0
    
    result = {"time": elapsed}
    m = re.search(r'测试期平均收益率:\s+([\d.]+%)', output)
    result["return"] = float(m.group(1).replace('%', '')) if m else 0
    m = re.search(r'测试期平均夏普:\s+([\d.]+)', output)
    result["sharpe"] = float(m.group(1)) if m else 0
    m = re.search(r'测试期平均回撤:\s+([\d.]+%)', output)
    result["drawdown"] = float(m.group(1).replace('%', '')) if m else 0
    m = re.search(r'正收益 fold:\s+(\d+)/(\d+)', output)
    if m:
        result["positive_folds"] = int(m.group(1))
        result["total_folds"] = int(m.group(2))
    
    return result

def set_clip_max(value):
    """修改 v42 策略中的 turnover_rate clip_max"""
    with open(STRATEGY_PATH, 'r') as f:
        lines = f.readlines()
    
    new_lines = []
    for line in lines:
        if "turnover_rate" in line and "_score_column" in line and "clip_max" in line:
            # Replace the clip_max value in this specific line
            line = re.sub(r'clip_max=[\d.]+', f'clip_max={value}', line)
        new_lines.append(line)
    
    with open(STRATEGY_PATH, 'w') as f:
        f.writelines(new_lines)
    
    # 验证
    with open(STRATEGY_PATH, 'r') as f:
        content = f.read()
    for line in content.split('\n'):
        if 'turnover_rate' in line and '_score_column' in line:
            print(f"  当前行: {line.strip()}")

# ── 扫描不同 clip_max ──
print("=" * 60)
print("v42 clip_max 扫描 (W_MOM=0.08, W_TR=0.12)")
print("=" * 60)

clip_values = [0.05, 0.10, 0.15, 0.20, 0.30]
results = []

for clip in clip_values:
    print(f"\n[clip_max={clip}]")
    set_clip_max(clip)
    
    print(f"  运行 WF 回测...")
    r = run_wf("v42")
    r["clip_max"] = clip
    results.append(r)
    print(f"  收益={r['return']:+.2f}%, 夏普={r['sharpe']:.3f}, 回撤={r['drawdown']:.2f}%, "
          f"正收益fold={r.get('positive_folds','?')}/{r.get('total_folds','?')}, 耗时={r['time']:.0f}s")

print("\n" + "=" * 60)
print("汇总")
print("=" * 60)
print(f"{'clip_max':>10} {'收益':>8} {'夏普':>8} {'回撤':>8} {'正fold':>8} {'耗时':>6}")
print("-" * 55)
for r in sorted(results, key=lambda x: x['sharpe'], reverse=True):
    print(f"{r['clip_max']:>10.2f} {r['return']:>+7.2f}% {r['sharpe']:>7.3f} {r['drawdown']:>7.2f}% "
          f"{r.get('positive_folds','?'):>3}/{r.get('total_folds','?'):<3} {r['time']:>5.0f}s")
