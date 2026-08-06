#!/usr/bin/env python3
"""v75i: 因子参数扫描（可续跑版）
基于v75f（广度过滤+默认权重），扫描因子窗口参数。
用全量回测（full=True）做初筛，最优组合WF验证。

特点：
- 每组完成后立即写入结果文件（断电可续）
- 重启时自动跳过已完成的组
- DEBUG输出重定向到/dev/null（避免I/O阻塞）

用法：
  python scripts/strategies/v75i_factor_param_scan.py          # 单参数逐个扫（默认）
  python scripts/strategies/v75i_factor_param_scan.py --grid '[[name,params],...]'  # 自定义网格
"""
import sys, os, time, json, argparse, warnings, logging
warnings.filterwarnings('ignore')

# 抑制所有DEBUG输出 —— 关键！否则每组从30秒膨胀到50分钟
logging.disable(logging.CRITICAL)
os.environ['PYTHONWARNINGS'] = 'ignore'

sys.path.insert(0, '/root/a-share-quant-sim')

from scripts.backtest.wf_runner import run_wf

# ── 扫描网格 ──
# 单参数逐个扫：固定其他参数，扫一个参数

# 1. 突破窗口
BREAKOUT_GRID = [
    # (描述, BREAKOUT_WINDOW)
    ("突破10日", 10),
    ("突破15日", 15),
    ("突破20日(默认)", 20),
    ("突破25日", 25),
    ("突破30日", 30),
]

# 2. 短期均量窗口
VOL_SHORT_GRID = [
    ("放量3日", 3),
    ("放量5日(默认)", 5),
    ("放量10日", 10),
]

# 3. 长期均量窗口
VOL_LONG_GRID = [
    ("长期15日", 15),
    ("长期20日(默认)", 20),
    ("长期30日", 30),
]

# 4. 流动性窗口
LIQ_GRID = [
    ("流动10日", 10),
    ("流动15日", 15),
    ("流动20日(默认)", 20),
    ("流动30日", 30),
]

OUT = '/tmp/v75i_param_scan.txt'

# 默认参数（v75f基准）
DEFAULT_WINDOWS = {
    'BREAKOUT': 20,
    'VOL_SHORT': 5,
    'VOL_LONG': 20,
    'LIQ': 20,
}


def load_done():
    """加载已完成的结果（用于断电续跑）"""
    done = {}
    if os.path.exists(OUT):
        with open(OUT, 'r') as f:
            for line in f:
                line = line.strip()
                if line.startswith('{'):
                    try:
                        r = json.loads(line)
                        done[r['name']] = r
                    except:
                        pass
    return done


def save_result(r):
    """追加单条结果到文件"""
    with open(OUT, 'a') as f:
        f.write(json.dumps(r, ensure_ascii=False) + '\n')


def run_one_param(name, param_key, param_value):
    """修改单个窗口参数，跑全量回测，返回结果"""
    import scripts.strategies.v75a_tech_momentum as mod

    # 保存原始权重（不改权重，只改窗口）
    orig_params = dict(mod.DEFAULT_PARAMS)

    try:
        t0 = time.time()
        # 重定向stdout/stderr到/dev/null
        with open(os.devnull, 'w') as devnull:
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout = devnull
            sys.stderr = devnull
            try:
                # 通过adapter设置窗口参数
                from scripts.backtest.strategy_adapter import get_adapter
                adapter = get_adapter()
                adapter._risk_params["v75i"][param_key] = param_value
                result = run_wf("v75i", full=True)
            finally:
                sys.stdout, sys.stderr = old_out, old_err
        elapsed = time.time() - t0

        if result is not None and hasattr(result, 'iloc'):
            return {
                'name': name,
                'param': param_key,
                'value': param_value,
                'sharpe': round(float(result['test_sharpe'].iloc[0]), 4),
                'return_pct': round(float(result['test_ret'].iloc[0]) * 100, 2),
                'max_dd_pct': round(float(result['test_dd'].iloc[0]) * 100, 2),
                'elapsed_s': round(elapsed, 1),
            }
        else:
            return {'name': name, 'error': 'run_wf returned None'}
    finally:
        pass  # 窗口参数由adapter管理，不需要恢复


def run_scan(grid, param_key):
    """扫描一组参数"""
    done = load_done()
    total = len(grid)
    completed = len(done)
    results = list(done.values())

    print(f"\n{'='*70}")
    print(f"扫描 {param_key} — {total}组 (已完成{completed}组)")
    print(f"{'='*70}")

    for i, (name, value) in enumerate(grid):
        if name in done:
            print(f"[{i+1}/{total}] {name} — 已完成，跳过 (Sharpe={done[name].get('sharpe','?')})")
            continue

        print(f"\n[{i+1}/{total}] {name} — {param_key}={value}")
        print("-" * 60)

        r = run_one_param(name, param_key, value)
        results.append(r)
        save_result(r)  # 立即写入，断电不丢

        if 'error' in r:
            print(f"  ❌ 错误: {r['error']}")
        else:
            print(f"  Sharpe={r['sharpe']:.4f}  return={r['return_pct']:.2f}%  dd={r['max_dd_pct']:.2f}%  ({r['elapsed_s']:.0f}s)")

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--grid', type=str, help='自定义网格JSON: [[name,value],...]')
    parser.add_argument('--param', type=str, default='BREAKOUT', help='扫描哪个参数: BREAKOUT/VOL_SHORT/VOL_LONG/LIQ')
    args = parser.parse_args()

    if args.grid:
        grid = json.loads(args.grid)
    elif args.param == 'BREAKOUT':
        grid = BREAKOUT_GRID
    elif args.param == 'VOL_SHORT':
        grid = VOL_SHORT_GRID
    elif args.param == 'VOL_LONG':
        grid = VOL_LONG_GRID
    elif args.param == 'LIQ':
        grid = LIQ_GRID
    else:
        print(f"未知参数: {args.param}")
        return

    print("=" * 70)
    print(f"v75i 因子参数扫描 — {args.param}")
    print(f"输出: {OUT}")
    print("=" * 70)

    results = run_scan(grid, args.param)

    # 汇总排序
    valid = [r for r in results if 'error' not in r]
    valid.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n{'='*70}")
    print(f"{args.param} 扫描汇总（按Sharpe降序）:")
    print(f"{'='*70}")
    for i, r in enumerate(valid):
        marker = " <<< 默认" if "默认" in r['name'] else ""
        print(f"  {i+1}. {r['name']:20s}  {args.param}={r['value']:>3}  Sharpe={r['sharpe']:.4f}  收益={r['return_pct']:.2f}%  回撤={r['max_dd_pct']:.2f}%{marker}")

    if valid:
        best = valid[0]
        base = next((r for r in valid if "默认" in r['name']), None)
        print(f"\n最优: {best['name']} ({args.param}={best['value']}, Sharpe={best['sharpe']:.4f})")
        if base:
            delta = best['sharpe'] - base['sharpe']
            print(f"vs 默认: Sharpe +{delta:.4f} ({delta/base['sharpe']*100:.1f}%提升)" if delta > 0
                  else f"vs 默认: Sharpe {delta:.4f} ({delta/base['sharpe']*100:.1f}%下降)")

    print(f"\n结果已保存: {OUT}")


if __name__ == '__main__':
    main()
