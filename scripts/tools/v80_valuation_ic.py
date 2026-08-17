#!/usr/bin/env python3
"""估值因子IC分析（PE/PB）

读取已采集的估值数据，计算IC。
数据源：data/external/valuation_daily.csv
"""
import sys
sys.path.insert(0, "/root/a-share-quant-sim")

import numpy as np
import pandas as pd
import sqlite3
from scipy.stats import spearmanr


def load_panels():
    """加载价格面板和估值数据"""
    print("[1] 加载数据...")

    # 价格面板
    from core.db import load_panel_from_db
    panels, codes = load_panel_from_db(
        start_date='2020-01-01', end_date='2026-06-30',
        need_open=False, need_hl=False, pool='zz1800'
    )
    close = panels[0]
    print(f"  价格: {close.shape[0]}天 x {close.shape[1]}只")

    # 估值数据
    val = pd.read_csv("data/external/valuation_daily.csv")
    val['date'] = pd.to_datetime(val['date'])
    print(f"  估值: {len(val)}行, {val['code'].nunique()}只股票")

    return close, val


def build_factor_panel(close, val, factor_col):
    """构建因子面板（与close对齐）"""
    # 取每个股票每个日期的因子值
    pivot = val.pivot_table(index='date', columns='code', values=factor_col)
    # 对齐到close的日期和股票
    pivot = pivot.reindex(index=close.index, columns=close.columns)
    return pivot


def calc_ic_series(factor_panel, fwd_ret):
    """计算逐日IC"""
    dates = factor_panel.index
    ic_list = []

    for i in range(30, len(dates) - 5):
        date = dates[i]
        f = factor_panel.iloc[i]
        r = fwd_ret.iloc[i]

        common = f.dropna().index.intersection(r.dropna().index)
        if len(common) < 100:
            continue

        ic, _ = spearmanr(f[common].values, r[common].values)
        if not np.isnan(ic):
            ic_list.append({'date': date, 'ic': ic})

    return pd.DataFrame(ic_list).set_index('date')


def analyze_ic(ic_df, name):
    """分析IC并输出"""
    ic_mean = ic_df['ic'].mean()
    ic_std = ic_df['ic'].std()
    ir = ic_mean / ic_std if ic_std > 0 else 0
    pct_pos = (ic_df['ic'] > 0).mean()

    print(f"\n  【{name}】")
    print(f"    IC均值: {ic_mean:.4f}")
    print(f"    IR: {ir:.4f}")
    print(f"    IC>0比例: {pct_pos:.2%}")

    # 判定
    if abs(ic_mean) > 0.03 and abs(ir) > 0.3 and (pct_pos > 0.6 or pct_pos < 0.4):
        verdict = "✅ PASS"
    elif abs(ic_mean) < 0.01 or abs(ir) < 0.1:
        verdict = "❌ FAIL"
    else:
        verdict = "⚠️ MARGINAL"
    print(f"    判定: {verdict}")

    # 分年
    ic_df['year'] = ic_df.index.year
    yearly = ic_df.groupby('year')['ic'].agg(['mean', 'std'])
    yearly['ir'] = yearly['mean'] / yearly['std']
    print(f"\n    分年IC:")
    print(yearly.to_string().indent(4))

    return {'name': name, 'ic_mean': ic_mean, 'ir': ir, 'pct_pos': pct_pos, 'verdict': verdict}


def main():
    print("=" * 60)
    print("估值因子IC分析（PE/PB）")
    print("=" * 60)

    close, val = load_panels()

    # 计算未来5日收益
    print("\n[2] 计算未来5日收益...")
    fwd_ret = close.pct_change(5, fill_method=None).shift(-5)

    # 构建因子面板
    print("\n[3] 构建因子面板...")
    pe_panel = build_factor_panel(close, val, 'pe_ttm')
    pb_panel = build_factor_panel(close, val, 'pb')

    # PE因子取倒数（低PE = 便宜）
    pe_inv_panel = 1.0 / pe_panel.replace(0, np.nan)

    # 计算IC
    print("\n[4] 计算IC...")
    results = []

    for name, panel in [('PE_TTM', pe_panel), ('PE倒数(低PE好)', pe_inv_panel), ('PB', pb_panel)]:
        ic_df = calc_ic_series(panel, fwd_ret)
        if len(ic_df) > 0:
            r = analyze_ic(ic_df, name)
            results.append(r)

    # 汇总
    print("\n" + "=" * 60)
    print("汇总")
    print("=" * 60)
    print(f"{'因子':<20} {'IC均值':>8} {'IR':>8} {'判定':<10}")
    print("-" * 50)
    for r in results:
        print(f"{r['name']:<20} {r['ic_mean']:>8.4f} {r['ir']:>8.4f} {r['verdict']:<10}")

    # 保存
    with open("/tmp/v80_valuation_ic.txt", "w") as f:
        for r in results:
            f.write(f"{r['name']}: IC={r['ic_mean']:.4f}, IR={r['ir']:.4f}, {r['verdict']}\n")
    print("\n结果保存: /tmp/v80_valuation_ic.txt")


if __name__ == "__main__":
    main()
