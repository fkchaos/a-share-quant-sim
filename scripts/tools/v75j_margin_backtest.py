#!/usr/bin/env python3
"""v75j + 融资融券择时回测
测试融资余额变化率作为买入条件过滤器
"""
import sys
sys.path.insert(0, "/root/a-share-quant-sim")

import numpy as np
import pandas as pd
import akshare as ak
from core.db import load_panel_from_db, get_stock_name_map
from scripts.strategies.v75j_liquidity_only import calc_factors_v75j

def get_margin_signal():
    """获取融资融券信号（5日变化率）"""
    df = ak.macro_china_market_margin_sh()
    df['date'] = pd.to_datetime(df['日期'])
    df = df.sort_values('date').reset_index(drop=True)
    df['margin_5d_chg'] = df['融资余额'].pct_change(5)
    return df.set_index('date')['margin_5d_chg']

def backtest_v75j_with_margin(close, vol, amt, opn, high, low, codes, 
                               margin_signal, threshold=0.0, direction='above'):
    """简化回测：只测试选股信号质量"""
    
    # Get factor scores for each date
    dates = close.index
    
    # Align margin signal with trading dates
    margin_aligned = margin_signal.reindex(dates, method='ffill')
    
    total_signals = 0
    valid_signals = 0
    
    # Track portfolio
    portfolio_value = 100000.0
    portfolio_history = []
    
    for i in range(60, len(dates) - 5):
        date = dates[i]
        
        # Check margin signal
        sig = margin_aligned.get(date, np.nan)
        if pd.isna(sig):
            continue
        
        # Apply filter
        if direction == 'above' and sig < threshold:
            continue
        if direction == 'below' and sig > threshold:
            continue
        
        total_signals += 1
        
        # Calculate factors
        try:
            factors = calc_factors_v75j(
                close.iloc[:i+1], vol.iloc[:i+1], amt.iloc[:i+1],
                high.iloc[:i+1], low.iloc[:i+1], opn.iloc[:i+1]
            )
            
            # Get top stocks
            factor_values = factors.get('v75j_liquidity', pd.Series())
            if len(factor_values) < 10:
                continue
            
            # Select top 3
            top_stocks = factor_values.nsmallest(3).index
            
            # Calculate 5-day forward return
            fwd_ret = close.iloc[i+5] / close.iloc[i] - 1
            selected_ret = fwd_ret[top_stocks].mean()
            
            valid_signals += 1
            portfolio_value *= (1 + selected_ret)
            portfolio_history.append({
                'date': date,
                'return': selected_ret,
                'portfolio': portfolio_value,
                'margin_sig': sig
            })
            
        except Exception as e:
            continue
    
    return pd.DataFrame(portfolio_history), total_signals, valid_signals

if __name__ == "__main__":
    print("=" * 60)
    print("v75j + 融资融券择时回测")
    print("=" * 60)
    
    # Load stock data
    print("\n[1] 加载股票数据...")
    panels, codes = load_panel_from_db(
        start_date='2020-01-01', end_date='2026-06-30',
        need_open=True, need_hl=True, pool='zz1800'
    )
    close, vol, amt, opn, high, low = panels
    print(f"  数据: {close.shape[0]}天 x {close.shape[1]}只股票")
    
    # Get margin signal
    print("\n[2] 获取融资融券信号...")
    margin_signal = get_margin_signal()
    print(f"  数据: {len(margin_signal)}天")
    
    # Test different thresholds
    print("\n[3] 测试不同阈值...")
    
    results = []
    
    # Test baseline (no filter)
    print("\n  [基准] 无择时过滤...")
    hist_base, total_base, valid_base = backtest_v75j_with_margin(
        close, vol, amt, opn, high, low, codes, margin_signal, 
        threshold=999, direction='above'  # Always pass
    )
    if len(hist_base) > 0:
        total_ret_base = hist_base['portfolio'].iloc[-1] / 100000 - 1
        sharpe_base = hist_base['return'].mean() / hist_base['return'].std() * np.sqrt(252) if hist_base['return'].std() > 0 else 0
        results.append({
            'config': 'baseline (no filter)',
            'signals': total_base,
            'valid': valid_base,
            'total_return': total_ret_base,
            'sharpe': sharpe_base
        })
        print(f"    信号数: {valid_base}, 总收益: {total_ret_base:.1%}, Sharpe: {sharpe_base:.3f}")
    
    # Test with margin filter
    thresholds = [0.0, 0.01, 0.03, -0.01, -0.03]
    for th in thresholds:
        if th >= 0:
            direction = 'above'
            label = f'margin_5d > {th:.2f}'
        else:
            direction = 'below'
            label = f'margin_5d < {th:.2f}'
        
        print(f"\n  [{label}]...")
        hist, total, valid = backtest_v75j_with_margin(
            close, vol, amt, opn, high, low, codes, margin_signal,
            threshold=th, direction=direction
        )
        
        if len(hist) > 0:
            total_ret = hist['portfolio'].iloc[-1] / 100000 - 1
            sharpe = hist['return'].mean() / hist['return'].std() * np.sqrt(252) if hist['return'].std() > 0 else 0
            results.append({
                'config': label,
                'signals': total,
                'valid': valid,
                'total_return': total_ret,
                'sharpe': sharpe
            })
            print(f"    信号数: {valid}, 总收益: {total_ret:.1%}, Sharpe: {sharpe:.3f}")
    
    # Summary
    print("\n" + "=" * 60)
    print("结果汇总")
    print("=" * 60)
    df_results = pd.DataFrame(results)
    print(df_results.to_string(index=False))
    
    # Save results
    with open('/tmp/v75j_margin_timing_results.txt', 'w') as f:
        f.write("v75j + 融资融券择时回测结果\n")
        f.write("=" * 60 + "\n\n")
        f.write(df_results.to_string(index=False))
    print(f"\n结果已保存到: /tmp/v75j_margin_timing_results.txt")
