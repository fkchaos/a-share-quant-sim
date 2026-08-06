#!/usr/bin/env python3
"""v75h: 因子权重扫描脚本
基于v75f（广度过滤），扫描v75a底层三因子权重组合。
用全量回测（full=True）做初筛，找到最优权重后WF验证。

用法：
  python scripts/strategies/v75h_weight_scan.py          # 默认粗扫6组
  python scripts/strategies/v75h_weight_scan.py --fine     # 精扫（基于粗扫最优±0.05）
  python scripts/strategies/v75h_weight_scan.py --best 0.50,0.25,0.25  # 验证特定权重
"""
import sys, os, time, warnings, json, argparse
warnings.filterwarnings('ignore')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# ── 权重网格 ──
# 三因子：突破(W_BREAKOUT) + 放量(W_VOL_SURGE) + 流动性(W_LIQUIDITY)
# 权重必须归一化（和=1.0）

COARSE_GRID = [
    # (突破, 放量, 流动性) — 描述
    (0.30, 0.30, 0.40, "流动性主导"),
    (0.35, 0.35, 0.30, "均衡偏量"),
    (0.40, 0.30, 0.30, "突破主导"),
    (0.45, 0.30, 0.25, "默认(基准)"),
    (0.50, 0.25, 0.25, "突破更高"),
    (0.55, 0.25, 0.20, "突破最高"),
    (0.40, 0.40, 0.20, "突破+放量并重"),
    (0.30, 0.45, 0.25, "放量主导"),
]

OUT = '/tmp/v75h_weight_scan.txt'
LOG = '/tmp/v75h_weight_scan_debug.log'


def run_one_weight(name, w_breakout, w_vol, w_liq):
    """运行一组权重的全量回测"""
    import importlib
    import scripts.strategies.v75a_tech_momentum as mod
    from scripts.backtest.wf_runner import run_wf

    # 保存原始权重
    orig_bs = mod.DEFAULT_PARAMS['W_BREAKOUT']
    orig_vr = mod.DEFAULT_PARAMS['W_VOL_SURGE']
    orig_lq = mod.DEFAULT_PARAMS['W_LIQUIDITY']

    # 覆盖权重
    mod.DEFAULT_PARAMS['W_BREAKOUT'] = w_breakout
    mod.DEFAULT_PARAMS['W_VOL_SURGE'] = w_vol
    mod.DEFAULT_PARAMS['W_LIQUIDITY'] = w_liq

    try:
        result = run_wf("v75f", full=True)
        if result is None:
            return None
        # full=True 返回 DataFrame
        if hasattr(result, 'iloc'):
            return {
                'name': name,
                'weights': {'breakout': w_breakout, 'vol': w_vol, 'liq': w_liq},
                'sharpe': float(result['test_sharpe'].iloc[0]),
                'return': float(result['test_return'].iloc[0]),
                'max_dd': float(result['test_max_dd'].iloc[0]),
                'win_rate': float(result['test_win_rate'].iloc[0]) if 'test_win_rate' in result.columns else 0,
            }
        else:
            return {
                'name': name,
                'weights': {'breakout': w_breakout, 'vol': w_vol, 'liq': w_liq},
                'sharpe': float(result.get('sharpe', 0)),
                'return': float(result.get('total_return', 0)),
                'max_dd': float(result.get('max_dd', 0)),
            }
    finally:
        # 恢复原始权重
        mod.DEFAULT_PARAMS['W_BREAKOUT'] = orig_bs
        mod.DEFAULT_PARAMS['W_VOL_SURGE'] = orig_vr
        mod.DEFAULT_PARAMS['W_LIQUIDITY'] = orig_lq


def main():
    parser = argparse.ArgumentParser(description='v75h 因子权重扫描')
    parser.add_argument('--fine', action='store_true', help='精扫模式（基于粗扫最优±0.05）')
    parser.add_argument('--best', type=str, help='验证特定权重，格式: 0.50,0.25,0.25')
    parser.add_argument('--grid', type=str, help='自定义网格JSON文件')
    args = parser.parse_args()

    if args.best:
        # 验证特定权重
        w = [float(x) for x in args.best.split(',')]
        assert len(w) == 3 and abs(sum(w) - 1.0) < 0.01, f"权重必须3个且归一化，当前: {w}"
        grid = [("自定义", w[0], w[1], w[2])]
    elif args.fine:
        # 精扫：基于粗扫最优±0.05
        # 先读粗扫结果
        if not os.path.exists(OUT):
            print("❌ 没有粗扫结果，先跑默认粗扫")
            return
        with open(OUT, 'r') as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith('=') and not l.startswith('时间')]
        # 解析最后一组结果的最优权重
        best = None
        best_sharpe = -999
        for line in lines:
            if '|' in line and 'sharpe' not in line.lower() and '---' not in line:
                parts = [p.strip() for p in line.split('|')]
                if len(parts) >= 5:
                    try:
                        s = float(parts[3])
                        if s > best_sharpe:
                            best_sharpe = s
                            best = parts
                    except:
                        pass
        if best is None:
            print("❌ 无法解析粗扫结果")
            return
        # 提取最优权重（从weights字段）
        # 格式: name | weights | return | sharpe | dd
        print(f"粗扫最优: {best[0]}, Sharpe={best_sharpe}")
        print("精扫需要手动指定最优权重，用 --best 参数")
        return
    elif args.grid:
        with open(args.grid, 'r') as f:
            raw = json.load(f)
        grid = [(g['name'], g['w_breakout'], g['w_vol'], g['w_liq']) for g in raw]
    else:
        grid = COARSE_GRID

    results = []
    total = len(grid)
    t0 = time.time()

    print("=" * 70)
    print(f"v75h 因子权重扫描 — {total}组")
    print(f"输出: {OUT}")
    print(f"调试: {LOG}")
    print("=" * 70)

    with open(OUT, 'a') as out_f:
        out_f.write(f"\n{'='*70}\n")
        out_f.write(f"v75h 因子权重扫描 — {time.strftime('%Y-%m-%d %H:%M')}\n")
        out_f.write(f"{'='*70}\n")

        for i, (name, w_bs, w_vr, w_lq) in enumerate(grid):
            t1 = time.time()
            print(f"\n[{i+1}/{total}] {name} — 突破={w_bs:.2f} 放量={w_vr:.2f} 流动={w_lq:.2f}")
            out_f.write(f"\n[{i+1}/{total}] {name}\n")
            out_f.write(f"  weights: breakout={w_bs:.2f} vol={w_vr:.2f} liq={w_lq:.2f}\n")

            r = run_one_weight(name, w_bs, w_vr, w_lq)
            elapsed = time.time() - t1

            if r is None:
                print(f"  ❌ 失败")
                out_f.write(f"  result: FAILED\n")
                continue

            results.append(r)
            print(f"  Sharpe={r['sharpe']:.3f}  收益={r['return']:.1f}%  回撤={r['max_dd']:.1f}%  ({elapsed:.0f}s)")
            out_f.write(f"  Sharpe={r['sharpe']:.3f}  return={r['return']:.1f}%  dd={r['max_dd']:.1f}%\n")

        # 汇总排序
        results.sort(key=lambda x: x['sharpe'])
        total_time = time.time() - t0
        print(f"\n{'='*70}")
        print(f"扫描完成 — {total_time:.0f}s")
        print(f"{'='*70}")
        print(f"{'排名':<4} {'名称':<20} {'权重(突破/放量/流动)':<25} {'Sharpe':<10} {'收益':<10} {'回撤':<10}")
        print("-" * 80)
        for rank, r in enumerate(results, 1):
            w = r['weights']
            w_str = f"{w['breakout']:.2f}/{w['vol']:.2f}/{w['liq']:.2f}"
            print(f"{rank:<4} {r['name']:<20} {w_str:<25} {r['sharpe']:<10.3f} {r['return']:<10.1f}% {r['max_dd']:<10.1f}%")

        out_f.write(f"\n{'='*70}\n")
        out_f.write(f"汇总排序 (总耗时 {total_time:.0f}s)\n")
        out_f.write(f"{'='*70}\n")
        for rank, r in enumerate(results, 1):
            w = r['weights']
            out_f.write(f"#{rank} {r['name']}: {w['breakout']:.2f}/{w['vol']:.2f}/{w['liq']:.2f} "
                       f"Sharpe={r['sharpe']:.3f} return={r['return']:.1f}% dd={r['max_dd']:.1f}%\n")

        if results:
            best = results[-1]  # sorted ascending, last is best
            out_f.write(f"\n最优: {best['name']} Sharpe={best['sharpe']:.3f}\n")
            out_f.write(f"权重: breakout={best['weights']['breakout']:.2f} vol={best['weights']['vol']:.2f} liq={best['weights']['liq']:.2f}\n")
            print(f"\n最优: {best['name']} Sharpe={best['sharpe']:.3f}")


if __name__ == '__main__':
    main()
