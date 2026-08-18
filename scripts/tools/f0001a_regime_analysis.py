#!/usr/bin/env python3
"""f0001a 分Regime IC分析"""
import sys
sys.path.insert(0, '/root/a-share-quant-sim')

import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from core.db import load_panel_from_db

def calc_f0001a(close, open_p):
    prev_close = close.shift(1)
    overnight = open_p / prev_close - 1.0
    intraday = close / open_p - 1.0
    return overnight - intraday

def main():
    print("加载数据...")
    panels, codes = load_panel_from_db(
        start_date='2020-01-01', end_date='2026-06-30',
        need_open=True, pool='zz1800'
    )
    close, vol, amt, open_p = panels
    print(f"数据: {close.shape[0]}天 x {close.shape[1]}只股票")

    # 计算因子
    print("计算f0001a因子...")
    factor = calc_f0001a(close, open_p)

    # 未来5日收益
    fwd_ret = close.pct_change(5).shift(-5)

    # 逐日计算RankIC
    print("计算RankIC...")
    ic_list = []
    dates = factor.index[20:]
    for date in dates:
        f = factor.loc[date].dropna()
        r = fwd_ret.loc[date].dropna()
        common = f.index.intersection(r.index)
        if len(common) < 50:
            continue
        ic, _ = spearmanr(f[common], r[common])
        ic_list.append({'date': date, 'ic': ic})

    ic_df = pd.DataFrame(ic_list).set_index('date')
    print(f"IC序列长度: {len(ic_df)}天")

    # 计算广度regime（MA20）
    print("计算广度regime...")
    dates_list = sorted(close.index)
    breadth = []
    for date in dates_list:
        close_today = close.loc[date].dropna()
        idx = dates_list.index(date)
        if idx == 0:
            breadth.append(np.nan)
            continue
        close_prev = close.loc[dates_list[idx-1]].dropna()
        common = close_today.index.intersection(close_prev.index)
        if len(common) < 100:
            breadth.append(np.nan)
            continue
        ret = close_today[common] / close_prev[common] - 1
        adv = (ret > 0).sum()
        dec = (ret < 0).sum()
        breadth.append((adv - dec) / len(common))

    breadth_series = pd.Series(breadth, index=dates_list)
    breadth_ma20 = breadth_series.rolling(20).mean()

    # 分regime计算IC
    print("分regime计算IC...")
    test_start = pd.Timestamp('2021-01-01')
    ic_test = ic_df[ic_df.index >= test_start]

    risk_on_ics = []
    risk_off_ics = []

    for date in ic_test.index:
        ic = ic_test.loc[date, 'ic']
        b_val = breadth_ma20.get(date, np.nan)
        if np.isnan(b_val):
            continue
        if b_val > 0:
            risk_on_ics.append(ic)
        else:
            risk_off_ics.append(ic)

    # 输出结果
    print("\n=== 分Regime IC分析结果 ===")
    if risk_on_ics:
        arr = np.array(risk_on_ics)
        print(f"risk_on:  IC均值={np.mean(arr):.4f}, IR={np.mean(arr)/np.std(arr):.4f}, 样本={len(risk_on_ics)}天")
    else:
        print("risk_on: 无样本")

    if risk_off_ics:
        arr = np.array(risk_off_ics)
        print(f"risk_off: IC均值={np.mean(arr):.4f}, IR={np.mean(arr)/np.std(arr):.4f}, 样本={len(risk_off_ics)}天")
    else:
        print("risk_off: 无样本")

    all_ics = risk_on_ics + risk_off_ics
    if all_ics:
        arr = np.array(all_ics)
        print(f"all:      IC均值={np.mean(arr):.4f}, IR={np.mean(arr)/np.std(arr):.4f}, 样本={len(all_ics)}天")

    # 保存结果
    import json
    result = {
        'risk_on': {'ic_mean': float(np.mean(risk_on_ics)) if risk_on_ics else None, 'n': len(risk_on_ics)},
        'risk_off': {'ic_mean': float(np.mean(risk_off_ics)) if risk_off_ics else None, 'n': len(risk_off_ics)},
        'all': {'ic_mean': float(np.mean(all_ics)) if all_ics else None, 'n': len(all_ics)}
    }
    with open('/tmp/f0001a_regime.json', 'w') as f:
        json.dump(result, f, indent=2)
    print(f"\n结果已保存: /tmp/f0001a_regime.json")

if __name__ == '__main__':
    main()
