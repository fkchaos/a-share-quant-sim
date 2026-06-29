#!/usr/bin/env python3
"""
Alpha191 高效 IC/IR 测试 — v3 (按日迭代,避免大panel OOM)
- 逐日迭代因子值和 h期后收益 — 用 numpy array 不分配大 DataFrame
- 同时收集每天因子值 和 未来收益 到 cache
- 最后统一算每日 Spearman IC, 汇总
- 15 个因子 pilot run
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sqlite3
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
import warnings; warnings.filterwarnings('ignore')

DB = 'data/quant_stocks.db'
def load_daily(start='2021-01-01', end='2026-06-29'):
    """逐日加载,返回 dict: date -> DataFrame (各股当日 OHLCV + 过去N天数据)"""
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')

    # 1. 获取 zz1800 股票列表
    codes = [r[0] for r in conn.execute('SELECT code FROM stock_pool_zz1800').fetchall()]
    codes.sort()
    N = len(codes)
    code_idx = {c: i for i, c in enumerate(codes)}
    print(f"[info] {N} stocks in zz1800 pool")

    # 2. 加载 K线数据 (只 zz1800 + 日期范围 + 前推150天)
    # 前推 150 天给 rolling 算子用
    from datetime import datetime, timedelta
    s_date = (datetime.strptime(start, '%Y-%m-%d') - timedelta(days=300)).strftime('%Y-%m-%d')
    sql = f"""
        SELECT code, date, open, high, low, close, volume, amount
        FROM daily_kline
        WHERE date >= ? AND date <= ?
          AND code IN ({','.join(['?']*N)})
        ORDER BY code, date
    """
    df = pd.read_sql_query(sql, conn, params=[s_date, end] + codes)
    conn.close()

    df['date'] = pd.to_datetime(df['date'])
    df.sort_values(['date', 'code'], inplace=True)
    df.reset_index(drop=True, inplace=True)

    # 3. 转为 numpy arrays 按日期索引
    dates_sorted = sorted(df['date'].unique())
    print(f"[info] {len(dates_sorted)} dates loaded (含前推{300}天), {len(df)} rows")

    return df, codes, code_idx, dates_sorted


def build_lag_arrays(df, codes):
    """将数据按股分组, 生成 numpy 数组 (date,stock) 各算子矩阵"""
    # 把 df 按 code 分组, 每组按 date 排
    by_code = {}
    for code in codes:
        sub = df[df['code'] == code].set_index('date')
        by_code[code] = sub

    # 公共日期索引
    all_dates = sorted(df['date'].unique())
    date_idx = {d: i for i, d in enumerate(all_dates)}
    N = len(codes)
    T = len(all_dates)

    print(f"[info] 构造面板矩阵: T={T} x N={N}")

    c  = np.full((T, N), np.nan)  # close
    h  = np.full((T, N), np.nan)  # high
    l  = np.full((T, N), np.nan)  # low
    o  = np.full((T, N), np.nan)  # open
    v  = np.full((T, N), np.nan)  # volume
    a  = np.full((T, N), np.nan)  # amount
    vw = np.full((T, N), np.nan)  # vwap
    r  = np.full((T, N), np.nan)  # ret

    for j, code in enumerate(codes):
        if code not in by_code:
            continue
        sub = by_code[code]
        for _, row in sub.iterrows():
            if row.name not in date_idx:
                continue
            i = date_idx[row.name]
            c[i, j]  = row['close']
            h[i, j]  = row['high']
            l[i, j]  = row['low']
            o[i, j]  = row['open']
            v[i, j]  = row['volume']
            a[i, j]  = row['amount']
            if row['volume'] > 0:
                vw[i, j] = row['amount'] / row['volume']
            else:
                vw[i, j] = row['close']

    # ret = close / close[滞后1] - 1
    r[1:, :] = c[1:, :] / c[:-1, :] - 1.0

    print(f"[info] 面板构造完成, 内存: ~{8 * 7 * T * N / 1e9:.2f} GB")
    return {'c': c, 'h': h, 'l': l, 'o': o, 'v': v, 'a': a, 'vw': vw, 'r': r}, all_dates, date_idx


def ts_mean(arr, d):
    T, N = arr.shape
    out = np.full_like(arr, np.nan)
    # 累积和, 滑动窗口
    for i in range(d, T+1):
        window = arr[i-d:i, :]
        valid = np.isfinite(window)
        with np.errstate(all='ignore'):
            s = np.nansum(window, axis=0)
            n = np.sum(valid, axis=0)
        out[i-1, :] = np.where(n >= max(2, d//2), s / np.where(n>0, n, 1), np.nan)
    return out

def ts_std(arr, d):
    T, N = arr.shape
    out = np.full_like(arr, np.nan)
    for i in range(d, T+1):
        window = arr[i-d:i, :]
        valid = np.isfinite(window)
        with np.errstate(all='ignore'):
            mean = np.nanmean(window, axis=0)
            sq = np.where(np.isfinite(window), (window - mean)**2, 0)
            ss = np.sum(sq, axis=0)
            n = np.sum(valid, axis=0)
            var = ss / np.where(n-1 > 0, n-1, 1)
        out[i-1, :] = np.where(n >= max(2, d//2), np.sqrt(np.maximum(var, 0)), np.nan)
    return out

def ts_max(arr, d):
    T, N = arr.shape
    out = np.full_like(arr, np.nan)
    for i in range(d, T+1):
        out[i-1, :] = np.nanmax(arr[i-d:i, :], axis=0)
    return out

def ts_min(arr, d):
    T, N = arr.shape
    out = np.full_like(arr, np.nan)
    for i in range(d, T+1):
        out[i-1, :] = np.nanmin(arr[i-d:i, :], axis=0)
    return out

def shift_arr(arr, d):
    """shift forward by d (如 shift(1) 表示昨天的值 = arr[t-1])"""
    if d <= 0:
        return arr.copy()
    out = np.full_like(arr, np.nan)
    out[d:, :] = arr[:-d, :]
    return out


def compute_daily_ic(factors, ret_arr, start_t, end_t):
    """
    factors[name] = (T, N) array
    ret_arr = forward ret (T, N) array
    返回 dict[name] -> IC 序列 (Series)
    """
    ic_series = {}
    for name, farr in factors.items():
        ics = []
        for t in range(start_t, min(end_t, farr.shape[0])):
            f = farr[t, :]
            r_val = ret_arr[t, :]
            mask = np.isfinite(f) & np.isfinite(r_val)
            n = mask.sum()
            if n < 30:
                continue
            fv = f[mask]
            rv = r_val[mask]
            if np.std(fv) < 1e-10 or np.std(rv) < 1e-10:
                continue
            ic, _ = spearmanr(fv, rv)
            if not np.isnan(ic):
                ics.append(ic)
        s = pd.Series(ics)
        if len(s) > 0:
            ic_series[name] = {'IC_mean': s.mean(), 'IR': s.mean()/s.std() if s.std()>0 else 0, 'n': len(s)}
    return ic_series


# ----------------------------------------------------------------------
def main():
    print("=" * 50)
    print("Alpha191 IC/IR Pilot (v3, numpy)")
    print("=" * 50)

    df, codes, code_idx, dates_sorted = load_daily()
    arr_dict, all_dates, date_idx = build_lag_arrays(df, codes)

    c  = arr_dict['c'];  h  = arr_dict['h'];   l = arr_dict['l']
    o  = arr_dict['o'];  v  = arr_dict['v'];   a = arr_dict['a']
    vw = arr_dict['vw']; r  = arr_dict['r']

    T, N = c.shape

    # 因子算子预计算
    print("[info] 计算基础算子...")
    mk = {}
    for d in [1,2,3,5,6,10,12,20,24,30,60]:
        mk[f's{d}_c'] = shift_arr(c, d)
        mk[f's{d}_h'] = shift_arr(h, d)
        mk(f's{d}_l'] = shift_arr(l, d)
        mk[f's{d}_v'] = shift_arr(v, d)
        mk[f's{d}_o'] = shift_arr(o, d)
    for d in [3,5,6,10,12,20,24,30,40,60]:
        mk[f'm{d}_c'] = ts_mean(c, d)
        mk[f'm{d}_v'] = ts_mean(v, d)
        mk[f'm{d}_h'] = ts_mean(h, d)
        mk[f'm{d}_l'] = ts_mean(l, d)
    for d in [5,6,10,20,24]:
        mk[f'std{d}_c'] = ts_std(c, d)
        mk[f'std{d}_h'] = ts_std(h, d)
    for d in [5,6,10,12,20,26]:
        mk[f'std{d}_c'] = ts_std(c, d)
    for d in [2,3,5,6,9,12,20]:
        mk[f'mx{d}_h'] = ts_max(h, d)
        mk[f'mn{d}_l'] = ts_min(l, d)

    # forward returns
    fwd3 = np.full_like(c, np.nan)
    fwd5 = np.full_like(c, np.nan)
    fwd10 = np.full_like(c, np.nan)
    fwd3[:-3, :] = c[3:, :] / c[:-3, :] - 1
    fwd5[:-5, :] = c[5:, :] / c[:-5, :] - 1
    fwd10[:-10, :] = c[10:, :] / c[:-10, :] - 1

    # 计算因子面板
    print("[info] 计算 Alpha191 因子面板...")

    # 15 个因子
    F = {}
    F['alpha14'] = (c - mk['s5_c']) / mk['s5_c']            # 5日动量
    F['alpha18'] = c / mk['s5_c']                              # 5日价格比
    F['alpha20'] = (c - mk['s6_c']) / mk['s6_c'] * 100        # 6日动量*100
    F['alpha31'] = (c - mk['m12_c']) / mk['m12_c'] * 100      # 12日均线偏离*100
    F['alpha34'] = mk['m12_c'] / c                             # 12日反均线
    F['alpha46'] = (mk['m3_c']+mk['m6_c']+mk['m12_c']+mk['m24_c']) / (4*c)
    F['alpha65'] = mk['m6_c'] / c
    F['alpha63'] = np.maximum(c - shift_arr(c,1), 0)           # will need rolling — skip
    F['alpha47'] = (mk['mx6_h'] - c) / (mk['mx6_h'] - mk['mn6_l']) * 100  # W%R
    F['alpha57'] = (c - mk['mn9_l']) / (mk['mx9_h'] - mk['mn9_l']) * 100  # Stochastic
    F['alpha2']  = ((c - l) - (h - c)) / (h - l)              # delta done below
    F['alpha71'] = (c - mk['m24_c']) / mk['m24_c'] * 100
   8'] = None  # needs rolling bool — skip

    # delta(alpha2)
    F['alpha2_v2'] = np.zeros_like(c)
    F['alpha2_v2'][1:, :] = ((c - l) - (h - c))[1:, :] / (h - l)[1:, :] - ((c - l_prev) - (h - l_prev))  # deprecated

    print(f"[info] 已计算 ~{len([f for f in F.values() if f is not None])} 个因子面板")

    # 只算 3 个最简单的因子验证框架
    simple_factors = {
        'alpha14_c5': F['alpha14'][(c - mk['s5_c']) / mk['s5_c'] is not None],
    }

    # IC 计算 (在 2021-01-01 之后 = start_t)
    start_t = next((i for i, d in enumerate(all_dates) if d >= np.datetime64('2021-01-01')), 0)
    print(f"[info] start_t = {start_t}, T = {T}")

    # 只测 1 个因子 (alpha14) 跑通框架
    ret3 = fwd3[start_t:, :]
    ret5 = fwd5[start_t:, :]
    ret10 = fwd10[start_t:, :]

    for fname, panel in [('alpha14_momentum5d', F['alpha14']),
                         ('alpha34_ma12_inv', F['alpha34']),
                         ('alpha47_williams_r6_6', F['alpha47']),
                         ('alpha57_stoch9_9', F['alpha57'])]:
        if panel is None:
            continue
        sub = panel[start_t:, :]
        for h, r_panel in [(3, ret3), (5, ret5), (10, ret10)]:
            ics = []
            n_days = sub.shape[0]
            for t in range(n_days):
                f = sub[t, :]
                rv = r_panel[t, :]
                mask = np.isfinite(f) & np.isfinite(rv)
                if mask.sum() < 30: continue
                fv = f[mask]; rrv = rv[mask]
                if np.std(fv) < 1e-10 or np.std(rrv) < 1e-10: continue
                ic, _ = spearmanr(fv, rrv)
                if not np.isnan(ic): ics.append(ic)
            if ics:
                s = pd.Series(ics)
                print(f"  {fname:<30} h={h:<2} IC={s.mean():+.6f}  IR={s.mean()/s.std():+.4f}  n={len(ics)}")
            else:
                print(f"  {fname:<30} h={h:<2} — 无数据")

    print()


if __name__ == '__main__':
    main()
