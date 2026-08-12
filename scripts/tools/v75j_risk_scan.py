#!/usr/bin/env python3
"""v75j 风控参数扫描 - 简化版"""
import sys
sys.path.insert(0, "/root/a-share-quant-sim")

import numpy as np
import pandas as pd
from core.db import load_panel_from_db
from scripts.strategies.v75j_liquidity_only import calc_factors_v75j, select_stocks_v75j

print("=" * 60)
print("v75j 风控参数扫描")
print("=" * 60)

# Load data
print("\n[1] 加载数据...")
panels, codes = load_panel_from_db(
    start_date='2021-01-01', end_date='2026-06-30', pool='zz1800'
)
close, vol, amt = panels
print(f"  数据: {close.shape[0]}天 x {close.shape[1]}只股票")

# Test different parameters
print("\n[2] 测试不同参数组合...")
results = []

for sl in [-0.05, -0.08, -0.10, -0.12]:
    for tp in [0.15, 0.20, 0.25, 0.30, 0.40]:
        for hd in [10, 15, 20, 25, 30]:
            # Simple backtest
            initial_capital = 100000
            cash = initial_capital
            holdings = {}
            portfolio_values = []
            
            for i in range(252, len(close)):
                # Check stop loss / take profit / hold days
                for code in list(holdings.keys()):
                    if code in close.columns:
                        current_price = close[code].iloc[i]
                        cost = holdings[code]['cost']
                        ret = (current_price - cost) / cost
                        
                        if ret <= sl or ret >= tp or i - holdings[code]['entry_idx'] >= hd:
                            cash += holdings[code]['shares'] * current_price
                            del holdings[code]
                
                # Calculate factors
                factor_data = calc_factors_v75j(
                    close.iloc[:i+1], vol.iloc[:i+1], amt.iloc[:i+1],
                    close.iloc[:i+1], close.iloc[:i+1], close.iloc[:i+1]
                )
                
                # Select stocks
                selected = select_stocks_v75j(
                    factor_data, close.index[i], close.iloc[:i+1], vol.iloc[:i+1], 
                    amt.iloc[:i+1], close.iloc[:i+1], close.iloc[:i+1], close.iloc[:i+1],
                    {}, {}, {}
                )
                
                # Sell holdings not in selected
                for code in list(holdings.keys()):
                    if code not in selected:
                        if code in close.columns:
                            cash += holdings[code]['shares'] * close[code].iloc[i]
                            del holdings[code]
                
                # Buy selected stocks
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
            
            daily_rets = np.diff(portfolio_values) / portfolio_values[:-1]
            sharpe = np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(252) if np.std(daily_rets) > 0 else 0
            
            results.append({
                'sl': sl,
                'tp': tp,
                'hd': hd,
                'return': total_return,
                'sharpe': sharpe,
                'max_dd': max_drawdown
            })

# Print results
print("\n[3] 结果汇总:")
print(f"{'SL':<6} {'TP':<6} {'HD':<6} {'Return':<10} {'Sharpe':<8} {'MaxDD':<10}")
print("-" * 46)
for r in sorted(results, key=lambda x: x['sharpe'], reverse=True)[:20]:
    print(f"{r['sl']:<6.2f} {r['tp']:<6.2f} {r['hd']:<6} "
          f"{r['return']:<10.2%} {r['sharpe']:<8.3f} {r['max_dd']:<10.2%}")

# Save results
df = pd.DataFrame(results)
df.to_csv('/tmp/v75j_risk_scan.csv', index=False)

# Find best
best = df.loc[df['sharpe'].idxmax()]
print(f"\n[4] 最佳参数:")
print(f"  SL={best['sl']}, TP={best['tp']}, HD={best['hd']}")
print(f"  Return={best['return']:.2%}, Sharpe={best['sharpe']:.3f}, MaxDD={best['max_dd']:.2%}")

print("\n完成!")
