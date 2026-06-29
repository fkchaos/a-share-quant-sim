#!/usr/bin/env python3
"""
scripts/backtest/sweep_all_strategies_v2.py
全策略重跑 WF，每个策略结果立即写入文件，跳过全A策略。
"""
import sys
import os
import re
import time
import subprocess
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

RESULT_FILE = '/tmp/sweep_all_results.txt'


def run_one(strategy, start='2021-06-01', end='2026-06-01'):
    """跑一个策略的 WF，返回结果"""
    cmd = [sys.executable, '-c', f'''
import sys, os
sys.path.insert(0, ".")
os.environ["PYTHONUNBUFFERED"] = "1"
from scripts.backtest.strategy_adapter import get_adapter
from scripts.backtest.wf_runner import run_wf

a = get_adapter()
if "{strategy}" in a._risk_params:
    a._risk_params["{strategy}"].setdefault("MAX_DAILY_BUY", 3)
    a._risk_params["{strategy}"].setdefault("MAX_POSITION", 0.20)

df = run_wf("{strategy}", train_days=252, test_days=126, step_days=63,
            start_date="{start}", end_date="{end}")
if df is not None and len(df) > 0:
    s = df["test_sharpe"].mean()
    r = df["test_ret"].mean() * 100
    d = df["test_dd"].mean() * 100
    p = (df["test_ret"] > 0).sum()
    t = len(df)
    print(f"RESULT: sharpe={{s:.3f}}, ret={{r:.2f}}, dd={{d:.1f}}, folds={{p}}/{{t}}")
else:
    print("RESULT: NODATA")
''']

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    output = result.stdout + result.stderr
    for line in output.split('\n'):
        m = re.search(r'RESULT: sharpe=([\d.-]+), ret=([\d.-]+), dd=([\d.-]+), folds=(\d+)/(\d+)', line)
        if m:
            return {
                'sharpe': float(m.group(1)),
                'ret': float(m.group(2)),
                'dd': float(m.group(3)),
                'pos_folds': int(m.group(4)),
                'total_folds': int(m.group(5)),
            }
    return None


def save_result(strategy, r, mark):
    """立即写入结果文件"""
    with open(RESULT_FILE, 'a') as f:
        f.write(f"{strategy}\t{r['sharpe']:.3f}\t{r['ret']:.2f}\t{r['dd']:.1f}\t{r['pos_folds']}/{r['total_folds']}\t{mark}\n")
        f.flush()


def main():
    # 跳过全A策略（太慢）：v46
    strategies = [
        'v11b', 'v32', 'v33', 'v35',
        'v39c', 'v39d', 'v39e', 'v39f', 'v39g', 'v39h', 'v39i',
        'v40', 'v40b', 'v41', 'v42', 'v44',
    ]

    # 清空结果文件
    with open(RESULT_FILE, 'w') as f:
        f.write("strategy\tsharpe\tret\tdd\tfolds\tmark\n")

    print("=" * 70)
    print("全策略 WF 重跑（amount 修复后，统一条件）")
    print("区间: 2021-06-01 ~ 2026-06-01, train=252, test=126, step=63")
    print(f"跳过: v46（全A太慢）")
    print("=" * 70)

    t0 = time.time()
    results = []

    for i, strat in enumerate(strategies):
        print(f"\n[{i+1}/{len(strategies)}] {strat}...", flush=True)
        try:
            r = run_one(strat)
            if r:
                mark = "PASS" if r['sharpe'] > 0.5 and r['pos_folds'] >= 0.6 * r['total_folds'] else "FAIL"
                r['name'] = strat
                r['mark'] = mark
                results.append(r)
                save_result(strat, r, mark)
                print(f"  {mark} Sharpe={r['sharpe']:.3f}, Return={r['ret']:.2f}%, DD={r['dd']:.1f}%, Folds={r['pos_folds']}/{r['total_folds']}")
            else:
                print(f"  NODATA")
                save_result(strat, {'sharpe':0,'ret':0,'dd':0,'pos_folds':0,'total_folds':0}, "NODATA")
        except subprocess.TimeoutExpired:
            print(f"  TIMEOUT")
            save_result(strat, {'sharpe':0,'ret':0,'dd':0,'pos_folds':0,'total_folds':0}, "TIMEOUT")
        except Exception as e:
            print(f"  ERROR: {e}")
            save_result(strat, {'sharpe':0,'ret':0,'dd':0,'pos_folds':0,'total_folds':0}, "ERROR")

    # 排序输出
    results.sort(key=lambda x: x['sharpe'], reverse=True)
    print(f"\n{'='*70}")
    print(f"{'排名':4s} {'策略':8s} {'夏普':>6s} {'收益':>8s} {'回撤':>6s} {'Fold':>6s} {'状态'}")
    print(f"{'='*70}")
    for rank, r in enumerate(results, 1):
        print(f"{rank:4d} {r['name']:8s} {r['sharpe']:6.3f} {r['ret']:7.2f}% {r['dd']:5.1f}% {r['pos_folds']:3d}/{r['total_folds']:<3d} {r['mark']}")

    print(f"\n总耗时: {time.time()-t0:.1f}s")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
