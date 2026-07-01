#!/usr/bin/env python3
"""情绪择时因子 IC 分析 — 市场级因子用时间序列IC"""
import sys, os, time, warnings
warnings.filterwarnings('ignore')
sys.path.insert(0, '/root/a-share-quant-sim')
import sqlite3, numpy as np, pandas as pd
from scipy import stats

LOG_FILE = '/root/a-share-quant-sim/scripts/tools/sentiment_ic_scan.log'

def log(msg):
    print(msg, flush=True)
    with open(LOG_FILE, 'a') as f:
        f.write(msg + '\n')

def load_data():
    log("[1] 加载数据...")
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
    df['ret'] = df.groupby('code')['close'].pct_change()

    close = df.pivot(index='date', columns='code', values='close')
    ret = df.pivot(index='date', columns='code', values='ret')
    turnover = df.pivot(index='date', columns='code', values='turnover')

    log(f"    {close.shape[0]} days, {close.shape[1]} stocks")
    return {'close': close, 'ret': ret, 'turnover': turnover}

def calc_factors(data):
    """计算所有候选情绪因子（市场级 = 单时间序列）"""
    log("[2] 计算因子...")
    close = data['close']
    ret = data['ret']
    turnover = data['turnover']
    
    factors = {}
    
    # 等权市场收益作为基准
    mkt_ret = ret.mean(axis=1)
    
    # 1. limit_count: 连续两天涨停股票数
    daily_ret = close.pct_change()
    is_limit = ((daily_ret >= 0.095) & (daily_ret <= 0.105)).astype(float).fillna(0)
    two_day_limit = (is_limit.shift(1).fillna(0) == 1) & (is_limit == 1)
    daily_limit_count = two_day_limit.astype(float).sum(axis=1)
    factors['limit_count'] = daily_limit_count
    
    # 2. breadth_ma5: 站上5日均线的股票比例
    ma5 = close.rolling(5, min_periods=3).mean()
    factors['breadth_ma5'] = (close > ma5).sum(axis=1) / close.shape[1]
    
    # 3. breadth_ma20: 站上20日均线的股票比例
    ma20 = close.rolling(20, min_periods=10).mean()
    factors['breadth_ma20'] = (close > ma20).sum(axis=1) / close.shape[1]
    
    # 4. avg_turnover: 全市场平均换手率
    factors['avg_turnover'] = turnover.mean(axis=1)
    
    # 5. avg_return_5d: 全市场5日平均收益
    factors['avg_return_5d'] = ret.rolling(5, min_periods=3).mean().mean(axis=1)
    
    # 6. avg_return_20d: 全市场20日平均收益
    factors['avg_return_20d'] = ret.rolling(20, min_periods=10).mean().mean(axis=1)
    
    # 7. volatility_20d: 全市场20日波动率
    factors['volatility_20d'] = ret.rolling(20, min_periods=10).std().mean(axis=1)
    
    # 8. up_ratio: 上涨股票比例
    factors['up_ratio'] = (ret > 0).sum(axis=1) / ret.shape[1]
    
    # 9. limit_up_count: 涨停股票数（单日）
    factors['limit_up_count'] = is_limit.sum(axis=1)
    
    # 10. return_dispersion: 收益离散度（截面标准差）
    factors['return_dispersion'] = ret.std(axis=1)
    
    # 11. avg_amplitude: 平均振幅
    close_range = close.rolling(5).max() - close.rolling(5).min()
    factors['avg_amplitude'] = (close_range / close.rolling(5).mean()).mean(axis=1)
    
    # 12. 新增：市场动量（20日市场收益）
    factors['market_momentum_20'] = mkt_ret.rolling(20, min_periods=10).sum()
    
    # 13. 新增：市场波动率变化（20日 vs 60日）
    vol_20 = mkt_ret.rolling(20).std()
    vol_60 = mkt_ret.rolling(60).std()
    factors['vol_change'] = vol_20 / vol_60.replace(0, np.nan)
    
    # 14. 新增：换手率变化（5日 vs 20日）
    turn_5 = turnover.mean(axis=1).rolling(5).mean()
    turn_20 = turnover.mean(axis=1).rolling(20).mean()
    factors['turnover_change'] = turn_5 / turn_20.replace(0, np.nan)
    
    log(f"    计算了 {len(factors)} 个因子")
    return factors

def calc_ic_ts(factors, mkt_ret, forward_days=5):
    """时间序列IC：因子值与未来N日市场收益的rank相关性"""
    log(f"[3] 计算时间序列IC (forward={forward_days}d)...")
    
    # 未来N日市场收益
    fwd_ret = mkt_ret.rolling(forward_days).sum().shift(-forward_days)
    
    results = []
    for name, factor in factors.items():
        # 对齐日期
        aligned = pd.DataFrame({'f': factor, 'r': fwd_ret}).dropna()
        if len(aligned) < 50:
            log(f"    {name:20s}: 数据不足 ({len(aligned)} days)")
            continue
        
        # Rank IC（每日一个值，跨时间的rank相关）
        # 但市场级因子只有一个时间序列，所以用 rolling rank
        # 简单方法：计算因子值与未来收益的 Spearman 相关
        ic, pval = stats.spearmanr(aligned['f'], aligned['r'])
        
        # 也计算 IC 的稳定性：rolling 60日窗口的IC
        rolling_ics = []
        window = 60
        for i in range(window, len(aligned)):
            chunk = aligned.iloc[i-window:i]
            r, _ = stats.spearmanr(chunk['f'], chunk['r'])
            if not np.isnan(r):
                rolling_ics.append(r)
        
        if rolling_ics:
            ic_mean = np.mean(rolling_ics)
            ic_std = np.std(rolling_ics)
            ir = ic_mean / ic_std if ic_std > 0 else 0
            ic_pos = np.mean(np.array(rolling_ics) > 0) * 100
        else:
            ic_mean = ic
            ic_std = 0
            ir = 0
            ic_pos = 50
        
        results.append({
            'name': name,
            'ic_whole': ic,
            'ic_mean': ic_mean,
            'ic_std': ic_std,
            'ir': ir,
            'ic_pos': ic_pos,
            'pval': pval,
            'n_dates': len(aligned),
        })
        
        log(f"    {name:20s}: IC_whole={ic:+.4f}, IC_roll={ic_mean:+.4f}, IR={ir:+.3f}, IC>0={ic_pos:.1f}%, p={pval:.4f}")
    
    return results

def main():
    with open(LOG_FILE, 'w') as f:
        f.write('')
    
    t0 = time.time()
    data = load_data()
    factors = calc_factors(data)
    
    # 等权市场收益
    mkt_ret = data['ret'].mean(axis=1)
    
    results = calc_ic_ts(factors, mkt_ret, forward_days=5)
    
    # 按|IC|排序
    results.sort(key=lambda x: abs(x['ic_mean']), reverse=True)
    
    log(f"\n{'='*80}")
    log(f"情绪因子时间序列IC分析结果 (按|IC|排序)")
    log(f"{'='*80}")
    log(f"\n{'因子':<20} {'IC全量':>8} {'IC均值':>8} {'IR':>8} {'IC>0':>8} {'p值':>8} {'状态':>6}")
    log("-" * 80)
    
    valid_factors = []
    for r in results:
        ic = r['ic_mean']
        ir = r['ir']
        pval = r['pval']
        
        # 判断标准: |IC| > 0.1 且 p < 0.05
        if abs(ic) > 0.1 and pval < 0.05:
            status = "✅ 有效"
            valid_factors.append(r)
        elif abs(ic) > 0.05 and pval < 0.1:
            status = "⚠️ 微弱"
        else:
            status = "❌ 无效"
        
        log(f"{r['name']:<20} {r['ic_whole']:>+8.4f} {ic:>+8.4f} {ir:>+8.3f} {r['ic_pos']:>7.1f}% {pval:>8.4f} {status}")
    
    log(f"\n有效因子: {len(valid_factors)}/{len(results)}")
    for r in valid_factors:
        direction = "正向" if r['ic_mean'] > 0 else "反向"
        log(f"  ✅ {r['name']}: IC={r['ic_mean']:+.4f}, IR={r['ir']:+.3f}, 方向={direction}")
    
    elapsed = time.time() - t0
    log(f"\n耗时 {elapsed:.1f}s")

if __name__ == '__main__':
    main()
