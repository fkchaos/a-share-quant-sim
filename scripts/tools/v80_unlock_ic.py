#!/usr/bin/env python3
"""解禁事件因子IC分析

因子定义：过去N日内，解禁市值占流通市值比例
逻辑：解禁前利空压股价，解禁后利空出尽可能反弹
"""
import sys
sys.path.insert(0, "/root/a-share-quant-sim")

import numpy as np
import pandas as pd
import sqlite3
from scipy.stats import spearmanr
from core.db import load_panel_from_db


def load_unlock_data():
    """加载解禁数据"""
    conn = sqlite3.connect('data/quant_stocks.db')
    df = pd.read_sql_query(
        "SELECT code, unlock_date, unlock_market_value, pct_of_circulating FROM unlock_events",
        conn
    )
    conn.close()
    df['unlock_date'] = pd.to_datetime(df['unlock_date'])
    return df


def calc_unlock_factor(unlock_df, close_panel, window=20):
    """计算解禁因子

    因子 = 过去N日解禁市值占流通市值比例的累计值
    解禁比例越高，利空越大，但如果已解禁完可能反弹
    """
    dates = close_panel.index
    codes = close_panel.columns
    result = pd.DataFrame(np.nan, index=dates, columns=codes)

    for i in range(window, len(dates)):
        date = dates[i]
        start_date = dates[i - window]

        # 窗口内的解禁
        mask = (unlock_df['unlock_date'] >= start_date) & (unlock_df['unlock_date'] <= date)
        window_data = unlock_df[mask]

        if len(window_data) == 0:
            continue

        # 按股票汇总解禁比例
        unlock_pct = window_data.groupby('code')['pct_of_circulating'].sum()

        valid_codes = unlock_pct.index.intersection(codes)
        if len(valid_codes) > 0:
            result.iloc[i][valid_codes] = unlock_pct[valid_codes]

    return result


def run_ic_analysis():
    """计算IC、IR"""
    print("=" * 60)
    print("解禁事件因子 IC分析")
    print("=" * 60)

    # 加载数据
    print("\n[1] 加载数据...")
    panels, codes = load_panel_from_db(
        start_date='2020-01-01', end_date='2026-06-30',
        need_open=True, need_hl=True, pool='zz1800'
    )
    close, vol, amt, opn, high, low = panels
    print(f"  行情数据: {close.shape[0]}天 x {close.shape[1]}只股票")

    unlock_df = load_unlock_data()
    print(f"  解禁数据: {len(unlock_df)}条")

    # 计算未来5日收益
    print("\n[2] 计算未来5日收益...")
    fwd_ret = close.pct_change(5, fill_method=None).shift(-5)

    # 计算因子
    print("\n[3] 计算因子...")
    factor_panel = calc_unlock_factor(unlock_df, close, window=20)

    # 逐日计算IC
    print("\n[4] 逐日计算IC...")
    dates = close.index
    ic_list = []

    for i in range(30, len(dates) - 5):
        date = dates[i]

        factor_vals = factor_panel.iloc[i]
        future_ret = fwd_ret.iloc[i]

        common = factor_vals.dropna().index.intersection(future_ret.dropna().index)
        if len(common) < 30:
            continue

        f = factor_vals[common].values
        r = future_ret[common].values

        ic, _ = spearmanr(f, r)
        if not np.isnan(ic):
            ic_list.append({'date': date, 'ic': ic})

    ic_df = pd.DataFrame(ic_list)
    if len(ic_df) == 0:
        print("  ❌ 有效样本不足")
        return

    ic_df.set_index('date', inplace=True)

    # 统计
    print("\n[5] IC统计:")
    ic_mean = ic_df['ic'].mean()
    ic_std = ic_df['ic'].std()
    ir = ic_mean / ic_std if ic_std > 0 else 0
    ic_pos_pct = (ic_df['ic'] > 0).mean()

    print(f"  IC均值: {ic_mean:.4f}")
    print(f"  IC标准差: {ic_std:.4f}")
    print(f"  IR: {ir:.4f}")
    print(f"  IC>0比例: {ic_pos_pct:.2%}")

    # 判定
    print("\n[6] 判定:")
    if abs(ic_mean) > 0.03 and abs(ir) > 0.3 and (ic_pos_pct > 0.6 or ic_pos_pct < 0.4):
        print("  ✅ 有效因子，进入WF验证")
        verdict = "PASS"
    elif abs(ic_mean) < 0.01 or abs(ir) < 0.1:
        print("  ❌ 证伪，不进入WF")
        verdict = "FAIL"
    else:
        print("  ⚠️ 微弱信号，不值得投入WF时间")
        verdict = "MARGINAL"

    # 分年IC
    print("\n[7] 分年IC:")
    ic_df['year'] = ic_df.index.year
    yearly = ic_df.groupby('year')['ic'].agg(['mean', 'std'])
    yearly['ir'] = yearly['mean'] / yearly['std']
    print(yearly.to_string())

    # 保存结果
    result_file = "/tmp/v80_unlock_ic_analysis.txt"
    with open(result_file, 'w') as f:
        f.write("解禁事件因子 IC分析结果\n")
        f.write("=" * 40 + "\n\n")
        f.write(f"IC均值: {ic_mean:.4f}\n")
        f.write(f"IC标准差: {ic_std:.4f}\n")
        f.write(f"IR: {ir:.4f}\n")
        f.write(f"IC>0比例: {ic_pos_pct:.2%}\n")
        f.write(f"判定: {verdict}\n\n")
        f.write(f"分年IC:\n{yearly.to_string()}\n")
    print(f"\n结果已保存: {result_file}")

    return verdict


if __name__ == "__main__":
    run_ic_analysis()
