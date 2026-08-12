#!/usr/bin/env python3
"""市场级融资融券择时信号测试
测试融资余额变化率作为v75j的择时过滤器
"""
import sys
sys.path.insert(0, "/root/a-share-quant-sim")

import numpy as np
import pandas as pd
import akshare as ak
from core.db import load_panel_from_db
from scripts.strategies.v75j_liquidity_only import calc_factors_v75j, select_stocks_v75j

def get_margin_data():
    """获取融资融券市场数据"""
    df = ak.macro_china_market_margin_sh()
    df['date'] = pd.to_datetime(df['日期'])
    df = df.sort_values('date').reset_index(drop=True)
    df = df.rename(columns={'融资余额': 'margin_balance', '融资买入额': 'margin_buy'})
    df['margin_5d_chg'] = df['margin_balance'].pct_change(5)
    df['margin_20d_chg'] = df['margin_balance'].pct_change(20)
    return df[['date', 'margin_balance', 'margin_5d_chg', 'margin_20d_chg']].set_index('date')

def run_backtest_with_timing(close, vol, amt, opn, high, low, codes, margin_df, threshold, direction='above'):
    """运行带回测择时的回测"""
    from scripts.backtest.strategy_adapter import StrategyAdapter
    
    adapter = StrategyAdapter('v75j')
    
    # Calculate factors
    factors = calc_factors_v75j(close, vol, amt, high, low, opn)
    
    # Get trading dates
    dates = close.index.tolist()
    
    # Filter dates where margin signal is valid
    valid_dates = []
    for d in dates:
        d_ts = pd.Timestamp(d)
        if d_ts in margin_df.index:
            sig = margin_df.loc[d_ts, 'margin_5d_chg']
            if pd.notna(sig):
                if direction == 'above' and sig > threshold:
                    valid_dates.append(d)
                elif direction == 'below' and sig < threshold:
                    valid_dates.append(d)
    
    # Run backtest on valid dates only
    from scripts.backtest.wf_runner import run_single_backtest
    
    results = []
    for d in valid_dates:
        try:
            selected = select_stocks_v75j(
                close.loc[:d], vol.loc[:d], amt.loc[:d],
                high.loc[:d], low.loc[:d], opn.loc[:d],
                adapter.params, adapter
            )
            if selected:
                results.append({'date': d, 'stocks': selected})
        except Exception as e:
            continue
    
    return results

if __name__ == "__main__":
    print("=" * 60)
    print("市场级融资融券择时信号测试")
    print("=" * 60)
    
    # Load data
    print("\n[1] 加载数据...")
    panels, codes = load_panel_from_db(
        start_date='2020-01-01', end_date='2026-06-30',
        need_open=True, need_hl=True, pool='zz1800'
    )
    close, vol, amt, opn, high, low = panels
    print(f"  数据: {close.shape[0]}天 x {close.shape[1]}只股票")
    
    # Get margin data
    print("\n[2] 获取融资融券数据...")
    margin_df = get_margin_data()
    print(f"  数据: {len(margin_df)}天")
    print(f"  日期范围: {margin_df.index.min()} to {margin_df.index.max()}")
    
    # Test different thresholds
    print("\n[3] 测试不同阈值...")
    thresholds = [-0.05, -0.03, -0.01, 0, 0.01, 0.03, 0.05]
    
    results = []
    for th in thresholds:
        print(f"\n  测试 threshold={th:.2f} (margin_5d_chg {'>' if th >= 0 else '<'} {th:.2f})...")
        
        # Count valid dates
        if th >= 0:
            valid_mask = margin_df['margin_5d_chg'] > th
        else:
            valid_mask = margin_df['margin_5d_chg'] < th
        
        valid_dates = margin_df[valid_mask].index
        print(f"    有效交易日: {len(valid_dates)} / {len(margin_df)}")
        
        results.append({
            'threshold': th,
            'valid_days': len(valid_dates),
            'total_days': len(margin_df),
            'coverage': len(valid_dates) / len(margin_df)
        })
    
    # Summary
    print("\n[4] 结果汇总:")
    df_results = pd.DataFrame(results)
    print(df_results.to_string(index=False))
    
    print("\n完成！")
