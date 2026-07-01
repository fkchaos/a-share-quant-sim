#!/usr/bin/env python3
"""舆情因子IC分析脚本"""

import sqlite3
import pandas as pd
import numpy as np
from scipy.stats import spearmanr
from datetime import datetime, timedelta
import sys
import os

# 添加项目路径
sys.path.insert(0, '/root/a-share-quant-sim')

from core.sentiment.factor_builder import SentimentFactorBuilder


def load_price_data(codes, start_date, end_date):
    """加载价格数据"""
    conn = sqlite3.connect('data/quant_stocks.db', timeout=15)
    
    placeholders = ','.join(['?' for _ in codes])
    df = pd.read_sql_query(f'''
        SELECT code, date, close
        FROM daily_kline
        WHERE code IN ({placeholders})
        AND date BETWEEN ? AND ?
        ORDER BY code, date
    ''', conn, params=codes + [start_date, end_date])
    
    conn.close()
    
    # 转换为面板
    df['date'] = pd.to_datetime(df['date'])
    pivot = df.pivot(index='date', columns='code', values='close')
    
    return pivot


def calc_forward_returns(price_panel, periods=[1, 3, 5]):
    """计算未来收益率"""
    results = {}
    for p in periods:
        results[f'fwd_ret_{p}d'] = price_panel.pct_change(p).shift(-p)
    return results


def calc_ic(factor_values, forward_returns):
    """计算Rank IC"""
    ic_list = []
    
    for date in factor_values.index:
        if date not in forward_returns.index:
            continue
        
        fv = factor_values.loc[date].dropna()
        fr = forward_returns.loc[date].dropna()
        
        common = list(set(fv.index) & set(fr.index))
        if len(common) < 10:
            continue
        
        ic, _ = spearmanr(fv[common], fr[common])
        ic_list.append(ic)
    
    if not ic_list:
        return 0.0, 0.0, 0
    
    ic_mean = np.mean(ic_list)
    ic_std = np.std(ic_list)
    ir = ic_mean / ic_std if ic_std > 0 else 0
    
    return ic_mean, ir, len(ic_list)


def main():
    print("="*60)
    print("舆情因子IC分析")
    print("="*60)
    print()
    
    # 参数
    start_date = '2024-01-01'
    end_date = '2026-06-30'
    
    # 获取股票池
    conn = sqlite3.connect('data/quant_stocks.db', timeout=15)
    codes_df = conn.execute('SELECT code FROM stock_pool_zz1800 LIMIT 100').fetchall()
    codes = [row[0] for row in codes_df]
    conn.close()
    
    print(f"股票池: {len(codes)} 只")
    print(f"时间范围: {start_date} ~ {end_date}")
    print()
    
    # 加载价格数据
    print("加载价格数据...")
    price_panel = load_price_data(codes, start_date, end_date)
    print(f"价格面板: {price_panel.shape}")
    print()
    
    # 计算未来收益率
    print("计算未来收益率...")
    fwd_returns = calc_forward_returns(price_panel, periods=[1, 3, 5])
    print(f"未来收益率: {list(fwd_returns.keys())}")
    print()
    
    # 获取舆情因子
    print("构建舆情因子...")
    builder = SentimentFactorBuilder()
    
    # 获取有新闻数据的日期
    conn = sqlite3.connect('data/sentiment.db', timeout=15)
    dates_df = conn.execute('''
        SELECT DISTINCT DATE(publish_time) as date
        FROM stock_news
        WHERE DATE(publish_time) BETWEEN ? AND ?
        ORDER BY date
    ''', (start_date, end_date)).fetchall()
    conn.close()
    
    dates = [row[0] for row in dates_df]
    print(f"有舆情数据的日期: {len(dates)} 天")
    print()
    
    # 计算IC
    print("计算IC...")
    print("="*60)
    
    factor_names = ['sentiment_score', 'news_heat', 'sentiment_momentum']
    fwd_periods = ['fwd_ret_1d', 'fwd_ret_3d', 'fwd_ret_5d']
    
    results = []
    
    for factor_name in factor_names:
        print(f"\n{factor_name}:")
        
        # 构建因子面板
        factor_panel = {}
        for date in dates:
            try:
                factors = builder.build_all_factors(codes, date)
                factor_panel[date] = factors[factor_name]
            except Exception as e:
                pass
        
        if not factor_panel:
            print("  无数据")
            continue
        
        factor_df = pd.DataFrame(factor_panel).T
        
        for fwd_name, fwd_ret in fwd_returns.items():
            ic_mean, ir, n = calc_ic(factor_df, fwd_ret)
            
            status = "✅" if abs(ic_mean) > 0.03 and abs(ir) > 0.3 else "❌"
            print(f"  {fwd_name}: IC={ic_mean:.4f}, IR={ir:.3f}, N={n} {status}")
            
            results.append({
                'factor': factor_name,
                'forward': fwd_name,
                'ic_mean': ic_mean,
                'ir': ir,
                'n': n,
                'status': '有效' if abs(ic_mean) > 0.03 and abs(ir) > 0.3 else '无效',
            })
    
    # 汇总
    print("\n" + "="*60)
    print("汇总")
    print("="*60)
    
    df_results = pd.DataFrame(results)
    print(df_results.to_string(index=False))
    
    # 保存结果
    df_results.to_csv('/root/a-share-quant-sim/docs/sentiment_ic_results.csv', index=False)
    print(f"\n结果已保存到 docs/sentiment_ic_results.csv")


if __name__ == '__main__':
    main()
