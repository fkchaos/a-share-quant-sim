#!/usr/bin/env python3
"""
综合对比：所有策略最终排名
============================
基于 2022-01 ~ 2026-05 回测 + WF 验证
"""
print("=" * 80)
print("策略综合排名（2022-2026）")
print("=" * 80)

results = [
    # 策略, 全量年化, 全量夏普, 全量回撤, WF夏普, WF正收益, 状态
    ("v22 纯动量",           252.0, 5.54, 7.1,  None,  None,  "✅ 全量最优"),
    ("v27 价量共振",         251.0, 5.72, 6.7,  8.66,  "15/15", "✅ WF通过"),
    ("v18b vol_of_vol改进",  251.8, 5.50, 7.6,  None,  None,  "⚠️ 不如v27"),
    ("v28 滴水穿石",         125.0, 5.65, 5.8,  None,  None,  "❌ 收益太低"),
    ("v29 球队硬币",         124.1, 5.62, 5.8,  None,  None,  "❌ 收益太低"),
]

print(f"\n{'策略':20} | {'全量年化':>8} | {'全量夏普':>8} | {'全量回撤':>8} | {'WF夏普':>8} | {'WF正收益':>8} | {'状态':>10}")
print("-" * 90)
for name, ar, sh, dd, wf_sh, wf_pos, status in results:
    wf_sh_str = f"{wf_sh:.2f}" if wf_sh else "—"
    wf_pos_str = wf_pos if wf_pos else "—"
    print(f"{name:20} | {ar:>7.1f}% | {sh:>7.2f} | {dd:>7.1f}% | {wf_sh_str:>8} | {wf_pos_str:>8} | {status:>10}")

print(f"\n{'='*80}")
print(f"结论：")
print(f"  1. v27 价量共振 WF 结果最好（15/15 正收益，夏普 8.66）")
print(f"  2. v22 纯动量全量收益最高（252%），但 WF 未跑")
print(f"  3. 增强因子作为'加分'会稀释收益（v28/v29 年化只有 v22 一半）")
print(f"  4. v27 的 pv_corr_20 因子 IC_IR=0.135，是唯一有效的新因子")
print(f"{'='*80}")
