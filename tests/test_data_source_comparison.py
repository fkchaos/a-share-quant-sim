#!/usr/bin/env python3
"""数据源对比测试 — v61b 回测

用 BaoStock 和腾讯两个数据源分别跑 v61b 回测，对比结果差异。
"""
import sys, os
import time
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))

from core.providers.baostock import BaoStockProvider
from core.providers.tencent import TencentProvider


def load_data_baostock(start_date='2020-06-01', end_date='2026-06-29'):
    """从 BaoStock 加载数据"""
    print("[BaoStock] 加载数据...")
    t0 = time.time()
    
    import sqlite3
    conn = sqlite3.connect('data/quant_stocks.db', timeout=30)
    codes_df = pd.read_sql_query('SELECT code FROM stock_pool_zz1800', conn)
    codes = codes_df['code'].tolist()
    conn.close()
    
    provider = BaoStockProvider()
    
    # 分批获取（BaoStock 限制单次6000行）
    all_dfs = []
    batch_size = 50
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i+batch_size]
        df = provider.get_daily_kline(batch, start_date, end_date)
        if not df.empty:
            all_dfs.append(df)
        if (i // batch_size) % 10 == 0:
            print(f"  进度: {i}/{len(codes)}...")
    
    if not all_dfs:
        return None
    
    big_df = pd.concat(all_dfs)
    
    # pivot 为面板
    big_df = big_df.reset_index()
    big_df['date'] = pd.to_datetime(big_df['date'])
    
    close = big_df.pivot(index='date', columns='code', values='close')
    volume = big_df.pivot(index='date', columns='code', values='volume')
    turnover = big_df.pivot(index='date', columns='code', values='turnover')
    
    t1 = time.time()
    print(f"[BaoStock] 加载完成: {close.shape[0]} 天, {close.shape[1]} 只, {t1-t0:.1f}s")
    
    return {
        'close': close,
        'volume': volume,
        'turnover': turnover,
    }


def load_data_tencent(start_date='2020-06-01', end_date='2026-06-29'):
    """从腾讯加载数据（从本地 SQLite）"""
    print("[腾讯] 加载数据...")
    t0 = time.time()
    
    import sqlite3
    conn = sqlite3.connect('data/quant_stocks.db', timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    codes_df = pd.read_sql_query('SELECT code FROM stock_pool_zz1800', conn)
    codes = codes_df['code'].tolist()
    
    placeholders = ','.join(['?']*len(codes))
    sql = f"""SELECT code, date, open, high, low, close, volume
              FROM daily_kline WHERE code IN ({placeholders})
              AND date >= '{start_date}' AND date <= '{end_date}'
              ORDER BY code, date"""
    df = pd.read_sql_query(sql, conn, params=codes)
    conn.close()
    
    if df.empty:
        return None
    
    df['date'] = pd.to_datetime(df['date'])
    
    # pivot 为面板
    close = df.pivot(index='date', columns='code', values='close')
    volume = df.pivot(index='date', columns='code', values='volume')
    
    # 腾讯没有 turnover，需要从 float_shares 算
    conn = sqlite3.connect('data/quant_stocks.db', timeout=30)
    fs_df = pd.read_sql_query('SELECT code, float_shares FROM stock_pool_zz1800', conn, index_col='code')
    conn.close()
    
    fs_map = fs_df['float_shares'].to_dict()
    fs_series = pd.Series({c: fs_map.get(c, np.nan) for c in volume.columns})
    turnover = volume.mul(100).div(fs_series, axis=1)  # 腾讯volume是股，需*100得到手，再/float_shares
    
    t1 = time.time()
    print(f"[腾讯] 加载完成: {close.shape[0]} 天, {close.shape[1]} 只, {t1-t0:.1f}s")
    
    return {
        'close': close,
        'volume': volume,
        'turnover': turnover,
    }


def run_v61b_backtest(data, label):
    """运行 v61b 回测"""
    print(f"\n{'='*60}")
    print(f"[{label}] 运行 v61b 回测...")
    print(f"{'='*60}")
    
    close = data['close']
    turnover = data['turnover']
    
    # 计算 turn_5
    turn_5 = turnover.rolling(5, min_periods=3).mean()
    
    # 计算市值（用 float_shares）
    import sqlite3
    conn = sqlite3.connect('data/quant_stocks.db', timeout=30)
    fs_df = pd.read_sql_query('SELECT code, float_shares FROM stock_pool_zz1800', conn, index_col='code')
    conn.close()
    fs_map = fs_df['float_shares'].to_dict()
    fs_series = pd.Series({c: fs_map.get(c, np.nan) for c in close.columns})
    mcap = close.mul(fs_series, axis=1)
    
    # 参数
    STOP_LOSS = -0.08
    TAKE_PROFIT = 0.25
    HOLD_DAYS_MAX = 5
    REBALANCE_DAYS = 5
    MAX_HOLDINGS = 5
    
    # 回测
    INIT_CASH = 200000
    cash = INIT_CASH
    holdings = {}
    nav_list = []
    
    dates = sorted(close.index)
    start_idx = next((i for i, d in enumerate(dates) if d >= pd.Timestamp('2021-01-01')), 0)
    test_dates = dates[start_idx:]
    
    first_day = True
    
    for date in test_dates:
        # 计算净值
        val = cash
        to_sell = []
        
        for code, pos in holdings.items():
            if code in close.columns:
                p = close.at[date, code] if date in close.index else np.nan
                if not np.isnan(p):
                    val += pos['shares'] * p
                    pnl = (p - pos['cost']) / pos['cost']
                    if pnl <= STOP_LOSS or pnl >= TAKE_PROFIT:
                        to_sell.append(code)
                        continue
                    pos['days'] = pos.get('days', 0) + 1
                    if pos['days'] >= HOLD_DAYS_MAX:
                        to_sell.append(code)
        
        # 卖出
        for code in to_sell:
            if code in close.columns:
                p = close.at[date, code]
                if not np.isnan(p):
                    cash += holdings[code]['shares'] * p * 0.9987
            del holdings[code]
        
        nav_list.append({'date': date, 'nav': val})
        
        # 买入（调仓日或首日）
        if first_day or len(to_sell) > 0:
            # 选股
            if date in turn_5.index:
                t5 = turn_5.loc[date]
                sz = mcap.loc[date]
                
                scores = pd.Series(0.0, index=close.columns)
                for f in (-t5, -sz):
                    valid = f.dropna()
                    if len(valid) >= 50:
                        ranked = valid.rank(ascending=True, pct=True)
                        scores[ranked.index] += ranked
                
                valid_codes = [c for c in scores.dropna().index
                              if close.at[date, c] > 0 and turnover.at[date, c] > 0]
                scores = scores[valid_codes].sort_values(ascending=False)
                
                candidates = scores.head(MAX_HOLDINGS * 2).index.tolist()
                held = set(holdings.keys())
                buy_list = [c for c in candidates if c not in held][:MAX_HOLDINGS]
                
                if buy_list:
                    n_buy = len(buy_list)
                    per = cash * 0.95 / n_buy
                    for code in buy_list:
                        if code in close.columns:
                            p = close.at[date, code]
                            if not np.isnan(p) and p > 0:
                                shares = int(per / p / 100) * 100
                                if shares > 0:
                                    cost = shares * p * 1.0003
                                    if cost <= cash:
                                        cash -= cost
                                        holdings[code] = {'shares': shares, 'cost': p, 'days': 0}
            
            first_day = False
    
    # 计算指标
    nav = pd.Series([n['nav'] for n in nav_list], index=[n['date'] for n in nav_list])
    total = (nav.iloc[-1] / nav.iloc[0] - 1) * 100
    daily_ret = nav.pct_change().dropna()
    sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
    dd = (nav / nav.cummax() - 1).min() * 100
    
    print(f"\n[{label}] v61b 回测结果:")
    print(f"  收益率: {total:+.1f}%")
    print(f"  夏普: {sharpe:.3f}")
    print(f"  最大回撤: {dd:.1f}%")
    print(f"  最终净值: {nav.iloc[-1]:,.0f}")
    
    return {
        'total': total,
        'sharpe': sharpe,
        'dd': dd,
        'nav': nav,
    }


def compare_turnover(data_tencent, data_baostock):
    """对比两个数据源的换手率"""
    print("\n" + "="*60)
    print("对比换手率 (腾讯 vs BaoStock)")
    print("="*60)
    
    t_t = data_tencent['turnover']
    t_b = data_baostock['turnover']
    
    # 找共同日期和股票
    common_dates = t_t.index.intersection(t_b.index)
    common_codes = t_t.columns.intersection(t_b.columns)
    
    t_common = t_t.loc[common_dates, common_codes]
    b_common = t_b.loc[common_dates, common_codes]
    
    # 计算差异
    diff = (t_common - b_common).abs()
    rel_diff = diff / (b_common + 1e-10) * 100
    
    print(f"共同日期: {len(common_dates)} 天")
    print(f"共同股票: {len(common_codes)} 只")
    print(f"换手率绝对差异: mean={diff.mean().mean():.4f}, max={diff.max().max():.4f}")
    print(f"换手率相对差异: mean={rel_diff.mean().mean():.1f}%, max={rel_diff.max().max():.1f}%")
    
    # 相关系数
    corr_list = []
    for code in common_codes[:50]:  # 取前50只计算
        t_series = t_common[code].dropna()
        b_series = b_common[code].dropna()
        common_idx = t_series.index.intersection(b_series.index)
        if len(common_idx) > 10:
            corr = t_series[common_idx].corr(b_series[common_idx])
            if not np.isnan(corr):
                corr_list.append(corr)
    
    if corr_list:
        print(f"换手率相关系数: mean={np.mean(corr_list):.3f}, min={np.min(corr_list):.3f}")
    
    return diff, rel_diff


if __name__ == '__main__':
    print("="*60)
    print("数据源对比测试 — v61b 回测")
    print("="*60)
    
    # 加载数据
    data_bs = load_data_baostock()
    data_tx = load_data_tencent()
    
    if data_bs is None or data_tx is None:
        print("数据加载失败!")
        sys.exit(1)
    
    # 对比换手率
    compare_turnover(data_tx, data_bs)
    
    # 分别跑回测
    result_bs = run_v61b_backtest(data_bs, "BaoStock")
    result_tx = run_v61b_backtest(data_tx, "腾讯")
    
    # 对比结果
    print("\n" + "="*60)
    print("回测结果对比")
    print("="*60)
    print(f"{'指标':<15} {'腾讯':<15} {'BaoStock':<15} {'差异':<15}")
    print("-"*60)
    print(f"{'收益率':<15} {result_tx['total']:+.1f}%{'':<10} {result_bs['total']:+.1f}%{'':<10} {result_bs['total']-result_tx['total']:+.1f}%")
    print(f"{'夏普':<15} {result_tx['sharpe']:.3f}{'':<11} {result_bs['sharpe']:.3f}{'':<11} {result_bs['sharpe']-result_tx['sharpe']:+.3f}")
    print(f"{'最大回撤':<15} {result_tx['dd']:.1f}%{'':<10} {result_bs['dd']:.1f}%{'':<10} {result_bs['dd']-result_tx['dd']:+.1f}%")
    
    # 结论
    print("\n" + "="*60)
    print("结论")
    print("="*60)
    sharpe_diff = abs(result_bs['sharpe'] - result_tx['sharpe'])
    if sharpe_diff < 0.1:
        print("✅ 夏普差异 < 0.1，数据源选择对结果影响不大")
        print("→ 建议用 BaoStock 作为 default（数据更全面）")
    else:
        print(f"⚠️ 夏普差异 = {sharpe_diff:.3f}，数据源选择对结果有显著影响")
        print("→ 需要分析原因，可能与换手率计算差异有关")
