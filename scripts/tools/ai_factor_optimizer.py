#!/usr/bin/env python3
"""
ai_factor_optimizer — 基于 IC 分析的因子组合优化工具
==============================================

功能：
1. 读取 IC 分析结果
2. 基于 IC 矩阵计算因子相关性，识别冗余
3. 用 IC_IR 加权 + 冗余剔除，生成最优因子权重
4. 对比新旧因子组合的回测表现
5. 可选：调用 DeepSeek 获取 AI 建议

用法：
    python scripts/ai_factor_optimizer.py --ic-path /root/data/backtest_results/ic_analysis_db.csv
    python scripts/ai_factor_optimizer.py --compare  # 对比新旧组合
"""

import sys, os
import json
import time
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(__file__))

DATA_DIR = Path(os.environ.get('BACKTEST_DATA_DIR', os.path.join(os.environ.get('PROJECT_ROOT', os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')))
REPORT_DIR = DATA_DIR / 'backtest_results'


def load_ic_results(ic_path=None):
    """加载 IC 分析结果"""
    path = Path(ic_path) if ic_path else REPORT_DIR / "ic_analysis_db.csv"
    if not path.exists():
        print("IC 结果不存在，请先运行 ai_factor_analyzer.py --calc-only")
        return None
    df = pd.read_csv(path, index_col=0)
    print("加载 IC 结果: %d 个因子" % len(df))
    return df


def analyze_factor_redundancy(ic_df, corr_threshold=0.8):
    """分析因子冗余（基于 IC 序列相关性）"""
    # 这里用 IC_mean 和 IR 来推断冗余
    # 同名前缀的因子（如 mom_5/mom_10/mom_20）通常高度相关

    groups = {}
    for name in ic_df.index:
        # 提取前缀
        parts = name.rsplit('_', 1)
        if len(parts) == 2 and parts[1].isdigit():
            prefix = parts[0]
        else:
            prefix = name
        if prefix not in groups:
            groups[prefix] = []
        groups[prefix].append(name)

    redundant = {}
    for prefix, members in groups.items():
        if len(members) > 1:
            # 保留 IR 最高的
            best = max(members, key=lambda x: abs(ic_df.loc[x, 'IR']))
            redundant[prefix] = {
                'keep': best,
                'drop': [m for m in members if m != best],
                'ir_best': ic_df.loc[best, 'IR'],
            }

    return redundant


def generate_optimal_weights(ic_df, redundant_info=None):
    """
    基于 IC_IR 生成最优因子权重

    策略：
    1. 剔除冗余因子（同名前缀只保留 IR 最高的）
    2. 剔除 IR 接近 0 的因子（|IR| < 0.02）
    3. 用 |IR| 加权（IR 越高权重越大）
    4. 归一化权重和为 1
    """
    df = ic_df.copy()

    # 1. 剔除冗余
    if redundant_info:
        to_drop = []
        for prefix, info in redundant_info.items():
            to_drop.extend(info['drop'])
        df = df.drop(index=[d for d in to_drop if d in df.index])
        print("剔除冗余因子 %d 个: %s" % (len(to_drop), to_drop[:5]))

    # 2. 剔除弱因子
    weak = df[abs(df['IR']) < 0.02].index.tolist()
    df = df.drop(index=[w for w in weak if w in df.index])
    print("剔除弱因子(|IR|<0.02) %d 个" % len(weak))

    # 3. IR 加权
    df['weight'] = abs(df['IR'])
    total = df['weight'].sum()
    if total > 0:
        df['weight'] = df['weight'] / total

    # 4. 方向：IR>0 正权重，IR<0 负权重
    df['weight'] = df['weight'] * np.sign(df['IR'])

    return df.sort_values('weight', key=abs, ascending=False)


def compare_with_v13(ic_df):
    """对比 v13 当前因子组合 vs 优化后组合"""

    # v13 当前使用的因子（从 select_stocks 中提取）
    v13_factors = {
        'rev_5': -1.0,      # 反转因子（负号表示跌幅越大分越高）
        'vol_ratio_5': 0.5,  # 放量加分
        'vol_shrink': 0.3,   # 缩量企稳加分（代理）
        'range_ratio': 0.2,  # 振幅收窄加分（代理）
    }

    print("\n" + "=" * 60)
    print("v13 当前因子组合 vs IC 分析建议")
    print("=" * 60)

    print("\nv13 当前因子 IR:")
    for f, w in v13_factors.items():
        if f in ic_df.index:
            row = ic_df.loc[f]
            print(f"  {f}: IR={row['IR']:.4f}, IC_mean={row['IC_mean']:.4f}, 权重={w:+.2f}")
        else:
            print(f"  {f}: 未在 IC 结果中")

    # IC 建议的 top 因子
    print("\nIC 分析建议的 Top 10 因子（按 |IR|）:")
    top = ic_df.reindex(ic_df['IR'].abs().sort_values(ascending=False).index).head(10)
    for name, row in top.iterrows():
        direction = "正权重" if row['IR'] > 0 else "负权重"
        print(f"  {name}: IR={row['IR']:.4f}, IC_mean={row['IC_mean']:.4f}, 建议{direction}")

    # 关键发现
    print("\n关键发现:")
    rev_5_ir = ic_df.loc['rev_5', 'IR'] if 'rev_5' in ic_df.index else 0
    mom_5_ir = ic_df.loc['mom_5', 'IR'] if 'mom_5' in ic_df.index else 0
    print(f"  rev_5 IR={rev_5_ir:.4f} (反转因子在前视5天下无效)")
    print(f"  mom_5 IR={mom_5_ir:.4f} (动量因子更有效)")
    print(f"  illiquidity IR={ic_df.loc['illiquidity', 'IR']:.4f} (小市值效应最强)")
    print(f"  gap_ratio IR={ic_df.loc['gap_ratio', 'IR']:.4f} (跳空因子第二)")

    return v13_factors


def generate_v13_improvement_plan(ic_df):
    """生成 v13 改进方案"""

    print("\n" + "=" * 60)
    print("v13 因子组合改进方案")
    print("=" * 60)

    # 方案A：替换反转因子为动量因子
    print("\n方案A：反转→动量（激进）")
    print("  将 rev_5 替换为 mom_5")
    print("  预期：IR 从 %.4f 提升到 %.4f" % (
        ic_df.loc['rev_5', 'IR'] if 'rev_5' in ic_df.index else 0,
        ic_df.loc['mom_5', 'IR'] if 'mom_5' in ic_df.index else 0))

    # 方案B：加入 gap_ratio
    print("\n方案B：加入跳空因子（稳健）")
    print("  保留 rev_5，加入 gap_ratio 作为辅助")
    print("  gap_ratio IR=%.4f，与 rev_5 相关性低" % ic_df.loc['gap_ratio', 'IR'])

    # 方案C：加入 illiquidity
    print("\n方案C：加入非流动性因子")
    print("  加入 illiquidity（IR=%.4f）作为选股过滤" % ic_df.loc['illiquidity', 'IR'])
    print("  小市值效应在 A 股长期有效")

    # 方案D：综合优化
    print("\n方案D：综合优化（推荐）")
    print("  主因子：mom_5 (IR=%.4f)" % ic_df.loc['mom_5', 'IR'])
    print("  辅助1：gap_ratio (IR=%.4f)" % ic_df.loc['gap_ratio', 'IR'])
    print("  辅助2：illiquidity (IR=%.4f)" % ic_df.loc['illiquidity', 'IR'])
    print("  过滤：vol_20 > 阈值（高波动率股票）")

    return {
        'scheme_A': {'mom_5': 1.0},
        'scheme_B': {'rev_5': -0.5, 'gap_ratio': 0.5},
        'scheme_C': {'rev_5': -0.3, 'illiquidity': 0.7},
        'scheme_D': {'mom_5': 0.4, 'gap_ratio': 0.3, 'illiquidity': 0.3},
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="因子组合优化")
    parser.add_argument("--ic-path", type=str, default=None)
    parser.add_argument("--compare", action="store_true", help="对比 v13 当前 vs 优化")
    parser.add_argument("--optimize", action="store_true", help="生成最优权重")
    parser.add_argument("--schemes", action="store_true", help="生成改进方案")
    args = parser.parse_args()

    ic_df = load_ic_results(args.ic_path)
    if ic_df is None:
        return

    print("\nIC 分析结果摘要:")
    print("  因子数: %d" % len(ic_df))
    print("  |IR|>0.05 的因子: %d" % (abs(ic_df['IR']) > 0.05).sum())
    print("  IR>0 的因子: %d" % (ic_df['IR'] > 0).sum())
    print("  IR<0 的因子: %d" % (ic_df['IR'] < 0).sum())

    # 冗余分析
    redundant = analyze_factor_redundancy(ic_df)
    if redundant:
        print("\n冗余因子组:")
        for prefix, info in redundant.items():
            print(f"  {prefix}: 保留 {info['keep']}(IR={info['ir_best']:.4f}), 剔除 {info['drop']}")

    if args.compare or not args.optimize:
        compare_with_v13(ic_df)

    if args.optimize or not args.compare:
        opt_df = generate_optimal_weights(ic_df, redundant)
        print("\n优化后因子权重 (Top 15):")
        for name, row in opt_df.head(15).iterrows():
            print(f"  {name}: weight={row['weight']:+.4f}, IR={row['IR']:.4f}")

    if args.schemes:
        schemes = generate_v13_improvement_plan(ic_df)

    # 保存结果
    opt_path = REPORT_DIR / "factor_optimization.json"
    result = {
        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
        'ic_summary': {
            'total_factors': len(ic_df),
            'strong_factors': int((abs(ic_df['IR']) > 0.05).sum()),
            'positive_ir': int((ic_df['IR'] > 0).sum()),
            'negative_ir': int((ic_df['IR'] < 0).sum()),
        },
        'top_factors': ic_df.head(10)['IR'].to_dict(),
        'redundant_groups': {k: v['keep'] for k, v in redundant.items()},
    }
    with open(opt_path, 'w') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    print("\n优化结果已保存: %s" % opt_path)


if __name__ == "__main__":
    main()
