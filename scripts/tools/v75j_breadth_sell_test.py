#!/usr/bin/env python3
"""v75j 广度卖出策略对比测试

测试4种方案：
A. 现状：广度<30%不卖（只阻止新买入）
B. 广度<30%全部清仓
C. 广度<30%减半仓（留一半）


D. 广度<30%收紧止损（止损从-8%→-5%）
"""
import sys, os, time
sys.path.insert(0, '/root/a-share-quant-sim')
import numpy as np, pandas as pd
from core.db import load_panel_from_db
from core.account import PortfolioState
from scripts.backtest.strategy_adapter import get_adapter

# 标准WF参数
TRAIN_DAYS = 252
TEST_DAYS = 126
STEP_DAYS = 63
START_DATE = '2021-01-01'
END_DATE = '2026-05-31'

def load_data():
    print("[1] 加载数据...")
    t0 = time.time()
    tpl, codes = load_panel_from_db(START_DATE, END_DATE, need_open=True, need_hl=True, pool='zz800')
    cp, vp, ap, op, hp, lp = tpl
    # 排除科创板
    keep = [c for c in cp.columns if not c.startswith(('688','689'))]
    cp, vp, ap, op, hp, lp = cp[keep], vp[keep], ap[keep], op[keep], hp[keep], lp[keep]
    print(f"  {cp.shape[0]}天 × {cp.shape[1]}只 ({time.time()-t0:.1f}s)")
    return cp, vp, ap, op, hp, lp

def calc_breadth(close_panel, date, ma_period=20):
    """计算广度"""
    import sqlite3
    conn = sqlite3.connect('data/quant_stocks.db')
    tech_codes = []
    for sector in ['电子', '计算机', '通信', '传媒']:
        rows = conn.execute("SELECT code FROM industry_map WHERE industry=?", (sector,)).fetchall()
        tech_codes.extend([r[0] for r in rows])
    conn.close()
    tech_codes = list(set(tech_codes))

    pos = close_panel.index.get_loc(date)
    if isinstance(pos, slice): pos = pos.start
    if pos < ma_period: return 1.0

    above = total = 0
    for c in tech_codes:
        if c in close_panel.columns:
            arr = close_panel[c].values
            if np.isnan(arr[pos]) or arr[pos] <= 0: continue
            total += 1
            ma = np.nanmean(arr[pos-ma_period+1:pos+1])
            if arr[pos] > ma: above += 1
    return above / total if total > 0 else 1.0

def run_backtest(cp, vp, ap, op, hp, lp, mode='A', train_days=TRAIN_DAYS, test_days=TEST_DAYS):
    """运行单个fold回测"""
    adapter = get_adapter()
    dates = cp.index.tolist()
    results = []
    i = 0
    while i + train_days + test_days <= len(dates):
        test_start = dates[i + train_days]
        test_end = dates[i + train_days + test_days - 1]
        test_dates = dates[i + train_days: i + train_days + test_days]

        state = PortfolioState(cash=200000, initial_capital=200000)
        nav_list = []
        params = adapter.get_risk_params('v75j')

        for j, date in enumerate(test_dates):
            price_data = cp.loc[date]

            # 广度
            breadth = calc_breadth(cp, date, params.get('BREADTH_MA', 20))

            # 更新hold_days
            for code in list(state.holdings.keys()):
                h = state.holdings[code]
                h['hold_days'] = h.get('hold_days', 0) + 1

            # 风控检查
            to_sell = adapter.risk_check('v75j', state, date, price_data, params)

            # 方案B/C/D: 广度卖出逻辑
            if mode == 'B' and breadth < 0.30:
                # 全部清仓
                for code in list(state.holdings.keys()):
                    if code not in [c for c,_,_ in to_sell]:
                        to_sell.append((code, 'breadth_exit', 0.0))
            elif mode == 'C' and breadth < 0.30:
                # 减半仓（保留持仓数一半）
                n_hold = len(state.holdings)
                n_keep = max(1, n_hold // 2)
                if n_hold > n_keep:
                    # 按PnL排序，卖出亏损最多的
                    items = [(c, (price_data.get(c,0) - h['cost_price'])/h['cost_price'])
                             for c, h in state.holdings.items() if c in price_data.index and c not in [x for x,_,_ in to_sell]]
                    items.sort(key=lambda x: x[1])
                    for code, _ in items[:n_hold - n_keep]:
                        to_sell.append((code, 'breadth_half', 0.0))
            elif mode == 'D' and breadth < 0.30:
                # 收紧止损到-5%
                for code, h in list(state.holdings.items()):
                    if code in price_data.index and code not in [c for c,_,_ in to_sell]:
                        cp_val = price_data[code]
                        if not pd.isna(cp_val) and cp_val > 0:
                            pnl = (cp_val - h['cost_price']) / h['cost_price']
                            if pnl <= -0.05:
                                to_sell.append((code, 'tight_stop', pnl))

            # 执行卖出
            for code, reason, pnl in to_sell:
                if code in state.holdings and code in price_data.index:
                    sp = price_data[code]
                    if not pd.isna(sp) and sp > 0:
                        state.cash += state.holdings[code]['shares'] * sp * 0.9987
                        del state.holdings[code]

            # 选股买入
            from scripts.strategies.v75j_liquidity_only import select_stocks_v75j, calc_factors_v75j
            factors = calc_factors_v75j(cp.iloc[:cp.index.get_loc(date)+1], vp.iloc[:cp.index.get_loc(date)+1],
                                         ap.iloc[:cp.index.get_loc(date)+1], hp.iloc[:cp.index.get_loc(date)+1],
                                         lp.iloc[:cp.index.get_loc(date)+1], op.iloc[:op.index.get_loc(date)+1])

            # 广度过滤买入
            if breadth >= 0.30:
                effective_params = dict(params)
                if breadth < 0.50:
                    effective_params['MAX_HOLDINGS'] = max(1, int(params['MAX_HOLDINGS'] * breadth / 0.50))
                cands = select_stocks_v75j(factors, date, cp.iloc[:cp.index.get_loc(date)+1], vp.iloc[:cp.index.get_loc(date)+1],
                                            ap.iloc[:cp.index.get_loc(date)+1], hp.iloc[:cp.index.get_loc(date)+1],
                                            lp.iloc[:lp.index.get_loc(date)+1], op.iloc[:op.index.get_loc(date)+1],
                                            state.holdings, effective_params)
                for code, score in cands[:effective_params.get('MAX_DAILY_BUY', 3)]:
                    if code not in state.holdings and code in price_data.index:
                        sp = price_data[code]
                        if not pd.isna(sp) and sp > 0 and sp <= params.get('MAX_STOCK_PRICE', 300):
                            max_pos = state.cash * params.get('MAX_POSITION', 0.35)
                            shares = int(max_pos / sp / 100) * 100
                            if shares > 0:
                                cost = shares * sp * 1.0003
                                if cost <= state.cash:
                                    state.cash -= cost
                                    state.holdings[code] = {'shares': shares, 'cost_price': sp, 'hold_days': 0, 'entry_date': str(date)}

            # 计算NAV
            val = state.cash
            for code, h in state.holdings.items():
                if code in price_data.index:
                    p = price_data[code]
                    if not pd.isna(p): val += h['shares'] * p
            nav_list.append(val)

        if len(nav_list) < 10: continue
        nav = pd.Series(nav_list)
        total_ret = (nav.iloc[-1] / nav.iloc[0] - 1) * 100
        daily_ret = nav.pct_change().dropna()
        sharpe = daily_ret.mean() / daily_ret.std() * np.sqrt(252) if daily_ret.std() > 0 else 0
        dd = (nav / nav.cummax() - 1).min() * 100
        results.append({'sharpe': sharpe, 'total': total_ret, 'dd': dd})
        i += STEP_DAYS

    if not results: return None
    avg_sharpe = np.mean([r['sharpe'] for r in results])
    avg_ret = np.mean([r['total'] for r in results])
    avg_dd = np.mean([r['dd'] for r in results])
    pos_folds = sum(1 for r in results if r['sharpe'] > 0)
    return {'sharpe': avg_sharpe, 'total': avg_ret, 'dd': avg_dd, 'folds': len(results), 'pos_folds': pos_folds}

if __name__ == '__main__':
    cp, vp, ap, op, hp, lp = load_data()
    print()
    for mode in ['A', 'B', 'C', 'D']:
        label = {'A': '现状(不卖)', 'B': '广度<30%全清', 'C': '广度<30%减半', 'D': '广度<30%收紧止损-5%'}[mode]
        print(f"=== 方案{mode}: {label} ===")
        t0 = time.time()
        r = run_backtest(cp, vp, ap, op, hp, lp, mode=mode)
        if r:
            print(f"  Sharpe={r['sharpe']:.3f}  Return={r['total']:+.1f}%  DD={r['dd']:.1f}%  Folds={r['folds']}({r['pos_folds']}+)")
        else:
            print("  无结果")
        print(f"  耗时 {time.time()-t0:.1f}s")
        print()
