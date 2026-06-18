#!/usr/bin/env python3
"""
v20c Walk-Forward 止损止盈参数扫描（DB 数据 + 每天动态选股）
======================================================
从 DB 加载数据，WF 分割，扫描 TP/SL 参数组合。
和 v20_walk_forward.py 的区别：从 DB 加载（非 CSV），扫描 TP/SL 参数。
"""
import sys, os, time, json, numpy as np, pandas as pd
from datetime import datetime

# 强制行缓冲（管道/重定向时确保实时输出）
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'strategies'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from v20_tail_pick import (
    V20Config, calc_tail_pick_factors, select_stocks_tail_pick,
)

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
REPORT_DIR = os.path.join(DATA_DIR, "backtest_results")
os.makedirs(REPORT_DIR, exist_ok=True)


def load_panels_from_db():
    """从 DB 加载 K 线面板"""
    from core.db import load_panel_from_db
    panels, codes = load_panel_from_db(need_hl=True)
    return panels  # (close, volume, amount, high, low, open)


def run_v20_fold(sub_close, sub_volume, sub_amount, sub_high, sub_low, sub_open,
                 tp, sl, hold_days_max, warmup_days=20, label="v20_fold"):
    """跑单个 fold 的回测"""
    factors = calc_tail_pick_factors(sub_close, sub_volume, sub_amount, sub_high, sub_low)

    cfg = V20Config()
    cfg.stop_profit = tp
    cfg.stop_loss = sl
    cfg.hold_days_max = hold_days_max
    initial_capital = cfg.initial_capital
    cash = initial_capital
    holdings = {}
    nav_list = []
    trade_log = []
    dates = sub_close.index
    pending_buy = []

    for i, date in enumerate(dates):
        if i < warmup_days:
            nav_list.append(initial_capital)
            continue
        if date not in sub_close.index:
            nav_list.append(nav_list[-1] if nav_list else initial_capital)
            continue

        price_data = sub_close.loc[date]
        open_data = sub_open.loc[date] if sub_open is not None else price_data

        # 1. 执行待买入（用开盘价）
        if pending_buy and cash > initial_capital * 0.1 and len(holdings) < cfg.max_holdings:
            available_cash = cash - initial_capital * 0.1
            n_buy = min(len(pending_buy), cfg.max_daily_buy, cfg.max_holdings - len(holdings))
            per_stock = available_cash / n_buy if n_buy > 0 else 0
            per_stock = min(per_stock, initial_capital * cfg.max_position)

            for code, score in pending_buy[:n_buy]:
                if code not in open_data.index:
                    continue
                buy_price = open_data[code]
                if pd.isna(buy_price) or buy_price <= 0:
                    continue
                # 排除涨停次日追高
                if i > 0:
                    prev_close = sub_close.iloc[i-1].get(code)
                    if prev_close and not pd.isna(prev_close) and prev_close > 0:
                        if buy_price >= prev_close * 1.09:
                            continue
                adj = buy_price * (1 + cfg.commission_rate + cfg.slippage_rate)
                shares = int(per_stock / adj / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * adj
                if cost > cash:
                    continue
                cash -= cost
                holdings[code] = {'shares': shares, 'cost': buy_price, 'hold_days': 0}
                trade_log.append({'date': str(date.date()), 'code': code, 'action': 'buy',
                                  'price': round(buy_price, 2), 'shares': shares})

        pending_buy = []

        # 2. 更新持仓天数
        for code in holdings:
            holdings[code]['hold_days'] += 1

        # 3. 风控（止盈/止损/超时）
        to_sell = []
        for code, h in list(holdings.items()):
            if code not in price_data.index:
                continue
            cp = price_data[code]
            if pd.isna(cp) or cp <= 0:
                continue
            pnl_pct = (cp - h['cost']) / h['cost']
            if pnl_pct <= cfg.stop_loss:
                to_sell.append((code, 'stop_loss', pnl_pct))
                continue
            if pnl_pct >= cfg.stop_profit:
                to_sell.append((code, 'stop_profit', pnl_pct))
                continue
            if h['hold_days'] >= cfg.hold_days_max:
                to_sell.append((code, 'timeout', pnl_pct))
                continue

        sold_codes = set()
        for code, reason, pnl_pct in to_sell:
            if code in price_data.index:
                sell_price = price_data[code]
                if pd.isna(sell_price) or sell_price <= 0:
                    continue
                h = holdings[code]
                sv = h['shares'] * sell_price * (1 - cfg.commission_rate - cfg.stamp_tax - cfg.slippage_rate)
                cash += sv
                trade_log.append({'date': str(date.date()), 'code': code, 'action': 'sell',
                                  'reason': reason, 'pnl_pct': round(pnl_pct * 100, 2)})
                sold_codes.add(code)
        for code in sold_codes:
            holdings.pop(code, None)

        # 4. 选股（每天动态调用，排除已持仓）
        if len(holdings) < cfg.max_holdings:
            candidates = select_stocks_tail_pick(
                factors, date, sub_close, sub_volume, sub_amount, sub_high, sub_low,
                current_holdings=list(holdings.keys())
            )
            if candidates:
                # 限制新股数量：卖出后持仓 + 新股 <= MAX_HOLDINGS
                max_new = max(0, cfg.max_holdings - len(holdings))
                candidates = candidates[:max_new]
                # 评分排序
                vol_ratio = factors['vol_ratio'].loc[date] if date in factors['vol_ratio'].index else pd.Series()
                range_ratio = factors['range_ratio'].loc[date] if date in factors['range_ratio'].index else pd.Series()
                recent_lu = factors['recent_limit_up'].loc[date] if date in factors['recent_limit_up'].index else pd.Series()
                scored = []
                for code in candidates:
                    vr = vol_ratio.get(code, 999)
                    rr = range_ratio.get(code, 999)
                    lu = recent_lu.get(code, 0)
                    score = (1.0 / (vr + 0.1)) * 2.0 + (1.0 / (rr + 0.1)) * 1.0 + lu * 0.5
                    scored.append((code, score))
                scored.sort(key=lambda x: x[1], reverse=True)
                pending_buy = scored[:cfg.max_daily_buy]

        # 5. NAV
        pv = cash
        for code, h in holdings.items():
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    pv += h['shares'] * p
        nav_list.append(pv)

    nav = pd.Series(nav_list, index=dates[:len(nav_list)])
    rets = nav.pct_change().dropna()
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    days = max((nav.index[-1] - nav.index[0]).days, 1)
    years = days / 365
    ann_ret = (1 + total_ret) ** (1 / years) - 1
    ann_vol = rets.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol > 0 else 0
    peak = nav.cummax()
    max_dd = ((nav - peak) / peak).min()
    sells = [t for t in trade_log if t['action'] == 'sell']
    wins = [t for t in sells if t.get('pnl_pct', 0) > 0]
    win_rate = len(wins) / len(sells) * 100 if sells else 0

    return {
        'annual_return': ann_ret, 'sharpe': sharpe, 'max_dd': max_dd,
        'win_rate': win_rate, 'total_trades': len(trade_log),
    }


def main():
    print("=" * 60)
    print("v20c Walk-Forward 止损止盈参数扫描（DB 数据）")
    print("=" * 60)

    print("\n[1/3] 加载数据...")
    t0 = time.time()
    panels = load_panels_from_db()
    close_panel = panels[0]
    volume_panel = panels[1]
    amount_panel = panels[2]
    high_panel = panels[3]
    low_panel = panels[4]
    open_panel = panels[5] if len(panels) > 5 else panels[0]
    print(f"  Panel: {close_panel.shape[0]} 天 × {close_panel.shape[1]} 只")
    print(f"  耗时 {time.time()-t0:.1f}s")

    dates = close_panel.index
    n = len(dates)
    train_days, test_days, step_days = 252, 252, 252

    # TP/SL 扫描范围
    SL_RANGE = [-0.02, -0.03, -0.04, -0.05, -0.07, -0.10]
    TP_RANGE = [0.05, 0.08, 0.10, 0.15, 0.20, 0.25, 0.30]
    HOLD_DAYS = 5  # 固定 hold_days_max = 5（与模拟盘一致，min=1 max=5，靠 TP/SL 主动卖出）

    total_combos = len(SL_RANGE) * len(TP_RANGE)
    all_results = []
    combo_idx = 0

    print(f"\n[2/3] WF 扫描 {total_combos} 组参数 (train={train_days}d, test={test_days}d)")
    print(f"{'='*70}")

    for sl in SL_RANGE:
        for tp in TP_RANGE:
            combo_idx += 1
            fold_results = []
            fold = 0
            train_end = train_days

            while train_end + test_days <= n:
                fold += 1
                train_start = max(0, train_end - train_days)
                test_start = train_end
                test_end = min(n, train_end + test_days)

                window_dates = dates[train_start:test_end]
                sub_close = close_panel.loc[window_dates]
                sub_volume = volume_panel.loc[window_dates]
                sub_amount = amount_panel.loc[window_dates]
                sub_high = high_panel.loc[window_dates]
                sub_low = low_panel.loc[window_dates]
                sub_open = open_panel.loc[window_dates]
                warmup = train_end - train_start

                m = run_v20_fold(sub_close, sub_volume, sub_amount, sub_high, sub_low, sub_open,
                                 tp=tp, sl=sl, hold_days_max=HOLD_DAYS,
                                 warmup_days=warmup, label=f"v20c_wf{fold}")

                test_start_date = dates[test_start].date()
                test_end_date = dates[test_end - 1].date()

                fold_results.append({
                    'fold': fold,
                    'test_period': f"{test_start_date}~{test_end_date}",
                    'ann_return': m['annual_return'],
                    'sharpe': m['sharpe'],
                    'max_dd': m['max_dd'],
                    'trades': m['total_trades'],
                    'win_rate': m['win_rate'],
                })

                train_end += step_days

            # 汇总该参数组合
            positive_folds = sum(1 for r in fold_results if r['ann_return'] > 0)
            avg_ret = np.mean([r['ann_return'] for r in fold_results])
            avg_sharpe = np.mean([r['sharpe'] for r in fold_results])
            avg_maxdd = np.mean([r['max_dd'] for r in fold_results])
            avg_winrate = np.mean([r['win_rate'] for r in fold_results])
            avg_trades = np.mean([r['trades'] for r in fold_results])

            result = {
                'SL': f"{sl:.0%}",
                'TP': f"{tp:.0%}",
                'hold_max': HOLD_DAYS,
                '年化': f"{avg_ret:.1%}",
                '夏普': f"{avg_sharpe:.2f}",
                '回撤': f"{avg_maxdd:.1%}",
                '胜率': f"{avg_winrate:.0f}%",
                '交易': f"{avg_trades:.0f}",
                '正fold': f"{positive_folds}/{len(fold_results)}",
                '正fold率': f"{positive_folds/len(fold_results):.0%}",
            }
            all_results.append(result)
            sys.stdout.write(f"\r  [{combo_idx}/{total_combos}] SL={result['SL']:>4} TP={result['TP']:>4} → "
                  f"年化={result['年化']:>6} 夏普={result['夏普']:>5} "
                  f"回撤={result['回撤']:>6} 胜率={result['胜率']:>5} "
                  f"正fold={result['正fold']}   ")
            sys.stdout.flush()

    # 输出汇总
    print(f"\n{'='*70}")
    print("📊 v20c WF 结果（按夏普降序）")
    print("=" * 70)
    df = pd.DataFrame(all_results)
    df['sv'] = df['夏普'].astype(float)
    df_sorted = df.sort_values('sv', ascending=False)
    print(df_sorted[['SL', 'TP', 'hold_max', '年化', '夏普', '回撤', '胜率', '交易', '正fold']].to_string(index=False))

    # 保存
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    csv_path = f"{REPORT_DIR}/v20c_wf_sl_tp_{ts}.csv"
    df_sorted.to_csv(csv_path, index=False)
    print(f"\n✅ 结果已保存: {csv_path}")

    # 最佳参数
    best = df_sorted.iloc[0]
    print(f"\n🏆 最佳参数: SL={best['SL']} TP={best['TP']} hold={best['hold_max']}天")
    print(f"   年化={best['年化']} 夏普={best['夏普']} 回撤={best['回撤']} 正fold={best['正fold']}")


if __name__ == '__main__':
    main()
