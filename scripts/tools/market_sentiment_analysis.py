#!/usr/bin/env python3
"""market_sentiment 分年分析 — 确保参数覆盖高收益、挡住低收益"""
import sys, os, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/root/a-share-quant-sim')
import sqlite3, numpy as np, pandas as pd

def load_data():
    conn = sqlite3.connect('data/quant_stocks.db', timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    codes_df = pd.read_sql_query('SELECT code, float_shares FROM stock_pool_zz1800', conn)
    codes = codes_df['code'].tolist()
    fs_map = dict(zip(codes_df['code'], codes_df['float_shares']))

    placeholders = ','.join(['?']*len(codes))
    sql = f"""SELECT code, date, open, high, low, close, volume
              FROM daily_kline WHERE code IN ({placeholders})
              AND date >= '2020-06-01' AND date <= '2026-06-29'
              ORDER BY code, date"""
    df = pd.read_sql_query(sql, conn, params=codes)
    conn.close()

    df['date'] = pd.to_datetime(df['date'])
    df['float_shares'] = df['code'].map(fs_map)
    df['turnover'] = df['volume'] * 100 / df['float_shares']
    df['market_cap'] = df['close'] * df['float_shares']

    close = df.pivot(index='date', columns='code', values='close')
    turnover = df.pivot(index='date', columns='code', values='turnover')
    mcap = df.pivot(index='date', columns='code', values='market_cap')

    return {'close': close, 'turnover': turnover, 'mcap': mcap}

def calc_market_sentiment(close, window):
    """计算market_sentiment（连续两天涨停数的滚动均值）"""
    daily_ret = close.pct_change()
    is_limit = ((daily_ret >= 0.095) & (daily_ret <= 0.105)).astype(float).fillna(0)
    two_day_limit = (is_limit.shift(1).fillna(0) == 1) & (is_limit == 1)
    daily_limit_count = two_day_limit.astype(float).sum(axis=1)
    sentiment = daily_limit_count.rolling(window).mean()
    return sentiment, daily_limit_count

def analyze_by_year(sentiment, close, threshold, mode, window):
    """分年分析：在给定参数下，v61b的表现"""
    dates = sorted(close.index)
    
    # 计算v61b选股得分
    turnover = None  # 不需要，简化
    
    results = {}
    
    for year in range(2021, 2027):
        year_dates = [d for d in dates if d.year == year]
        if len(year_dates) < 20:
            continue
        
        # 统计该年sentiment的分布
        year_sent = sentiment.loc[sentiment.index.year == year].dropna()
        if len(year_sent) == 0:
            continue
        
        # 该年中有多少天会交易（根据sentiment过滤）
        trade_days = 0
        block_days = 0
        for d in year_dates:
            if d in sentiment.index:
                sent_val = sentiment.loc[d]
                if not np.isnan(sent_val):
                    recent = sentiment.loc[:d].tail(window)
                    if len(recent) >= window:
                        pct = (recent < sent_val).mean()
                        if mode == 'hot' and pct >= threshold:
                            trade_days += 1
                        elif mode == 'cold' and pct <= threshold:
                            trade_days += 1
                        else:
                            block_days += 1
        
        total_days = trade_days + block_days
        trade_pct = trade_days / total_days * 100 if total_days > 0 else 0
        
        # 计算该年等权市场收益（作为参考）
        year_ret = close.loc[close.index.year == year].pct_change().mean(axis=1)
        mkt_ret = (year_ret.mean() * 252) * 100
        
        results[year] = {
            'trade_days': trade_days,
            'block_days': block_days,
            'trade_pct': trade_pct,
            'mkt_ret': mkt_ret,
            'sent_mean': year_sent.mean(),
            'sent_std': year_sent.std(),
        }
    
    return results

def main():
    print("加载数据...")
    data = load_data()
    close = data['close']
    
    # 测试不同窗口
    for window in [10, 15, 20]:
        print(f"\n{'='*80}")
        print(f"window = {window}")
        print(f"{'='*80}")
        
        sentiment, raw_count = calc_market_sentiment(close, window)
        
        # 测试不同阈值和模式
        for mode in ['hot', 'cold']:
            for threshold in [0.3, 0.4, 0.5, 0.6, 0.7]:
                print(f"\n--- {mode} mode, threshold={threshold} ---")
                results = analyze_by_year(sentiment, close, threshold, mode, window)
                
                print(f"{'年份':<6} {'交易天数':>8} {'阻挡天数':>8} {'交易占比':>8} {'市场收益':>10} {'sent均值':>10}")
                print("-" * 60)
                
                for year, r in sorted(results.items()):
                    print(f"{year:<6} {r['trade_days']:>8} {r['block_days']:>8} {r['trade_pct']:>7.1f}% {r['mkt_ret']:>+9.1f}% {r['sent_mean']:>10.2f}")
    
    # 看看raw_count的分布
    print(f"\n{'='*80}")
    print(f"raw daily_limit_count 分布")
    print(f"{'='*80}")
    print(raw_count.describe())
    
    # 分年统计
    print(f"\n分年统计:")
    for year in range(2021, 2027):
        year_data = raw_count[raw_count.index.year == year]
        if len(year_data) > 0:
            print(f"  {year}: mean={year_data.mean():.2f}, max={year_data.max():.0f}, >0占比={((year_data>0).sum()/len(year_data)*100):.1f}%")

if __name__ == '__main__':
    main()
