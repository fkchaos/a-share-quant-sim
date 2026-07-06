#!/usr/bin/env python3
"""
v68 因子权重扫描
扫描 W_MOM × W_ILLIQ × W_SIZE 组合
"""
import sys, os, json, time, io
from contextlib import redirect_stdout
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scripts.backtest.wf_runner import run_wf
from scripts.backtest.strategy_adapter import get_adapter

MOM_VALS = [0.15, 0.20, 0.25, 0.30, 0.35]
ILLIQ_VALS = [0.00, 0.05, 0.10, 0.15]
SIZE_VALS = [0.25, 0.30, 0.35]

RESULT_FILE = '/root/a-share-quant-sim/v68_weight_scan.json'

def load_results():
    if os.path.exists(RESULT_FILE):
        with open(RESULT_FILE) as f:
            return json.load(f)
    return {}

def save_results(results):
    with open(RESULT_FILE, 'w') as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

def main():
    results = load_results()
    adapter = get_adapter()
    total = len(MOM_VALS) * len(ILLIQ_VALS) * len(SIZE_VALS)
    done = sum(1 for v in results.values() if 'sharpe' in v)
    print(f"v68 权重扫描: {total} 组合, 已完成 {done}")

    for w_mom in MOM_VALS:
        for w_illiq in ILLIQ_VALS:
            for w_size in SIZE_VALS:
                key = f"mom{w_mom}_illiq{w_illiq}_size{w_size}"
                if key in results and 'sharpe' in results[key]:
                    print(f"  [跳过] {key} → 夏普={results[key]['sharpe']:.3f}")
                    continue

                rp = adapter._risk_params['v68']
                rp['W_MOM'] = w_mom
                rp['W_ILLIQ'] = w_illiq
                rp['W_SIZE'] = w_size

                t0 = time.time()
                try:
                    buf = io.StringIO()
                    with redirect_stdout(buf):
                        df = run_wf('v68', 252, 126, 63, '2021-01-01', '2026-06-24')
                    elapsed = time.time() - t0

                    sharpe = df['test_sharpe'].mean()
                    ret = df['test_ret'].mean() * 100  # 转百分比
                    dd = df['test_dd'].mean() * 100
                    pos_rate = (df['test_ret'] > 0).sum() / len(df) * 100

                    results[key] = {
                        'sharpe': round(sharpe, 3),
                        'ret': round(ret, 2),
                        'dd': round(dd, 2),
                        'pos_rate': round(pos_rate, 1),
                        'n_folds': len(df),
                        'params': {'W_MOM': w_mom, 'W_ILLIQ': w_illiq, 'W_SIZE': w_size},
                        'time': round(elapsed, 1),
                    }
                    save_results(results)
                    done += 1
                    print(f"  [{done}/{total}] {key} → 夏普={sharpe:.3f} 收益={ret:.1f}% 回撤={dd:.1f}% 正fold={pos_rate:.0f}% ({elapsed:.0f}s)")
                except Exception as e:
                    import traceback
                    print(f"  [{done+1}/{total}] {key} → ERROR: {e}")
                    traceback.print_exc()
                    results[key] = {'error': str(e)}
                    save_results(results)

    # 排序输出
    valid = {k: v for k, v in results.items() if 'sharpe' in v}
    ranked = sorted(valid.items(), key=lambda x: x[1]['sharpe'], reverse=True)

    print(f"\n{'='*60}")
    print(f"v68 权重扫描 Top 10")
    print(f"{'='*60}")
    for rank, (k, v) in enumerate(ranked[:10], 1):
        p = v['params']
        print(f"  #{rank:2d} 夏普={v['sharpe']:.3f} 收益={v['ret']:.1f}% 回撤={v['dd']:.1f}% 正fold={v['pos_rate']:.0f}% | MOM={p['W_MOM']} ILLIQ={p['W_ILLIQ']} SIZE={p['W_SIZE']}")

if __name__ == '__main__':
    main()
