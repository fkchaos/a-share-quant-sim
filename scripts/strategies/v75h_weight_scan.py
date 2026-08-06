#!/usr/bin/env python3
"""v75h: 因子权重扫描（可续跑版）
基于v75f（广度过滤），扫描v75a底层三因子权重组合。
用全量回测（full=True）做初筛，找到最优权重后WF验证。

特点：
- 每组完成后立即写入结果文件（断电可续）
- 重启时自动跳过已完成的组
- DEBUG输出重定向到/dev/null（避免I/O阻塞）

用法：
  python scripts/strategies/v75h_weight_scan.py          # 粗扫（默认）
  python scripts/strategies/v75h_weight_scan.py --fine    # 精扫（±0.05）
"""
import sys, os, time, json, argparse, warnings, logging
warnings.filterwarnings('ignore')

# 抑制所有DEBUG输出 —— 关键！否则每组从30秒膨胀到50分钟
logging.disable(logging.CRITICAL)
os.environ['PYTHONWARNINGS'] = 'ignore'

sys.path.insert(0, '/root/a-share-quant-sim')

from scripts.backtest.wf_runner import run_wf

# ── 扫描网格 ──
# 权重必须归一化（和=1.0）

COARSE_GRID = [
    # (描述, 突破, 放量, 流动性)
    ("流动性主导",     0.30, 0.30, 0.40),
    ("均衡偏量",       0.35, 0.35, 0.30),
    ("突破主导",       0.40, 0.30, 0.30),
    ("默认(基准)",     0.45, 0.30, 0.25),
    ("突破更高",       0.50, 0.25, 0.25),
    ("突破最高",       0.55, 0.25, 0.20),
    ("突破+放量并重",  0.40, 0.40, 0.20),
    ("放量主导",       0.30, 0.45, 0.25),
]

OUT = '/tmp/v75h_weight_scan.txt'
LOG = '/tmp/v75h_weight_scan_debug.log'


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


def run_one_weight(name, w_breakout, w_vol, w_liq):
    """修改v75a权重，跑全量回测，返回结果"""
    import scripts.strategies.v75a_tech_momentum as mod
    orig = dict(mod.DEFAULT_PARAMS)

    mod.DEFAULT_PARAMS['W_BREAKOUT'] = w_breakout
    mod.DEFAULT_PARAMS['W_VOL_SURGE'] = w_vol
    mod.DEFAULT_PARAMS['W_LIQUIDITY'] = w_liq

    try:
        t0 = time.time()
        # 重定向stdout/stderr到/dev/null（抑制WF的DEBUG输出）
        with open(os.devnull, 'w') as devnull:
            old_out, old_err = sys.stdout, sys.stderr
            sys.stdout = devnull
            sys.stderr = devnull
            try:
                result = run_wf("v75f", full=True)
            finally:
                sys.stdout, sys.stderr = old_out, old_err
        elapsed = time.time() - t0

        if result is not None and hasattr(result, 'iloc'):
            return {
                'name': name,
                'weights': {'breakout': w_breakout, 'vol': w_vol, 'liq': w_liq},
                'sharpe': round(float(result['test_sharpe'].iloc[0]), 4),
                'return_pct': round(float(result['test_ret'].iloc[0]) * 100, 2),
                'max_dd_pct': round(float(result['test_dd'].iloc[0]) * 100, 2),
                'elapsed_s': round(elapsed, 1),
            }
        else:
            return {'name': name, 'error': 'run_wf returned None'}
    finally:
        mod.DEFAULT_PARAMS.update(orig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fine', action='store_true', help='精扫模式（基于粗扫最优±0.05）')
    parser.add_argument('--grid', type=str, help='精扫时的权重JSON，格式: [[name,b,v,l],...]')
    args = parser.parse_args()

    if args.fine and args.grid:
        grid = json.loads(args.grid)
    elif args.fine:
        # 默认精扫：基于粗扫结果附近±0.05
        print("精扫模式需要 --grid 参数指定权重网格")
        print("示例: python v75h_weight_scan.py --fine --grid '[[\"微调A\",0.48,0.28,0.24],[\"微调B\",0.52,0.28,0.20]]'")
        return
    else:
        grid = COARSE_GRID

    done = load_done()
    total = len(grid)
    completed = len(done)
    results = list(done.values())

    print("=" * 70)
    print(f"v75h 因子权重扫描 — {total}组 (已完成{completed}组)")
    print(f"输出: {OUT}")
    print("=" * 70)

    for i, (name, w_bs, w_vr, w_lq) in enumerate(grid):
        if name in done:
            print(f"[{i+1}/{total}] {name} — 已完成，跳过 (Sharpe={done[name].get('sharpe','?')})")
            continue

        print(f"\n[{i+1}/{total}] {name} — 突破={w_bs:.2f} 放量={w_vr:.2f} 流动={w_lq:.2f}")
        print("-" * 60)

        r = run_one_weight(name, w_bs, w_vr, w_lq)
        results.append(r)
        save_result(r)  # 立即写入，断电不丢

        if 'error' in r:
            print(f"  ❌ 错误: {r['error']}")
        else:
            print(f"  Sharpe={r['sharpe']:.4f}  return={r['return_pct']:.2f}%  dd={r['max_dd_pct']:.2f}%  ({r['elapsed_s']:.0f}s)")

    # 汇总排序（只排非错误的）
    valid = [r for r in results if 'error' not in r]
    valid.sort(key=lambda x: x['sharpe'], reverse=True)

    print(f"\n{'='*70}")
    print("汇总（按Sharpe降序）:")
    print(f"{'='*70}")
    for i, r in enumerate(valid):
        bs = r['weights']
        marker = " <<< 当前基准" if r['name'] == '默认(基准)' else ""
        print(f"  {i+1}. {r['name']:12s}  突破={bs['breakout']:.2f} 放量={bs['vol']:.2f} 流动={bs['liq']:.2f}"
              f"  Sharpe={r['sharpe']:.4f}  收益={r['return_pct']:.2f}%  回撤={r['max_dd_pct']:.2f}%{marker}")

    if valid:
        best = valid[0]
        base = next((r for r in valid if r['name'] == '默认(基准)'), None)
        print(f"\n最优: {best['name']} (Sharpe={best['sharpe']:.4f})")
        if base:
            delta = best['sharpe'] - base['sharpe']
            print(f"vs 默认: Sharpe +{delta:.4f} ({delta/base['sharpe']*100:.1f}%提升)" if delta > 0
                  else f"vs 默认: Sharpe {delta:.4f} ({delta/base['sharpe']*100:.1f}%下降)")

    print(f"\n结果已保存: {OUT}")


if __name__ == '__main__':
    main()
