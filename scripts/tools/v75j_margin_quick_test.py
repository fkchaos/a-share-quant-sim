#!/usr/bin/env python3
"""v75j + 融资融券择时 - 修复版
测试融资余额变化率作为买入条件过滤器
"""
import sys
sys.path.insert(0, "/root/a-share-quant-sim")

import numpy as np
import pandas as pd
import akshare as ak
from core.db import load_panel_from_db
from scripts.strategies.v75j_liquidity_only import calc_factors_v75j, select_stocks_v75j

def get_margin_signal():
    """获取融资余额5日变化率"""
    df = ak.macro_china_market_margin_sh()
    df['日期'] = pd.to_datetime(df['日期'])
    df = df.sort_values('日期').set_index('日期')
    df['margin_5d_chg'] = df['融资余额'].pct_change(5)
    return df['margin_5d_chg']

def run_backtest_with_timing(threshold, direction='above'):
    """运行带回测的回测"""
    # Load data
    panels, codes = load_panel_from_db(
        start_date='2021-01-01', end_date='2026-06-30',
        need_open=True, need_hl=True, pool='zz1800'
    )
    close, vol, amt, opn, high, low = panels
    
    # Get margin signal
    margin_sig = get_margin_signal()
    
    # Run backtest
    initial_capital = 100000
    cash = initial_capital
    holdings = {}
    portfolio_values = []
    
    for i in range(252, len(close)):
        date = close.index[i]
        
        # Check margin signal
        if date in margin_sig.index:
            sig = margin_sig.loc[date]
            if pd.isna(sig):
                signal_active = True  # No signal, allow trading
            elif direction == 'above':
                signal_active = sig > threshold
            else:
                signal_active = sig < threshold
        else:
            signal_active = True
        
        # Check stop loss / take profit
        for code in list(holdings.keys()):
            if code in close.columns:
                current_price = close[code].iloc[i]
                cost = holdings[code]['cost']
                ret = (current_price - cost) / cost
                
                # Stop loss
                if ret <= -0.08:
                    cash += holdings[code]['shares'] * current_price
                    del holdings[code]
                    continue
                
                # Take profit
                if ret >= 0.25:
                    cash += holdings[code]['shares'] * current_price
                    del holdings[code]
                    continue
                
                # Hold days max
                if i - holdings[code]['entry_idx'] >= 20:
                    cash += holdings[code]['shares'] * current_price
                    del holdings[code]
        
        # Calculate factors
        factor_data = calc_factors_v75j(
            close.iloc[:i+1], vol.iloc[:i+1], amt.iloc[:i+1],
            high.iloc[:i+1], low.iloc[:i+1], opn.iloc[:i+1]
        )
        
        # Select stocks (only if signal is active)
        if signal_active:
            selected = select_stocks_v75j(factor_data, codes, {})
        else:
            selected = []
        
        # Sell holdings not in selected
        for code in list(holdings.keys()):
            if code not in selected:
                if code in close.columns:
                    cash += holdings[code]['shares'] * close[code].iloc[i]
                    del holdings[code]
        
        # Buy selected stocks (equal weight)
        if selected and len(holdings) < 3:
            for code in selected[:3-len(holdings)]:
                if code not in holdings and code in close.columns:
                    price = close[code].iloc[i]
                    if price > 0:
                        buy_amount = min(cash / (3 - len(holdings)), 30000)
                        shares = int(buy_amount / price / 100) * 100
                        if shares > 0:
                            holdings[code] = {
                                'shares': shares,
                                'cost': price,
                                'entry_idx': i
                            }
                            cash -= shares * price
        
        # Calculate portfolio value
        portfolio_value = cash
        for code, h in holdings.items():
            if code in close.columns:
                portfolio_value += h['shares'] * close[code].iloc[i]
        
        portfolio_values.append(portfolio_value)
    
    # Calculate metrics
    total_return = (portfolio_values[-1] - initial_capital) / initial_capital
    peak = np.maximum.accumulate(portfolio_values)
    drawdown = (np.array(portfolio_values) - peak) / peak
    max_drawdown = drawdown.min()
    
    # Daily returns
    daily_rets = np.diff(portfolio_values) / portfolio_values[:-1]
    sharpe = np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252) if np.std(daily_rets) > 0 else 0
    
    return {
        'total_return': total_return,
        'max_drawdown': max_drawdown,
        'sharpe': sharpe
    }

# Test different thresholds
print("Testing margin timing signals...")
results = []

# Test baseline (no filter)
print("  [Baseline] No filter...")
baseline = run_backtest_with_timing(0, 'above')  # Will use signal_active=True for all
results.append({
    'threshold': 'baseline',
    'direction': 'N/A',
    **baseline
})
print(f"    Return={baseline['total_return']:.2%}, Sharpe={baseline['sharpe']:.3f}, MaxDD={baseline['max_drawdown']:.2%}")

# Test different thresholds
for threshold in [-0.03, -0.01, 0.00, 0.01, 0.03]:
    for direction in ['above', 'below']:
        try:
            result = run_backtest_with_timing(threshold, direction)
            results.append({
                'threshold': threshold,
                'direction': direction,
                **result
            })
            print(f"  threshold={threshold}, direction={direction}: "
                  f"Return={result['total_return']:.2%}, "
                  f"Sharpe={result['sharpe']:.3f}, "
                  f"MaxDD={result['max_drawdown']:.2%}")
        except Exception as e:
            print(f"  threshold={threshold}, direction={direction}: Error={e}")

# Save results
df = pd.DataFrame(results)
df.to_csv('/tmp/v75j_margin_test.csv', index=False)

# Find best
best = df.loc[df['sharpe'].idxmax()]
print(f"\n=== Best ===")
print(f"Threshold={best['threshold']}, Direction={best['direction']}")
print(f"Sharpe={best['sharpe']:.3f}, Return={best['total_return']:.2%}, MaxDD={best['max_drawdown']:.2%}")

print("\nDone!")
