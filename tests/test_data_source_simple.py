#!/usr/bin/env python3
"""数据源对比测试 — 简化版

只用 20 只股票测试，快速验证数据源差异。
"""
import sys, os
import time
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))

from core.providers.baostock import BaoStockProvider
from core.providers.tencent import TencentProvider


def load_test_codes(n=20):
    """获取测试用股票代码"""
    import sqlite3
    conn = sqlite3.connect('data/quant_stocks.db', timeout=30)
    codes_df = pd.read_sql_query('SELECT code FROM stock_pool_zz1800 LIMIT ?', conn, params=(n,))
    conn.close()
    return codes_df['code'].tolist()


def load_data_baostock(codes, start_date='2021-01-01', end_date='2021-12-31'):
    """从 BaoStock 加载数据"""
    print(f"[BaoStock] 加载 {len(codes)} 只股票...")
    t0 = time.time()
    
    provider = BaoStockProvider()
    df = provider.get_daily_kline(codes, start_date, end_date)
    
    t1 = time.time()
    print(f"[BaoStock] 加载完成: {len(df)} 行, {t1-t0:.1f}s")
    
    if df.empty:
        return None
    
    df = df.reset_index()
    df['date'] = pd.to_datetime(df['date'])
    
    close = df.pivot(index='date', columns='code', values='close')
    volume = df.pivot(index='date', columns='code', values='volume')
    turnover = df.pivot(index='date', columns='code', values='turnover')
    
    return {
        'close': close,
        'volume': volume,
        'turnover': turnover,
    }


def load_data_tencent(codes, start_date='2021-01-01', end_date='2021-12-31'):
    """从腾讯 Provider 加载数据
    
    注意：腾讯API只返回最近N天数据，不支持指定历史日期范围。
    如果 start_date 太早，会返回最近N天的数据。
    """
    print(f"[腾讯] 加载 {len(codes)} 只股票...")
    t0 = time.time()
    
    provider = TencentProvider()
    df = provider.get_daily_kline(codes, start_date, end_date)
    
    t1 = time.time()
    print(f"[腾讯] 加载完成: {len(df)} 行, {t1-t0:.1f}s")
    
    if df.empty:
        return None
    
    df = df.reset_index()
    df['date'] = pd.to_datetime(df['date'])
    
    close = df.pivot(index='date', columns='code', values='close')
    volume = df.pivot(index='date', columns='code', values='volume')
    turnover = df.pivot(index='date', columns='code', values='turnover')
    
    return {
        'close': close,
        'volume': volume,
        'turnover': turnover,
    }


def compare_data(data_t, data_b, label_t="腾讯", label_b="BaoStock"):
    """对比两个数据源"""
    print("\n" + "="*60)
    print(f"数据对比: {label_t} vs {label_b}")
    print("="*60)
    
    # 基本信息
    print(f"\n{label_t}:")
    print(f"  日期范围: {data_t['close'].index.min()} ~ {data_t['close'].index.max()}")
    print(f"  天数: {data_t['close'].shape[0]}")
    print(f"  股票数: {data_t['close'].shape[1]}")
    
    print(f"\n{label_b}:")
    print(f"  日期范围: {data_b['close'].index.min()} ~ {data_b['close'].index.max()}")
    print(f"  天数: {data_b['close'].shape[0]}")
    print(f"  股票数: {data_b['close'].shape[1]}")
    
    # 共同数据
    common_dates = data_t['close'].index.intersection(data_b['close'].index)
    common_codes = data_t['close'].columns.intersection(data_b['close'].columns)
    
    print(f"\n共同数据:")
    print(f"  日期: {len(common_dates)} 天")
    print(f"  股票: {len(common_codes)} 只")
    
    if len(common_dates) == 0 or len(common_codes) == 0:
        print("❌ 无共同数据!")
        return
    
    # 价格对比
    t_close = data_t['close'].loc[common_dates, common_codes]
    b_close = data_b['close'].loc[common_dates, common_codes]
    
    price_diff = (t_close - b_close).abs()
    print(f"\n收盘价差异:")
    print(f"  绝对差异: mean={price_diff.mean().mean():.4f}, max={price_diff.max().max():.4f}")
    
    # 成交量对比
    t_vol = data_t['volume'].loc[common_dates, common_codes]
    b_vol = data_b['volume'].loc[common_dates, common_codes]
    
    vol_diff = (t_vol - b_vol).abs() / (b_vol + 1e-10) * 100
    print(f"\n成交量差异 (相对):")
    print(f"  mean={vol_diff.mean().mean():.1f}%, max={vol_diff.max().max():.1f}%")
    
    # 换手率对比
    t_turn = data_t['turnover'].loc[common_dates, common_codes]
    b_turn = data_b['turnover'].loc[common_dates, common_codes]
    
    turn_diff = (t_turn - b_turn).abs()
    turn_rel_diff = turn_diff / (b_turn + 1e-10) * 100
    
    print(f"\n换手率差异:")
    print(f"  绝对差异: mean={turn_diff.mean().mean():.4f}, max={turn_diff.max().max():.4f}")
    print(f"  相对差异: mean={turn_rel_diff.mean().mean():.1f}%, max={turn_rel_diff.max().max():.1f}%")
    
    # 相关系数
    corr_list = []
    for code in common_codes:
        t_s = t_turn[code].dropna()
        b_s = b_turn[code].dropna()
        ci = t_s.index.intersection(b_s.index)
        if len(ci) > 10:
            corr = t_s[ci].corr(b_s[ci])
            if not np.isnan(corr):
                corr_list.append(corr)
    
    if corr_list:
        print(f"\n换手率相关系数:")
        print(f"  mean={np.mean(corr_list):.3f}, min={np.min(corr_list):.3f}, max={np.max(corr_list):.3f}")


if __name__ == '__main__':
    print("="*60)
    print("数据源对比测试 — 简化版 (20只股票, 2021年)")
    print("="*60)
    
    # 获取测试股票
    codes = load_test_codes(20)
    print(f"测试股票: {codes[:5]}... (共{len(codes)}只)")
    
    # 加载数据
    data_bs = load_data_baostock(codes, '2021-01-01', '2021-12-31')
    data_tx = load_data_tencent(codes, '2021-01-01', '2021-12-31')
    
    if data_bs is None or data_tx is None:
        print("数据加载失败!")
        sys.exit(1)
    
    # 对比
    compare_data(data_tx, data_bs)
    
    print("\n" + "="*60)
    print("测试完成")
    print("="*60)
