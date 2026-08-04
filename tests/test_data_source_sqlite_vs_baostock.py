#!/usr/bin/env python3
"""数据源对比测试 — 用 SQLite 腾讯历史数据作为基准

对比 SQLite (腾讯历史) vs BaoStock Provider
验证两者在相同时间段的数据一致性。
"""
import sys, os
import time
import pandas as pd
import numpy as np
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))

from core.providers.baostock import BaoStockProvider


def load_test_codes(n=20):
    """从 stock_pool_zz1800 取前 n 只股票"""
    conn = sqlite3.connect('data/quant_stocks.db', timeout=30)
    df = pd.read_sql_query("SELECT code FROM stock_pool_zz1800 LIMIT ?", conn, params=[n])
    conn.close()
    codes = df['code'].tolist()
    print(f"测试股票: {codes[:5]}... (共{len(codes)}只)")
    return codes


def load_sqlite_tencent(codes, start_date='2021-01-01', end_date='2021-12-31'):
    """从 SQLite 加载腾讯历史数据（作为基准）"""
    print(f"[SQLite 腾讯] 加载 {len(codes)} 只股票...")
    t0 = time.time()
    
    conn = sqlite3.connect('data/quant_stocks.db', timeout=30)
    placeholders = ','.join(['?'] * len(codes))
    sql = f"""SELECT code, date, open, high, low, close, volume
              FROM daily_kline WHERE code IN ({placeholders})
              AND date >= ? AND date <= ?
              ORDER BY code, date"""
    df = pd.read_sql_query(sql, conn, params=codes + [start_date, end_date])
    conn.close()
    
    t1 = time.time()
    print(f"[SQLite 腾讯] 加载完成: {len(df)} 行, {t1-t0:.1f}s")
    
    if df.empty:
        return None
    
    df['date'] = pd.to_datetime(df['date'])
    return df


def load_baostock(codes, start_date='2021-01-01', end_date='2021-12-31'):
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
    return df


def compare_data(df_tx, df_bs, label_tx="SQLite 腾讯", label_bs="BaoStock"):
    """对比两个数据源"""
    print(f"\n{'='*60}")
    print(f"数据对比: {label_tx} vs {label_bs}")
    print(f"{'='*60}")
    
    # 提取数据
    if 'code' in df_tx.columns:
        tx_close = df_tx.pivot(index='date', columns='code', values='close')
        tx_volume = df_tx.pivot(index='date', columns='code', values='volume')
    else:
        tx_close = df_tx['close']
        tx_volume = df_tx['volume']
    
    if 'code' in df_bs.columns:
        bs_close = df_bs.pivot(index='date', columns='code', values='close')
        bs_volume = df_bs.pivot(index='date', columns='code', values='volume')
    else:
        bs_close = df_bs['close']
        bs_volume = df_bs['volume']
    
    # 对齐
    common_dates = tx_close.index.intersection(bs_close.index)
    common_codes = tx_close.columns.intersection(bs_close.columns)
    
    print(f"\n{label_tx}:")
    print(f"  日期范围: {tx_close.index.min()} ~ {tx_close.index.max()}")
    print(f"  天数: {len(tx_close.index)}")
    print(f"  股票数: {len(tx_close.columns)}")
    
    print(f"\n{label_bs}:")
    print(f"  日期范围: {bs_close.index.min()} ~ {bs_close.index.max()}")
    print(f"  天数: {len(bs_close.index)}")
    print(f"  股票数: {len(bs_close.columns)}")
    
    print(f"\n共同数据:")
    print(f"  日期: {len(common_dates)} 天")
    print(f"  股票: {len(common_codes)} 只")
    
    if len(common_dates) == 0 or len(common_codes) == 0:
        print("  ❌ 无共同数据!")
        return
    
    # 收盘价对比
    t_close = tx_close.loc[common_dates, common_codes]
    b_close = bs_close.loc[common_dates, common_codes]
    
    close_diff = (t_close - b_close).abs()
    print(f"\n收盘价差异:")
    print(f"  绝对差异: mean={close_diff.mean().mean():.4f}, max={close_diff.max().max():.4f}")
    
    # 成交量对比
    t_vol = tx_volume.loc[common_dates, common_codes]
    b_vol = bs_volume.loc[common_dates, common_codes]
    
    vol_diff = (t_vol - b_vol).abs()
    vol_rel_diff = vol_diff / (b_vol + 1e-10) * 100
    
    print(f"\n成交量差异 (单位: 手):")
    print(f"  绝对差异: mean={vol_diff.mean().mean():.2f}, max={vol_diff.max().max():.2f}")
    print(f"  相对差异: mean={vol_rel_diff.mean().mean():.4f}%, max={vol_rel_diff.max().max():.4f}%")
    
    # 完全一致比例
    close_exact = (close_diff < 0.001).all(axis=1).mean() * 100
    vol_exact = (vol_diff < 1).all(axis=1).mean() * 100
    
    print(f"\n完全一致比例:")
    print(f"  收盘价: {close_exact:.1f}%")
    print(f"  成交量: {vol_exact:.1f}%")
    
    # 差异分布
    print(f"\n成交量差异分布:")
    print(f"  =0: {(vol_diff == 0).all(axis=1).mean()*100:.1f}%")
    print(f"  ≤1: {(vol_diff <= 1).all(axis=1).mean()*100:.1f}%")
    print(f"  ≤10: {(vol_diff <= 10).all(axis=1).mean()*100:.1f}%")
    print(f"  >10: {(vol_diff > 10).any(axis=1).mean()*100:.1f}%")
    
    # 相关系数
    corr_list = []
    for code in common_codes:
        if t_vol[code].std() > 0 and b_vol[code].std() > 0:
            corr = t_vol[code].corr(b_vol[code])
            if not np.isnan(corr):
                corr_list.append(corr)
    
    if corr_list:
        print(f"\n成交量相关系数:")
        print(f"  mean={np.mean(corr_list):.4f}, min={np.min(corr_list):.4f}, max={np.max(corr_list):.4f}")


def main():
    print("="*60)
    print("数据源对比测试 — SQLite 腾讯 vs BaoStock")
    print("="*60)
    
    codes = load_test_codes(20)
    
    # 加载数据
    df_tx = load_sqlite_tencent(codes, '2021-01-01', '2021-12-31')
    df_bs = load_baostock(codes, '2021-01-01', '2021-12-31')
    
    if df_tx is None or df_bs is None:
        print("数据加载失败!")
        return
    
    # 对比
    compare_data(df_tx, df_bs)
    
    print("\n" + "="*60)
    print("测试完成")
    print("="*60)


if __name__ == '__main__':
    main()
