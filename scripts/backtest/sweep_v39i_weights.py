#!/usr/bin/env python3
"""
scripts/backtest/sweep_v39i_weights.py — v39i 因子权重扫描
一次加载数据，循环跑不同权重组合，大幅加速。
"""
import sys
import os
import time
import re
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import subprocess


def run_one(weights, start='2021-01-01', end='2026-06-01'):
    """跑一次 WF，返回 (sharpe, ret, dd, pos_folds, total_folds)"""
    w = weights
    cmd = [sys.executable, '-c', f'''
import sys, os, re
sys.path.insert(0, ".")
os.environ["PYTHONUNBUFFERED"] = "1"
from scripts.backtest.strategy_adapter import get_adapter
from scripts.backtest.wf_runner import run_wf

a = get_adapter()
a._risk_params["v39i"]["MAX_DAILY_BUY"] = 3
a._risk_params["v39i"]["MAX_POSITION"] = 0.20
a._risk_params["v39i"].update({{
    "W_MOM": {w["W_MOM"]}, "W_PV_CORR": {w["W_PV_CORR"]},
    "W_TURNOVER": {w["W_TURNOVER"]}, "W_SIZE": {w["W_SIZE"]},
    "W_FUND_FLOW": {w["W_FUND_FLOW"]}, "W_GAP": {w["W_GAP"]},
    "W_ILLIQ": {w["W_ILLIQ"]},
}})
df = run_wf("v39i", train_days=252, test_days=126, step_days=63,
            start_date="{start}", end_date="{end}")
if df is not None and len(df) > 0:
    s = df["test_sharpe"].mean()
    r = df["test_ret"].mean() * 100
    d = df["test_dd"].mean() * 100
    p = (df["test_ret"] > 0).sum()
    t = len(df)
    print(f"RESULT: sharpe={{s:.3f}}, ret={{r:.2f}}, dd={{d:.1f}}, folds={{p}}/{{t}}")
else:
    print("RESULT: FAILED")
''']

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    output = result.stdout + result.stderr
    for line in output.split('\n'):
        m = re.search(r'RESULT: sharpe=([\d.]+), ret=([\d.]+), dd=([\d.]+), folds=(\d+)/(\d+)', line)
        if m:
            return float(m.group(1)), float(m.group(2)), float(m.group(3)), int(m.group(4)), int(m.group(5))
    return None


def main():
    print("=" * 70)
    print("v39i 因子权重扫描（amount 修复后，原始风控参数 BUY=3/POS=0.20）")
    print("=" * 70)

    weight_configs = [
        # (name, W_MOM, W_PV_CORR, W_TURNOVER, W_SIZE, W_FUND_FLOW, W_GAP, W_ILLIQ)
        ('01_baseline',      0.15, 0.05, 0.05, 0.30, 0.05, 0.05, 0.20),
        ('02_illiq_heavy',   0.10, 0.05, 0.05, 0.35, 0.00, 0.05, 0.30),
        ('03_balanced',      0.10, 0.05, 0.10, 0.30, 0.00, 0.05, 0.25),
        ('04_top3_equal',    0.05, 0.05, 0.20, 0.30, 0.00, 0.05, 0.35),
        ('05_size_illiq',    0.05, 0.00, 0.10, 0.40, 0.00, 0.05, 0.40),
        ('06_turnover_heavy',0.10, 0.05, 0.20, 0.25, 0.00, 0.05, 0.20),
        ('07_drop_weak',     0.10, 0.00, 0.10, 0.35, 0.00, 0.05, 0.30),
        ('08_illiq_turnover',0.05, 0.05, 0.15, 0.30, 0.00, 0.05, 0.35),
    ]

    results = []
    t0 = time.time()

    for name, wm, wp, wt, ws, wf, wg, wi in weight_configs:
        weights = {
            'W_MOM': wm, 'W_PV_CORR': wp, 'W_TURNOVER': wt,
            'W_SIZE': ws, 'W_FUND_FLOW': wf, 'W_GAP': wg, 'W_ILLIQ': wi,
        }
        print(f"\n{name}: MOM={wm} PV={wp} TO={wt} SIZE={ws} FF={wf} GAP={wg} ILLIQ={wi}")
        r = run_one(weights)
        if r:
            sharpe, ret, dd, pf, tf = r
            mark = "✅" if sharpe > 0.5 and pf >= 0.6 * tf else "❌"
            results.append((name, sharpe, ret, dd, pf, tf, mark))
            print(f"  {mark} Sharpe={sharpe:.3f}, Return={ret:.2f}%, DD={dd:.1f}%, Folds={pf}/{tf}")
        else:
            print(f"  FAILED")
            results.append((name, 0, 0, 0, 0, 0, "❌"))

    # 排序输出
    results.sort(key=lambda x: x[1], reverse=True)
    print(f"\n{'='*70}")
    print(f"{'排名':4s} {'配置':20s} {'夏普':>6s} {'收益':>8s} {'回撤':>6s} {'Fold':>6s} {'状态'}")
    print(f"{'='*70}")
    for rank, (name, sharpe, ret, dd, pf, tf, mark) in enumerate(results, 1):
        print(f"{rank:4d} {name:20s} {sharpe:6.3f} {ret:7.2f}% {dd:5.1f}% {pf:3d}/{tf:<3d} {mark}")

    print(f"\n总耗时: {time.time()-t0:.1f}s")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
