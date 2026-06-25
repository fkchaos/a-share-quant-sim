#!/usr/bin/env python3
"""
scripts/backtest/sweep_v42_turnover.py — v42 换手率因子参数扫描
====================================================
"""
import os, sys, re, time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WF_RUNNER = os.path.join(PROJECT_ROOT, "scripts", "backtest", "wf_runner.py")

def run_and_parse(strategy, full=True, start='2023-01-01', end='2026-06-24'):
    """运行 wf_runner 并解析结果"""
    if full:
        cmd = f'python3 {WF_RUNNER} --strategy {strategy} --full --start {start} --end {end}'
    else:
        cmd = f'python3 {WF_RUNNER} --strategy {strategy} --train 252 --test 126 --step 63 --start 2021-01-01 --end 2026-06-24'
    
    t0 = time.time()
    output = os.popen(cmd).read()
    elapsed = time.time() - t0
    
    result = {"time": elapsed}
    
    if full:
        m = re.search(r'总收益率:\s+([\d.]+%)', output)
        result["return"] = float(m.group(1).replace('%', '')) if m else 0
        m = re.search(r'夏普比率:\s+([\d.]+)', output)
        result["sharpe"] = float(m.group(1)) if m else 0
        m = re.search(r'最大回撤:\s+([\d.]+%)', output)
        result["drawdown"] = float(m.group(1).replace('%', '')) if m else 0
    else:
        m = re.search(r'测试期平均收益率:\s+([\d.]+%)', output)
        result["return"] = float(m.group(1).replace('%', '')) if m else 0
        m = re.search(r'测试期平均夏普:\s+([\d.]+)', output)
        result["sharpe"] = float(m.group(1)) if m else 0
        m = re.search(r'测试期平均回撤:\s+([\d.]+%)', output)
        result["drawdown"] = float(m.group(1).replace('%', '')) if m else 0
    
    return result


# ── 扫描不同配置 ──
print("=" * 60)
print("v42 换手率因子 — 参数扫描")
print("=" * 60)

tests = [
    # 基线
    ("v39i_基线", "v39i", False, '2021-01-01', '2026-06-24'),
    ("v42_基线_W0.05", "v42", False, '2021-01-01', '2026-06-24'),
]

results = []
for name, strategy, full, start, end in tests:
    print(f"\n[{name}] 运行中...")
    r = run_and_parse(strategy, full, start, end)
    r["name"] = name
    results.append(r)
    print(f"  收益={r['return']:+.2f}%, 夏普={r['sharpe']:.3f}, 回撤={r['drawdown']:.2f}%, 耗时={r['time']:.0f}s")

print("\n" + "=" * 60)
print("汇总")
print("=" * 60)
print(f"{'配置':<20} {'收益':>8} {'夏普':>8} {'回撤':>8} {'耗时':>6}")
print("-" * 55)
for r in results:
    print(f"{r['name']:<20} {r['return']:>+7.2f}% {r['sharpe']:>7.3f} {r['drawdown']:>7.2f}% {r['time']:>5.0f}s")
