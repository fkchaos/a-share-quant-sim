#!/usr/bin/env python3
"""v44 参数扫描——子进程方式，通过临时修改 strategy_map.py"""
import subprocess, time, json, re, os

os.chdir('/root/a-share-quant-sim')

# 读取原始文件
with open('core/strategy_map.py', 'r') as f:
    original_content = f.read()

WEIGHT_CONFIGS = [
    ("v44_base",   0.35, 0.25, 0.15, 0.10, 0.05, 0.10),
    ("v44_flow45", 0.45, 0.20, 0.10, 0.10, 0.05, 0.10),
    ("v44_flow50", 0.50, 0.15, 0.10, 0.10, 0.05, 0.10),
    ("v44_mom35",  0.25, 0.35, 0.15, 0.10, 0.05, 0.10),
    ("v44_mom40",  0.20, 0.40, 0.15, 0.10, 0.05, 0.10),
    ("v44_bal",    0.30, 0.30, 0.15, 0.10, 0.05, 0.10),
    ("v44_size25", 0.30, 0.25, 0.25, 0.10, 0.05, 0.10),
    ("v44_tr20",   0.30, 0.25, 0.15, 0.20, 0.05, 0.05),
    ("v44_gap0",   0.35, 0.25, 0.15, 0.10, 0.00, 0.15),
]

def set_v44_weights(wf, wm, ws, wt, wg, wi):
    """直接修改 strategy_map.py 中的 v44 权重行"""
    with open('core/strategy_map.py', 'r') as f:
        content = f.read()
    
    # 用精确替换——只替换 v44 块中的值
    # 策略：找到 '"v44": {' 位置，然后替换其后的权重行
    lines = content.split('\n')
    in_v44 = False
    new_lines = []
    for line in lines:
        if '"v44":' in line:
            in_v44 = True
        elif in_v44 and line.strip().startswith('},') and not any(x in line for x in ['STOP', 'HOLD', 'MAX']):
            in_v44 = False
        
        if in_v44:
            if '"W_FLOW":' in line:
                line = re.sub(r': [\d.]+', f': {wf}', line)
            elif '"W_MOM":' in line:
                line = re.sub(r': [\d.]+', f': {wm}', line)
            elif '"W_SIZE":' in line:
                line = re.sub(r': [\d.]+', f': {ws}', line)
            elif '"W_TURNOVER":' in line:
                line = re.sub(r': [\d.]+', f': {wt}', line)
            elif '"W_GAP":' in line:
                line = re.sub(r': [\d.]+', f': {wg}', line)
            elif '"W_ILLIQ":' in line:
                line = re.sub(r': [\d.]+', f': {wi}', line)
        
        new_lines.append(line)
    
    with open('core/strategy_map.py', 'w') as f:
        f.write('\n'.join(new_lines))

def restore():
    with open('core/strategy_map.py', 'w') as f:
        f.write(original_content)

def run_wf():
    """跑 WF 并解析结果"""
    result = subprocess.run(
        ['python', 'scripts/backtest/wf_runner.py',
         '--strategy', 'v44', '--train', '252', '--test', '126', '--step', '126',
         '--start', '20230101', '--end', '20260601'],
        capture_output=True, text=True, timeout=180
    )
    output = result.stdout + result.stderr
    
    ret_m = re.search(r'平均收益率:\s+([\d.]+%)', output)
    sharpe_m = re.search(r'平均夏普:\s+([\d.]+)', output)
    dd_m = re.search(r'平均回撤:\s+([\d.]+%)', output)
    fold_m = re.search(r'正收益 fold:\s+(\d+)/(\d+)', output)
    passed = '✅' if 'WF 通过' in output else '❌'
    
    return {
        'return': ret_m.group(1) if ret_m else '?',
        'return_v': float(ret_m.group(1).rstrip('%')) if ret_m else 0,
        'sharpe': float(sharpe_m.group(1)) if sharpe_m else 0,
        'dd': dd_m.group(1) if dd_m else '?',
        'dd_v': float(dd_m.group(1).rstrip('%')) if dd_m else 0,
        'folds': f"{fold_m.group(1)}/{fold_m.group(2)}" if fold_m else '?',
        'passed': passed,
    }

results = []

print(f"{'name':15s} | {'params':40s} | {'ret':>8s} | {'sharpe':>7s} | {'dd':>8s} | {'folds':>5s} | {'pass':>4s}")
print("-" * 100)

for name, wf, wm, ws, wt, wg, wi in WEIGHT_CONFIGS:
    set_v44_weights(wf, wm, ws, wt, wg, wi)
    t0 = time.time()
    m = run_wf()
    elapsed = time.time() - t0
    params_str = f"F={wf} M={wm} S={ws} T={wt} G={wg} I={wi}"
    print(f"{name:15s} | {params_str:40s} | {m['return']:>8s} | {m['sharpe']:7.3f} | {m['dd']:>8s} | {m['folds']:>5s} | {m['passed']:>4s} ({elapsed:.0f}s)")
    results.append({'name': name, 'type': 'weight', 'params': params_str, **m})

restore()

# 汇总
print("\n" + "="*100)
print("=== TOP 5（按夏普排序）===")
sorted_r = sorted([r for r in results if isinstance(r.get('sharpe'), (int, float))],
                  key=lambda x: x['sharpe'], reverse=True)
for i, r in enumerate(sorted_r[:5]):
    print(f"  {i+1}. {r['name']:15s} | {r['params']:40s} | ret={r['return']} | sharpe={r['sharpe']:.3f} | dd={r['dd']}")

with open('data/v44_param_scan.json', 'w') as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print(f"\n结果已保存 → data/v44_param_scan.json")
