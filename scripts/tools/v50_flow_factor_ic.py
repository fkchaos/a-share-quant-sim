"""
v50 资金流因子 IC 分析
计算 4 个新资金流因子与未来 N 日收益率的 IC（信息系数）序列。

因子定义：
1. flow_price_vol_diverge (量价背离度): mom_5 - vol_change_5d_norm
   ↑ 价格涨但量缩 = 量价背离（负信号）；价格涨且量增 = 量价共振（正信号）
2. turnover_change_rate (换手率变化率): turnover_5d / turnover_20d
   ↑ 换手率加速 = 资金活跃度上升
3. flow_in_ratio (资金流入占比): up_day_amount / total_amount_5d
   ↑ 上涨日成交额占比高 = 主动买入力量强
4. vol_anomaly (量量比异常度 z-score): (vol_1d - vol_20d_mean) / vol_20d_std
   ↑ 异常放量
"""
import numpy as np
import pandas as pd
from core.db import load_panel_from_db


def compute_flow_factors(close, volume, amount, float_shares_dict=None):
    """
    计算 4 个资金流因子面板。
    
    注意: DB 中 amount 在早期可能为 0，用 close * volume 替代。
    
    Returns
    -------
    dict[str, pd.DataFrame]: {factor_name: panel (date x code)}
    """
    factors = {}
    
    # 修复 amount: 如果 amount 为 0，用 close * volume 替代
    amount_filled = amount.copy()
    mask = (amount_filled == 0) | (amount_filled.isna())
    amount_filled = amount_filled.where(~mask, close * volume)
    
    # ── 因子1: 量价背离度 ──
    # mom_5 = 5日价格动量
    mom_5 = close.pct_change(5)
    
    # vol_change_5d = 5日成交量变化率
    vol_avg_5d = volume.rolling(5).mean()
    vol_prev_5d = volume.shift(5).rolling(5).mean()
    vol_change_5d = vol_avg_5d / (vol_prev_5d + 1) - 1
    
    # 标准化 vol_change 到与 mom_5 同量级
    vol_change_norm = vol_change_5d.rolling(60).apply(
        lambda x: (x.iloc[-1] - x.mean()) / (x.std() + 1e-8), raw=False
    )
    
    # 量价背离 = mom_5 - vol_change_norm（正 = 量价同步上涨，负 = 量价背离）
    factors['flow_diverge'] = mom_5 - vol_change_norm.fillna(0)
    
    # ── 因子2: 换手率变化率 ──
    if float_shares_dict is not None:
        # 真实换手率 = volume / float_shares
        float_shares = pd.Series(float_shares_dict)
        turnover = volume / float_shares.replace(0, np.nan)
    else:
        # 退化为量比
        turnover = volume / volume.rolling(20).mean()
    
    turnover_5d = turnover.rolling(5).mean()
    turnover_20d = turnover.rolling(20).mean()
    factors['turnover_change'] = turnover_5d / (turnover_20d + 1e-8) - 1
    
    # ── 因子3: 资金流入占比 ──
    # 上涨日成交额 / 5日总成交额
    up_day = (close > close.shift(1)).astype(float)
    up_amount = amount_filled * up_day
    total_amount_5d = amount_filled.rolling(5).sum()
    factors['flow_in_ratio'] = up_amount.rolling(5).sum() / (total_amount_5d + 1e-8)
    
    # ── 因子4: 量比异常度 (z-score) ──
    vol_20d_mean = volume.rolling(20).mean()
    vol_20d_std = volume.rolling(20).std()
    factors['vol_anomaly'] = (volume - vol_20d_mean) / (vol_20d_std + 1e-8)
    
    return factors


def compute_factor_ic(factor_panel, close, forward_days=5, min_stocks=50, step=10):
    """
    计算单个因子的 IC 时间序列。
    
    Parameters
    ----------
    factor_panel : pd.DataFrame  因子值面板 (date x code)
    close : pd.DataFrame         收盘价面板
    forward_days : int           前瞻天数
    min_stocks : int             最少有效股票数
    step : int                   采样间隔（天）
    
    Returns
    -------
    dict: { 'ic_series', 'ic_mean', 'ic_std', 'ir', 'n_obs', 'positive_ic_ratio' }
    """
    # 标签：T+1 开盘买入，T+forward_days 日收盘卖出
    buy_price = close.shift(-1)
    sell_price = close.shift(-forward_days)
    future_return = sell_price / buy_price - 1
    
    all_dates = factor_panel.index
    valid_dates = []
    ic_values = []
    
    for i in range(0, len(all_dates) - forward_days - 1, step):
        date = all_dates[i]
        
        if date not in future_return.index:
            continue
        
        f = factor_panel.loc[date]
        r = future_return.loc[date]
        
        # 对齐
        common = f.dropna().index.intersection(r.dropna().index)
        if len(common) < min_stocks:
            continue
        
        f_vals = f.loc[common].values
        r_vals = r.loc[common].values
        
        if not (np.all(np.isfinite(f_vals)) and np.all(np.isfinite(r_vals))):
            continue
        
        # Spearman 秩相关
        f_rank = pd.Series(f_vals).rank()
        r_rank = pd.Series(r_vals).rank()
        ic = f_rank.corr(r_rank, method='spearman')
        
        if np.isfinite(ic):
            ic_values.append(ic)
            valid_dates.append(date)
    
    if len(ic_values) == 0:
        return None
    
    ic_series = pd.Series(ic_values, index=valid_dates)
    ic_mean = np.mean(ic_values)
    ic_std = np.std(ic_values)
    ir = ic_mean / ic_std if ic_std > 0 else 0
    
    return {
        'ic_series': ic_series,
        'ic_mean': ic_mean,
        'ic_std': ic_std,
        'ir': ir,
        'n_obs': len(ic_values),
        'positive_ic_ratio': sum(1 for x in ic_values if x > 0) / len(ic_values)
    }


if __name__ == '__main__':
    import time
    t0 = time.time()
    
    print("=" * 60)
    print("v50 资金流因子 IC 分析")
    print("=" * 60)
    
    # 加载数据
    print("\n加载面板数据 (zz1800, 2023-01-01 起)...")
    panels, codes = load_panel_from_db(
        start_date='2023-01-01', end_date=None,
        need_open=True, need_hl=True, pool='zz1800'
    )
    close, volume, amount, open_, high, low = panels
    print(f"  面板: {close.shape[0]} 天 × {close.shape[1]} 只")
    
    # 获取流通股本
    try:
        from core.db import get_float_shares_map
        float_shares_dict = get_float_shares_map()
        print(f"  流通股本: {len(float_shares_dict)} 只")
    except Exception:
        float_shares_dict = None
        print("  流通股本不可用，退化为量比")
    
    # 计算因子
    print("\n计算资金流因子...")
    factors = compute_flow_factors(close, volume, amount, float_shares_dict)
    for name, panel in factors.items():
        print(f"  {name}: {panel.shape}, 非零占比 {(panel.abs() > 1e-8).sum().sum() / panel.size:.1%}")
    
    # IC 分析
    factor_names = list(factors.keys())
    labels = {
        'flow_diverge': '量价背离度',
        'turnover_change': '换手率变化率',
        'flow_in_ratio': '资金流入占比',
        'vol_anomaly': '量比异常度'
    }
    
    results = {}
    
    for factor_name in factor_names:
        label = labels.get(factor_name, factor_name)
        print(f"\n{'─' * 50}")
        print(f"因子: {factor_name} ({label})")
        print(f"{'─' * 50}")
        
        for fd in [1, 3, 5, 10, 20]:
            result = compute_factor_ic(factors[factor_name], close, forward_days=fd, step=10)
            
            if result is None:
                print(f"  T+{fd:2d}: 无有效数据")
                continue
            
            ic_key = f"{factor_name}_T{fd}"
            results[ic_key] = result
            
            # 判断
            if result['ic_mean'] > 0.03 and result['ir'] > 0.3:
                verdict = "✅ 有效"
            elif result['ic_mean'] > 0.01:
                verdict = "⚠️ 微弱"
            else:
                verdict = "❌ 无效"
            
            print(f"  T+{fd:2d}: IC={result['ic_mean']:+.4f}  IR={result['ir']:+.4f}  "
                  f"正IC占比={result['positive_ic_ratio']:.0%}  "
                  f"n={result['n_obs']:3d}  {verdict}")
    
    # 汇总
    print(f"\n{'=' * 60}")
    print("汇总")
    print(f"{'=' * 60}")
    
    best_results = {}
    for factor_name in factor_names:
        best_ic = -999
        best_key = None
        for key, res in results.items():
            if key.startswith(factor_name) and abs(res['ic_mean']) > abs(best_ic):
                best_ic = res['ic_mean']
                best_key = key
        if best_key:
            best_results[factor_name] = (best_key, results[best_key])
    
    print(f"\n{'因子':<20} {'最佳持有期':<12} {'IC Mean':>10} {'IR':>10} {'正IC占比':>10} {'判断':<10}")
    print("─" * 75)
    for factor_name in factor_names:
        if factor_name in best_results:
            key, res = best_results[factor_name]
            label = labels.get(factor_name, factor_name)
            td = key.split('_T')[1]
            
            if res['ic_mean'] > 0.03 and res['ir'] > 0.3:
                verdict = "✅ 有效"
            elif res['ic_mean'] > 0.01:
                verdict = "⚠️ 微弱"
            else:
                verdict = "❌ 无效"
            
            print(f"{label:<20} T+{td:<9} {res['ic_mean']:>+10.4f} {res['ir']:>+10.4f} "
                  f"{res['positive_ic_ratio']:>9.0%} {verdict:<10}")
    
    elapsed = time.time() - t0
    print(f"\n总耗时: {elapsed:.1f}s")
