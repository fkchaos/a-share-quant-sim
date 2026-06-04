#!/usr/bin/env python3
"""
v8 Walk-Forward 过拟合验证
==========================
滚动窗口：训练集 2 年 → 测试集 6 个月，步长 6 个月
基于 factor_fair_compare 的回测引擎（带行业限制）
"""
import sys, os, json, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

import numpy as np
import pandas as pd

from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.account import (PortfolioState, buy, sell, check_stop_loss,
                          check_take_profit, apply_holding_decay, portfolio_value)
from core.config import config as core_config, STRATEGY_PROFILES

# ── v8 参数 ──
PROFILE = STRATEGY_PROFILES['v8_all_icir']
FACTOR_WEIGHTS = PROFILE.factor_weights
TOP_N = PROFILE.top_n
REBAL_FREQ = PROFILE.rebalance_freq
STOP_LOSS = PROFILE.stop_loss
MAX_IND = PROFILE.max_industry_weight
USE_TP = PROFILE.use_take_profit
TP_TIERS = PROFILE.tp_tiers
USE_DECAY = PROFILE.use_holding_decay
USE_VOL_SCALING = PROFILE.use_vol_scaling
VOL_TARGET = PROFILE.vol_target
MAX_POSITION = PROFILE.max_position
INITIAL_CAPITAL = core_config.costs.initial_capital

# ── 行业映射（股票代码前2位 → 行业大类）──
def get_industry(code):
    """简化行业映射"""
    prefix_map = {
        '60': '金融', '00': '制造', '30': '医药', '68': '科技',
    }
    # 简单按前2位分组
    return code[:2]

# ── 数据加载 ──────────────────────────────────────────────────────
def load_panel_global():
    """加载全部数据，用于切片"""
    files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
    all_data = {}
    for f in files:
        code = f.replace(".csv", "")
        df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
        if len(df) > 0:
            all_data[code] = df

    valid = {}
    for code, df in all_data.items():
        if df.index.min() <= pd.Timestamp('2021-01-01') + pd.Timedelta(days=60) and \
           df.index.max() >= pd.Timestamp('2025-12-31') - pd.Timedelta(days=60):
            valid[code] = df

    close_panel = pd.DataFrame({c: d['close'] for c, d in valid.items()})
    volume_panel = pd.DataFrame({c: d['volume'] for c, d in valid.items()})
    amount_panel = pd.DataFrame({c: d.get('amount', d['close'] * d['volume']) for c, d in valid.items()})
    common_dates = close_panel.dropna(how='all').index.sort_values()
    common_dates = common_dates[(common_dates >= '2021-01-01') & (common_dates <= '2026-05-31')]
    return (
        close_panel.loc[common_dates],
        volume_panel.loc[common_dates],
        amount_panel.loc[common_dates],
        list(valid.keys())
    )

# ── 回测引擎（基于 factor_fair_compare，支持行业限制）──
def run_bt(close_panel, score, stock_codes):
    state = PortfolioState(cash=INITIAL_CAPITAL, initial_capital=INITIAL_CAPITAL)
    dates = close_panel.index
    nav_list = []

    for i, date in enumerate(dates):
        if i < 120:
            nav_list.append(INITIAL_CAPITAL)
            continue
        if date not in close_panel.index:
            nav_list.append(nav_list[-1] if nav_list else INITIAL_CAPITAL)
            continue

        price_data = close_panel.loc[date]

        # 止损
        state = check_stop_loss(state, date, price_data)

        # 止盈
        if USE_TP and TP_TIERS:
            state = check_take_profit(state, date, price_data, TP_TIERS)

        # 持有期 decay
        if USE_DECAY:
            state = apply_holding_decay(state, date, price_data, REBAL_FREQ)

        # 调仓
        if (i - 120) % REBAL_FREQ == 0 and date in score.index:
            day_score = score.loc[date].dropna()
            valid_idx = day_score.index.isin(price_data.dropna().index)
            day_score = day_score[valid_idx]

            if len(day_score) > 0:
                # 行业限制选股
                if MAX_IND and MAX_IND > 0:
                    top_stocks = []
                    ind_count = {}
                    max_per_ind = max(1, int(MAX_IND * TOP_N))
                    for code in day_score.sort_values(ascending=False).index:
                        ind = get_industry(code)
                        if ind_count.get(ind, 0) < max_per_ind:
                            top_stocks.append(code)
                            ind_count[ind] = ind_count.get(ind, 0) + 1
                        if len(top_stocks) >= TOP_N:
                            break
                else:
                    top_stocks = day_score.nlargest(TOP_N).index.tolist()

                if top_stocks:
                    current_pv = portfolio_value(state, date, price_data)

                    # 卖出不在目标列表中的持仓
                    for c in list(state.holdings.keys()):
                        if c not in top_stocks and c in price_data.index:
                            p = price_data[c]
                            if not pd.isna(p) and p > 0:
                                state = sell(state, c, p, date, 0)

                    # 买入
                    for c in top_stocks:
                        if c not in state.holdings and c in price_data.index:
                            p = price_data[c]
                            if pd.isna(p) or p <= 0:
                                continue

                            target_val = min(current_pv / len(top_stocks), current_pv * MAX_POSITION)
                            adj_p = p * (1 + core_config.costs.slippage_rate)
                            shares = int(target_val / adj_p / 100) * 100
                            if shares > 0 and state.cash >= shares * adj_p:
                                state = buy(state, c, p, date, shares=shares)

        # 记录 NAV
        dv = portfolio_value(state, date, price_data)
        nav_list.append(dv)

    nav = pd.Series(nav_list, index=dates)
    return nav


# ── 绩效指标 ──────────────────────────────────────────────────────
def calc_metrics(nav_series):
    rets = nav_series.pct_change().dropna()
    total_ret = nav_series.iloc[-1] / nav_series.iloc[0] - 1
    days = (nav_series.index[-1] - nav_series.index[0]).days
    years = max(days / 365, 0.01)
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = rets.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    peak = nav_series.cummax()
    max_dd = ((nav_series - peak) / peak).min()
    calmar = ann_ret / abs(max_dd) if max_dd != 0 else 0
    return {
        'total_return': round(total_ret * 100, 2),
        'annual_return': round(ann_ret * 100, 2),
        'annual_vol': round(ann_vol * 100, 2),
        'sharpe': round(sharpe, 3),
        'max_drawdown': round(max_dd * 100, 2),
        'calmar': round(calmar, 3),
        'days': days,
    }


# ── Walk-Forward 主循环 ───────────────────────────────────────────
def main():
    print("=" * 60)
    print("v8 Walk-Forward 过拟合验证")
    print("=" * 60)

    # 加载全部数据
    print("\n加载数据...")
    t0 = time.time()
    close_all, vol_all, amt_all, codes = load_panel_global()
    print(f"  {len(codes)} 只股票, {len(close_all)} 个交易日, 耗时 {time.time()-t0:.1f}s")

    # 滚动窗口参数
    TRAIN_YEARS = 2
    TEST_MONTHS = 6
    STEP_MONTHS = 6

    start = pd.Timestamp('2021-01-01')
    end = pd.Timestamp('2026-05-31')

    folds = []
    current = start + pd.DateOffset(years=TRAIN_YEARS)
    while current + pd.DateOffset(months=TEST_MONTHS) <= end:
        train_start = current - pd.DateOffset(years=TRAIN_YEARS)
        train_end = current - pd.Timedelta(days=1)
        test_start = current
        test_end = current + pd.DateOffset(months=TEST_MONTHS) - pd.Timedelta(days=1)
        folds.append({
            'train': (str(train_start.date()), str(train_end.date())),
            'test': (str(test_start.date()), str(test_end.date())),
        })
        current += pd.DateOffset(months=STEP_MONTHS)

    print(f"\n共 {len(folds)} 个 fold:")
    for i, f in enumerate(folds):
        print(f"  Fold {i+1}: 训练 {f['train'][0]}~{f['train'][1]} → 测试 {f['test'][0]}~{f['test'][1]}")

    results = []

    for i, fold in enumerate(folds):
        t0 = time.time()
        print(f"\n{'─'*50}")
        print(f"Fold {i+1}/{len(folds)}: 测试期 {fold['test'][0]} ~ {fold['test'][1]}")
        sys.stdout.flush()

        # 切片：需要前120天预热
        warmup_start = pd.Timestamp(fold['test'][0]) - pd.Timedelta(days=180)
        test_end = fold['test'][1]

        close_slice = close_all[(close_all.index >= warmup_start) & (close_all.index <= test_end)]
        vol_slice = vol_all[(vol_all.index >= warmup_start) & (vol_all.index <= test_end)]
        amt_slice = amt_all[(amt_all.index >= warmup_start) & (amt_all.index <= test_end)]

        if len(close_slice) < 150:
            print(f"  ⚠️ 数据不足，跳过")
            continue

        # 计算因子
        try:
            factors = calc_factors_panel(close_slice, vol_slice, amt_slice)
        except Exception as e:
            print(f"  ❌ 因子计算失败: {e}")
            continue

        # 合成评分（只用 v8 权重中存在的因子）
        available = {k: v for k, v in FACTOR_WEIGHTS.items() if k in factors}
        score = composite_score(factors, available)

        # 回测
        nav = run_bt(close_slice, score, codes)

        # 截取测试期
        test_start = pd.Timestamp(fold['test'][0])
        test_nav = nav[(nav.index >= test_start) & (nav.index <= test_end)]

        if len(test_nav) < 10:
            print(f"  ⚠️ 测试期数据不足")
            continue

        metrics = calc_metrics(test_nav)
        metrics['fold'] = i + 1
        metrics['test_period'] = f"{fold['test'][0]}~{fold['test'][1]}"
        results.append(metrics)

        elapsed = time.time() - t0
        print(f"  年化: {metrics['annual_return']}% | Sharpe: {metrics['sharpe']} | "
              f"回撤: {metrics['max_drawdown']}% | Calmar: {metrics['calmar']} | "
              f"{elapsed:.1f}s")
        sys.stdout.flush()

    # ── 汇总 ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("Walk-Forward 汇总")
    print(f"{'='*60}")

    if not results:
        print("❌ 没有有效 fold")
        return

    df = pd.DataFrame(results)
    print(f"\n{'Fold':>4} | {'测试期':>22} | {'年化%':>7} | {'Sharpe':>7} | {'回撤%':>7} | {'Calmar':>7}")
    print("-" * 75)
    for _, r in df.iterrows():
        print(f"{int(r['fold']):>4} | {r['test_period']:>22} | {r['annual_return']:>7.2f} | "
              f"{r['sharpe']:>7.3f} | {r['max_drawdown']:>7.2f} | {r['calmar']:>7.3f}")

    print(f"\n{'─'*40}")
    print(f"平均年化:   {df['annual_return'].mean():.2f}%  (std: {df['annual_return'].std():.2f}%)")
    print(f"平均Sharpe: {df['sharpe'].mean():.3f}  (std: {df['sharpe'].std():.3f})")
    print(f"平均回撤:   {df['max_drawdown'].mean():.2f}%")
    print(f"平均Calmar: {df['calmar'].mean():.3f}")
    print(f"正收益fold: {(df['annual_return'] > 0).sum()}/{len(df)} ({(df['annual_return'] > 0).mean()*100:.0f}%)")
    print(f"Sharpe>1:   {(df['sharpe'] > 1).sum()}/{len(df)}")

    # 保存结果
    out_dir = os.path.join(DATA_DIR, "backtest_results")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, f"walk_forward_v8_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
    with open(out_file, 'w') as f:
        json.dump({
            'strategy': 'v8_all_icir',
            'folds': results,
            'summary': {
                'avg_annual_return': round(df['annual_return'].mean(), 2),
                'avg_sharpe': round(df['sharpe'].mean(), 3),
                'avg_max_drawdown': round(df['max_drawdown'].mean(), 2),
                'avg_calmar': round(df['calmar'].mean(), 3),
                'positive_folds': int((df['annual_return'] > 0).sum()),
                'total_folds': len(df),
            }
        }, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {out_file}")

    return results


if __name__ == '__main__':
    main()
