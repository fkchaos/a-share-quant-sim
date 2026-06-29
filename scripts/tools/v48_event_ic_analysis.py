"""
v48 事件因子 IC 分析
分别计算 insider / unlock 因子与未来 N 日收益的 IC。
"""
import numpy as np
import pandas as pd
import sqlite3
from core.db import load_panel_from_db


def compute_factor_ic(factor_name: str, forward_days: int = 5,
                      start_date: str = '2023-01-01', pool: str = 'zz1800'):
    """
    计算事件因子的 IC 时间序列。
    """
    # 加载面板
    panels, codes = load_panel_from_db(start_date, None, need_open=True, need_hl=True, pool=pool)
    close, volume, amount, open_, high, low = panels
    
    # 标签：T+1 开盘买入，T+forward_days 收盘卖出
    buy_price = open_.shift(-1)
    sell_price = close.shift(-forward_days)
    raw_label = sell_price / buy_price - 1
    
    db_path = 'data/quant_stocks.db'
    conn = sqlite3.connect(db_path)
    
    ic_values = []
    ic_dates = []
    
    # 遍历有数据的日期
    test_dates = close.index[50:-forward_days-1]  # 跳过前50天（特征预热）
    
    for date in test_dates[::5]:  # 每5天采样一次（加速）
        date_str = str(date)[:10]
        
        # 获取因子值
        if factor_name == 'insider':
            factor = _get_insider_factor(conn, date_str)
        elif factor_name == 'unlock':
            factor = _get_unlock_factor(conn, date_str)
        else:
            continue
        
        if len(factor) == 0:
            continue
        
        # 获取标签
        if date not in raw_label.index:
            continue
        label = raw_label.loc[date]
        
        # 对齐
        common_stocks = factor.index.intersection(label.index)
        common_stocks = common_stocks[label.loc[common_stocks].notna()]
        
        if len(common_stocks) < 30:
            continue
        
        f_vals = factor.loc[common_stocks].values
        l_vals = label.loc[common_stocks].values
        
        # Spearman IC
        f_rank = pd.Series(f_vals).rank().values
        l_rank = pd.Series(l_vals).rank().values
        
        # 计算秩相关
        n = len(f_rank)
        if n < 10:
            continue
        
        f_mean = f_rank.mean()
        l_mean = l_rank.mean()
        cov = ((f_rank - f_mean) * (l_rank - l_mean)).sum()
        std_f = np.sqrt(((f_rank - f_mean)**2).sum())
        std_l = np.sqrt(((l_rank - l_mean)**2).sum())
        
        if std_f > 0 and std_l > 0:
            ic = cov / (std_f * std_l)
            if np.isfinite(ic):
                ic_values.append(ic)
                ic_dates.append(date_str)
    
    conn.close()
    
    if len(ic_values) == 0:
        return None
    
    ic_mean = np.mean(ic_values)
    ic_std = np.std(ic_values)
    ir = ic_mean / ic_std if ic_std > 0 else 0
    
    return {
        'ic_series': pd.Series(ic_values, index=ic_dates),
        'ic_mean': ic_mean,
        'ic_std': ic_std,
        'ir': ir,
        'n_obs': len(ic_values),
        'positive_ic_ratio': sum(1 for x in ic_values if x > 0) / len(ic_values)
    }


def _get_insider_factor(conn, date_str: str, lookback_days: int = 90) -> pd.Series:
    """获取高管增持因子。"""
    query = """
        SELECT code, trade_date, change_shares, price
        FROM insider_trades
        WHERE trade_date >= date(?, '-' || ? || ' days')
          AND trade_date <= ?
          AND change_shares IS NOT NULL
          AND price IS NOT NULL
          AND change_shares != 0
    """
    df = pd.read_sql_query(query, conn, params=[date_str, lookback_days, date_str])
    
    if len(df) == 0:
        return pd.Series(dtype=float)
    
    df['amount'] = df['change_shares'] * df['price']
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    ref_date = pd.to_datetime(date_str)
    df['days_ago'] = (ref_date - df['trade_date']).dt.days
    
    # 时间衰减
    df['weight'] = 0.25
    df.loc[df['days_ago'] <= 30, 'weight'] = 1.0
    df.loc[(df['days_ago'] > 30) & (df['days_ago'] <= 60), 'weight'] = 0.5
    
    df['weighted_amount'] = df['amount'] * df['weight']
    
    # 净增持（增持为正，减持为负）
    net = df.groupby('code')['weighted_amount'].sum()
    net = net.clip(lower=0)  # 只保留净增持
    
    if len(net) == 0:
        return pd.Series(dtype=float)
    
    # 归一化
    max_val = net.max()
    if max_val > 0:
        return net / max_val
    return net


def _get_unlock_factor(conn, date_str: str, forward_days: int = 30) -> pd.Series:
    """获取解禁压力因子（负向）。"""
    query = """
        SELECT code, pct_of_circulating
        FROM unlock_events
        WHERE unlock_date >= ?
          AND unlock_date <= date(?, '+' || ? || ' days')
          AND pct_of_circulating IS NOT NULL
    """
    df = pd.read_sql_query(query, conn, params=[date_str, date_str, forward_days])
    
    if len(df) == 0:
        return pd.Series(dtype=float)
    
    # 取最大解禁比例
    max_unlock = df.groupby('code')['pct_of_circulating'].max()
    
    # 归一化
    max_val = max_unlock.max()
    if max_val > 0:
        return max_unlock / max_val
    return max_unlock


if __name__ == '__main__':
    import time
    t0 = time.time()
    
    print("=" * 60)
    print("v48 事件因子 IC 分析")
    print("=" * 60)
    
    for factor_name in ['insider', 'unlock']:
        print(f"\n--- {factor_name} 因子 ---")
        result = compute_factor_ic(factor_name, forward_days=5, start_date='2023-01-01', pool='zz1800')
        
        if result:
            print(f"  IC Mean: {result['ic_mean']:+.4f}")
            print(f"  IC Std:  {result['ic_std']:.4f}")
            print(f"  IR:      {result['ir']:+.4f}")
            print(f"  正IC占比: {result['positive_ic_ratio']:.1%}")
            print(f"  观测数:  {result['n_obs']}")
            
            if result['ic_mean'] > 0.02 and result['ir'] > 0.2:
                print(f"  ✅ {factor_name} 因子有效")
            elif result['ic_mean'] > 0.005:
                print(f"  ⚠️ {factor_name} 因子微弱")
            else:
                print(f"  ❌ {factor_name} 因子无效")
        else:
            print(f"  无有效数据")
    
    elapsed = time.time() - t0
    print(f"\n总耗时: {elapsed:.1f}s")
