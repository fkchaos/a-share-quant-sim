#!/usr/bin/env python3
"""
v12_multi 权重扫描 — 直接调用 walk_forward，一次加载数据
评分统一用 StrategyEngine multi 模式，run_kwargs 透传风控参数
"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")

from core.config import STRATEGY_PROFILES, StrategyConfig, MarketFilter
from core.data import load_and_build_panel
from core.strategy import StrategyEngine
from scripts.run_backtest import walk_forward

WEIGHT_COMBOS = [
    ("w532", 0.5, 0.3, 0.2, "当前 v12 (baseline)"),
    ("w631", 0.6, 0.3, 0.1, "提高 v11b"),
    ("w721", 0.7, 0.2, 0.1, "v11b 主导"),
    ("w820", 0.8, 0.2, 0.0, "v11b 绝对主导, 去掉 v6b"),
    ("w730", 0.7, 0.3, 0.0, "去掉 v6b"),
]

def make_profile_and_score_fn(suffix, w1, w2, w3):
    """注册临时 profile，返回 score_fn"""
    label = f"_v12scan_{suffix}"
    prof = StrategyConfig(
        label=label, weight_method="equal",
        top_n=12, rebalance_freq=20, stop_loss=0.20, max_position=0.10,
        use_vol_scaling=True, vol_target=0.20, max_industry_weight=0.25,
        use_take_profit=True, tp_tiers=[(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
        use_holding_decay=True, factor_weights=None,
        multi_strategy={"strategies": [
            {"profile": "v11b_zz800_union", "mode": "ensemble", "weight": w1},
            {"profile": "v10c_zz800_balanced", "mode": "factor", "weight": w2},
            {"profile": "v6b_hlr", "mode": "factor", "weight": w3},
        ]},
    )
    STRATEGY_PROFILES[label] = prof
    engine = StrategyEngine(profile=label, mode="multi")
    return lambda factors: engine.score_panel(factors)

def main():
    print("=" * 60)
    print("v12_multi 权重扫描 (WF)")
    print("=" * 60)

    print("\n加载数据...")
    t0 = time.time()
    loaded, codes = load_and_build_panel(None, None, need_open=False, need_hl=True, market_filter=MarketFilter())
    close_panel, volume_panel, amount_panel = loaded[0], loaded[1], loaded[2]
    high_panel, low_panel = loaded[3], loaded[4]
    print(f"  {close_panel.shape[1]} 只股票, {close_panel.shape[0]} 天, {time.time()-t0:.1f}s")

    # 风控参数（跟 v12_multi 一致）
    run_kwargs = {
        'use_take_profit': True, 'tp_tiers': [(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)],
        'use_holding_decay': True, 'max_industry_weight': 0.25,
        'max_position': 0.10, 'use_vol_scaling': True, 'vol_target': 0.20,
    }

    all_results = []

    for suffix, w1, w2, w3, desc in WEIGHT_COMBOS:
        label = f"v12_{suffix}"
        print(f"\n{'─'*50}")
        print(f"▶ {label}: v11b={w1} v10c={w2} v6b={w3} ({desc})")
        t1 = time.time()

        score_fn = make_profile_and_score_fn(suffix, w1, w2, w3)

        wf_results, wf_nav = walk_forward(
            close_panel, score_fn=score_fn, run_kwargs=run_kwargs,
            volume_panel=volume_panel, amount_panel=amount_panel,
            high_panel=high_panel, low_panel=low_panel, label=label,
        )

        elapsed = time.time() - t1

        if wf_results:
            df = pd.DataFrame(wf_results)
            avg_ret = df['ann_return'].mean()
            avg_sharpe = df['sharpe'].mean()
            pos = (df['ann_return'] > 0).sum()
            total = len(df)
            avg_dd = df['max_dd'].mean()
            print(f"  年化={avg_ret*100:.1f}% Sharpe={avg_sharpe:.2f} "
                  f"回撤={avg_dd*100:.1f}% 正收益={pos}/{total} ({pos/total*100:.0f}%) {elapsed:.0f}s")
            all_results.append({
                'label': label, 'weights': f"v11b={w1}/v10c={w2}/v6b={w3}",
                'w1': w1, 'w2': w2, 'w3': w3, 'desc': desc,
                'avg_annual': round(avg_ret * 100, 2), 'avg_sharpe': round(avg_sharpe, 3),
                'avg_maxdd': round(avg_dd * 100, 2), 'positive_folds': f"{pos}/{total}",
                'positive_pct': round(pos / total * 100, 0), 'elapsed_s': round(elapsed, 1),
            })

    print(f"\n{'='*70}")
    print("权重扫描汇总 (按正收益比例降序)")
    print(f"{'='*70}")
    all_results.sort(key=lambda x: (-x['positive_pct'], -x['avg_sharpe']))
    print(f"{'权重':<30} {'年化%':>8} {'夏普':>7} {'回撤%':>8} {'正收益':>8}")
    print("-" * 70)
    for r in all_results:
        print(f"{r['weights']:<30} {r['avg_annual']:>8.1f} {r['avg_sharpe']:>7.2f} {r['avg_maxdd']:>8.1f} {r['positive_folds']:>8}")

    os.makedirs(REPORT_DIR, exist_ok=True)
    out_file = os.path.join(REPORT_DIR, f"v12_weight_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(out_file, 'w') as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_file}")
    return all_results

if __name__ == '__main__':
    main()
