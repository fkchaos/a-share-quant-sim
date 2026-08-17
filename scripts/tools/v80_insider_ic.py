#!/usr/bin/env python3
"""高管增减持因子IC分析

因子定义：过去N日内，高管净买入股数占流通股比例
逻辑：高管净增持 = 内部人看好，未来可能上涨
"""
import sys
sys.path.insert(0, "/root/a-share-quant-sim")

import numpy as np
import pandas as pd
import sqlite3
from scipy.stats import spearmanr
from core.db import load_panel_from_db


def load_insider_data():
    """加载高管增减持数据"""
    conn = sqlite3.connect('data/quant_stocks.db')
    df = pd.read_sql_query(
        "SELECT code, trade_date, change_shares, method FROM insider_trades",
        conn
    )
    conn.close()
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    return df


def calc_insider_factor(insider_df, close_panel, vol_panel, window=20):
    """计算高管增减持因子

    因子 = 过去N日净增持股数 / 成交量均值
    净增持 = SUM(change_shares > 0) - SUM(change_shares < 0)
    """
    dates = close_panel.index
    codes = close_panel.columns
    result = pd.DataFrame(np.nan, index=dates, columns=codes)

    # 筛选有意义的交易方法（排除分红送转、新股申购等）
    valid_methods = ['竞价交易', '二级市场买卖', '大宗交易', '大宗交易平台',
                     '二级市场', '盘后定价']
    insider_df = insider_df[insider_df['method'].isin(valid_methods)].copy()

    for i in range(window, len(dates)):
        date = dates[i]
        start_date = dates[i - window]

        # 窗口内的增减持
        mask = (insider_df['trade_date'] >= start_date) & (insider_df['trade_date'] <= date)
        window_data = insider_df[mask]

        if len(window_data) == 0:
            continue

        # 按股票汇总净增持
        net_buy = window_data.groupby('code')['change_shares'].sum()

        # 只保留有数据的股票
        valid_codes = net_buy.index.intersection(codes)
        if len(valid_codes) == 0:
            continue

        # 成交量均值（用于标准化）
        vol_mean = vol_panel.iloc[i-window:i+1][valid_codes].mean()

        # 因子值 = 净增持 / 成交量均值
        factor_vals = net_buy[valid_codes] / vol_mean.replace(0, np.nan)
        result.iloc[i][valid_codes] = factor_vals

    return result


def run_ic_analysis():
    """计算IC、IR"""
    print("=" * 60)
    print("高管增减持因子 IC分析")
    print("=" * 60)

    # 加载数据
    print("\n[1] 加载数据...")
    panels, codes = load_panel_from_db(
        start_date='2020-01-01', end_date='2026-06-30',
        need_open=True, need_hl=True, pool='zz1800'
    )
    close, vol, amt, opn, high, low = panels
    print(f"  行情数据: {close.shape[0]}天 x {close.shape[1]}只股票")

    insider_df = load_insider_data()
    print(f"  增减持数据: {len(insider_df)}条")

    # 计算未来5日收益
    print("\n[2] 计算未来5日收益...")
    fwd_ret = close.pct_change(5, fill_method=None).shift(-5)

    # 计算因子
    print("\n[3] 计算因子...")
    factor_panel = calc_insider_factor(insider_df, close, vol, window=20)

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
        print("  ❌ 有效样本不足，无法计算IC")
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
    result_file = "/tmp/v80_insider_ic_analysis.txt"
    with open(result_file, 'w') as f:
        f.write("高管增减持因子 IC分析结果\n")
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
