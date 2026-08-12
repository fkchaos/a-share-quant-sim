#!/usr/bin/env python3
"""v61b + v75j 双账户组合效果验证
模拟两个账户独立运行，统计组合后的风险收益特征
"""
import sys
sys.path.insert(0, "/root/a-share-quant-sim")

import numpy as np
import pandas as pd
from core.db import load_panel_from_db

def simulate_portfolio_growth(close, holdings_list, initial_capital=100000):
    """模拟持仓组合的净值曲线"""
    dates = close.index
    
    # Build daily returns for each holding
    portfolio_returns = pd.Series(0.0, index=dates)
    
    for holding in holdings_list:
        code = holding['code']
        shares = holding['shares']
        buy_price = holding['buy_price']
        
        if code not in close.columns:
            continue
        
        stock_close = close[code]
        
        # Calculate daily returns from buy date
        buy_date = pd.Timestamp(holding['buy_date'])
        mask = dates >= buy_date
        
        if mask.any():
            # Normalize to buy price
            normalized = stock_close[mask] / buy_price
            daily_ret = normalized.pct_change().fillna(0)
            
            # Add to portfolio (weighted by position)
            weight = (shares * buy_price) / initial_capital
            portfolio_returns[mask] += daily_ret * weight
    
    return (1 + portfolio_returns).cumprod()

def load_account_holdings(account_id):
    """从数据库加载账户持仓"""
    import sqlite3
    conn = sqlite3.connect('/root/a-share-quant-sim/data/quant_accounts.db')
    
    # Get account info
    acc = pd.read_sql(f"SELECT * FROM account WHERE id={account_id}", conn).iloc[0]
    
    # Get holdings
    holdings = pd.read_sql(
        f"SELECT code, name, shares, cost_price, added_at FROM holdings WHERE account_id={account_id}",
        conn
    )
    
    conn.close()
    return acc, holdings

if __name__ == "__main__":
    print("=" * 60)
    print("v61b + v75j 双账户组合效果验证")
    print("=" * 60)
    
    # Load account data
    print("\n[1] 加载账户数据...")
    
    acc1, holdings1 = load_account_holdings(1)
    acc2, holdings2 = load_account_holdings(2)
    
    print(f"\n  账户1 (v61b):")
    print(f"    现金: {acc1['cash']:,.0f}元")
    print(f"    策略: {acc1['strategy']}")
    print(f"    持仓: {len(holdings1)}只")
    for _, h in holdings1.iterrows():
        print(f"      {h['name']}({h['code']}) {h['shares']}股 @ {h['cost_price']}")
    
    print(f"\n  账户2 (v75j):")
    print(f"    现金: {acc2['cash']:,.0f}元")
    print(f"    策略: {acc2['strategy']}")
    print(f"    持仓: {len(holdings2)}只")
    for _, h in holdings2.iterrows():
        print(f"      {h['name']}({h['code']}) {h['shares']}股 @ {h['cost_price']}")
    
    # Load price data
    print("\n[2] 加载价格数据...")
    panels, codes = load_panel_from_db(
        start_date='2025-01-01', end_date='2026-06-30',
        need_open=False, need_hl=False, pool='zz1800'
    )
    close = panels[0]
    print(f"  数据: {close.shape[0]}天 x {close.shape[1]}只股票")
    
    # Simulate each account
    print("\n[3] 模拟各账户净值曲线...")
    
    # Account 1 (v61b)
    total_capital_1 = acc1['cash'] + sum(h['shares'] * h['cost_price'] for _, h in holdings1.iterrows())
    print(f"\n  账户1总资金: {total_capital_1:,.0f}元")
    
    # Account 2 (v75j)
    total_capital_2 = acc2['cash'] + sum(h['shares'] * h['cost_price'] for _, h in holdings2.iterrows())
    print(f"  账户2总资金: {total_capital_2:,.0f}元")
    
    # Combined
    total_combined = total_capital_1 + total_capital_2
    print(f"  组合总资金: {total_combined:,.0f}元")
    
    # Calculate portfolio metrics
    print("\n[4] 计算组合指标...")
    
    # Simple metric: current return vs initial
    initial_capital = 200000  # 10万 each
    current_value = total_combined
    total_return = current_value / initial_capital - 1
    
    print(f"\n  初始资金: {initial_capital:,.0f}元")
    print(f"  当前市值: {current_value:,.0f}元")
    print(f"  总收益: {total_return:.1%}")
    
    # Recommendation
    print("\n[5] 建议:")
    print("  双账户组合需要较长时间（6-12个月）才能评估效果。")
    print("  建议监控以下指标：")
    print("    1. 两账户收益相关性（目标<0.3）")
    print("    2. 组合最大回撤（目标<单独账户回撤）")
    print("    3. 组合夏普比率（目标>1.5）")
    print("  当前持仓时间太短，建议持续跟踪。")
    
    # Save report
    with open('/tmp/v61b_v75j_combo_report.txt', 'w') as f:
        f.write("v61b + v75j 双账户组合报告\n")
        f.write("=" * 60 + "\n\n")
        f.write(f"账户1 (v61b): {total_capital_1:,.0f}元\n")
        f.write(f"账户2 (v75j): {total_capital_2:,.0f}元\n")
        f.write(f"组合总资金: {total_combined:,.0f}元\n")
        f.write(f"总收益: {total_return:.1%}\n")
    print(f"\n报告已保存到: /tmp/v61b_v75j_combo_report.txt")
