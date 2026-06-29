#!/usr/bin/env python3
"""
v51 卖出优化 — WF 参数扫描（清洁版）
抑制所有中间输出，只打印最终对比表。
"""
import sys, os, time, io
sys.path.insert(0, '/root/a-share-quant-sim')

# 抑制 stderr (debug 输出)
sys.stderr = open(os.devnull, 'w')
# 抑制 stdout (交易日志) — 在 run_wf 内部恢复
_old_stdout = sys.stdout

from scripts.backtest.wf_runner import run_wf
from scripts.backtest.strategy_adapter import get_adapter

# 扫描配置: (sl, tp, hold_max, hold_ext, hold_ext_pnl, label)
configs = [
    (-0.03, 0.10, 5, 5, 0.03, 'tight_sl'),
    (-0.05, 0.15, 5, 5, 0.03, 'wide_tp'),
    (-0.03, 0.15, 5, 5, 0.03, 'tight_sl_wide_tp'),
    (-0.05, 0.10, 8, 7, 0.03, 'long_hold'),
    (-0.05, 0.10, 8, 7, 0.05, 'long_hold_high_ext'),
    (-0.03, 0.08, 3, 3, 0.02, 'fast_turn'),
    (-0.08, 0.20, 10, 7, 0.05, 'loose_sl_long'),
    (-0.03, 0.20, 5, 7, 0.03, 'asym_3_20'),
    (-0.03, 0.10, 3, 3, 0.02, 'tight_sl_short'),
    (-0.08, 0.08, 5, 5, 0.03, 'wide_sl_tight_tp'),
]

BASELINE = {
    'label': 'v39i_baseline', 'sl': -0.05, 'tp': 0.10,
    'hold': 5, 'ext': 5, 'ext_pnl': 0.03,
    'ret': 103.51, 'sharpe': 1.199, 'dd': 16.69, 'pf': '4/4',
    'note': '2021-2026, 4folds',
}


def run_silently(cfg):
    """跑 WF，抑制 run_wf 内部的交易日志"""
    sl, tp, hold_max, hold_ext, hold_ext_pnl, label = cfg
    
    # 直接修改全局 _risk_params（get_risk_params 返回副本，不能改副本）
    adapter = get_adapter()
    rp = adapter._risk_params['v39i']
    rp['STOP_LOSS'] = sl
    rp['TAKE_PROFIT'] = tp
    rp['HOLD_DAYS_MAX'] = hold_max
    rp['HOLD_DAYS_EXTEND'] = hold_ext
    rp['HOLD_DAYS_EXTEND_PNL'] = hold_ext_pnl
    
    # 抑制 run_wf 内部的 stdout (交易日志)
    sys.stdout = io.StringIO()
    t0 = time.time()
    result = run_wf('v39i', 252, 126, 63, '2023-01-01', '2026-06-24')
    elapsed = time.time() - t0
    sys.stdout = _old_stdout  # 恢复，后续 print 可见
    
    if result is None or len(result) == 0:
        return None
    
    avg_ret = result['test_ret'].mean() * 100
    avg_sharpe = result['test_sharpe'].mean()
    avg_dd = result['test_dd'].mean() * 100
    pos_folds = (result['test_ret'] > 0).sum()
    total_folds = len(result)
    
    return {
        'label': label, 'sl': sl, 'tp': tp,
        'hold': hold_max, 'ext': hold_ext, 'ext_pnl': hold_ext_pnl,
        'ret': avg_ret, 'sharpe': avg_sharpe, 'dd': avg_dd,
        'pf': f"{pos_folds}/{total_folds}",
        'elapsed': elapsed,
    }


if __name__ == '__main__':
    print("=" * 70)
    print("v51 卖出优化 — WF 参数扫描")
    print("=" * 70)
    print(f"\n基线 (v39i): SL=-0.05, TP=0.10, HOLD=5, EXT=5, EXT_PNL=0.03")
    print(f"  → 夏普 1.199 / 收益 +103.51% / 回撤 16.69% / 正Fold 4/4 (2021-2026)")
    print(f"\n扫描 {len(configs)} 个配置 (2023-2026, 8 folds)...")
    
    results = []
    for i, cfg in enumerate(configs):
        sl, tp, hold_max, hold_ext, hold_ext_pnl, label = cfg
        print(f"\n[{i+1}/{len(configs)}] {label}: SL={sl}, TP={tp}, HOLD={hold_max}, EXT={hold_ext}, EXT_PNL={hold_ext_pnl}")
        
        r = run_silently(cfg)
        if r:
            results.append(r)
            print(f"  → 收益={r['ret']:+.2f}%, 夏普={r['sharpe']:.3f}, 回撤={r['dd']:.2f}%, 正Fold={r['pf']}, 耗时={r['elapsed']:.0f}s")
        else:
            print(f"  → ❌ 失败")
    
    # 恢复 stderr
    sys.stderr = sys.__stderr__
    
    # 汇总
    print(f"\n\n{'='*70}")
    print("汇总对比")
    print(f"{'='*70}")
    
    header = f"{'配置':<22} {'SL':>6} {'TP':>6} {'HOLD':>5} {'EXT':>4} {'ePNL':>5} {'收益%':>9} {'夏普':>7} {'回撤%':>7} {'正Fold':>7}"
    print(f"\n{header}")
    print("─" * 85)
    
    # 基线
    b = BASELINE
    print(f"{b['label']:<22} {b['sl']:>6.2f} {b['tp']:>6.2f} {b['hold']:>5} {b['ext']:>4} {b['ext_pnl']:>5.2f} {b['ret']:>+8.2f} {b['sharpe']:>7.3f} {b['dd']:>7.2f} {b['pf']:>7}  ({b['note']})")
    
    for r in results:
        print(f"{r['label']:<22} {r['sl']:>6.2f} {r['tp']:>6.2f} {r['hold']:>5} {r['ext']:>4} {r['ext_pnl']:>5.2f} {r['ret']:>+8.2f} {r['sharpe']:>7.3f} {r['dd']:>7.2f} {r['pf']:>7}")
    
    # 找最优（按夏普）
    valid = [r for r in results if isinstance(r.get('sharpe'), (int, float))]
    if valid:
        best = max(valid, key=lambda x: x['sharpe'])
        print(f"\n🏆 最优 (按夏普): {best['label']}")
        print(f"   SL={best['sl']}, TP={best['tp']}, HOLD={best['hold']}, EXT={best['ext']}, EXT_PNL={best['ext_pnl']}")
        print(f"   夏普={best['sharpe']:.3f}, 收益={best['ret']:+.2f}%, 回撤={best['dd']:.2f}%")
    
    # 找最优（按回撤）
    if valid:
        best_dd = min(valid, key=lambda x: x['dd'])
        print(f"\n🛡️ 最优 (按回撤): {best_dd['label']}")
        print(f"   回撤={best_dd['dd']:.2f}%, 夏普={best_dd['sharpe']:.3f}, 收益={best_dd['ret']:+.2f}%")
