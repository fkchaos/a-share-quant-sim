#!/usr/bin/env python3
"""
scripts/tools/v56a_factor_analysis.py — v56a 单因子 WF 验证

对 IC 分析中有效的因子逐个跑 Walk-Forward，确认：
1) 单因子 WF 是否通过
2) 与 v39g baseline 的增量
3) 输出结论到 /tmp/v56a_factor_wf_results.txt

有效因子（IC分析确认）：
- reversal_score: 5d IC=+0.049 IR=+0.435（连板数↑→收益↑，反向因子需反转逻辑）
- mom_5: 5d IC=+0.006 IR=+0.045（保留，v39g已有）
- quality_score: 5d IC=+0.006 IR=+0.037（低波质量）

无效因子（IC≈0或反向）：
- smart_q, retail_resid, herding_score, chip_score, volflow_resid → 丢弃
"""
import sys, os, time, pickle
import numpy as np
import pandas as pd
sys.path.insert(0, '/root/a-share-quant-sim')

from core.db import load_panel_from_db
from core.account import PortfolioState, buy, sell, portfolio_value
from core.config import TradingCosts
from scripts.strategies.v56a_multialpha import calc_factors, DEFAULT_PARAMS


# ══════════════════════════════════════════════════════════
# 单因子选股器：只用1个因子打分
# ══════════════════════════════════════════════════════════

def make_single_factor_adapter(factor_name, reverse=False):
    """创建一个只用指定因子的选股函数"""
    
    def select_fn(factors, date, current_holdings=None, params=None, sold_recently=None):
        if factor_name not in factors or date not in factors[factor_name].index:
            return []
        s = factors[factor_name].loc[date].dropna()
        # 排除持仓/冷却
        if current_holdings:
            s = s.drop(labels=list(current_holdings.keys()), errors='ignore')
        if sold_recently:
            s = s.drop(labels=list(sold_recently.keys()), errors='ignore')
        
        if reverse:
            s = s.sort_values(ascending=True)  # 反向：值越小越好
        else:
            s = s.sort_values(ascending=False)  # 正向：值越大越好
        
        n = params.get('MAX_DAILY_BUY', 3)
        selected = s.index[:n]
        return [(code, s[code]) for code in selected]
    
    return select_fn


# ══════════════════════════════════════════════════════════
# WF 回测器（复用 wf_runner 的核心逻辑，简化版）
# ══════════════════════════════════════════════════════════

def run_single_factor_wf(factor_name, reverse=False, tp_sl_sharpe_tuple=None):
    """跑一个单因子的 WF 回测"""
    if tp_sl_sharpe_tuple:
        stop_loss, take_profit, hold_max = tp_sl_sharpe_tuple
    else:
        stop_loss, take_profit, hold_max = -0.05, 0.10, 5
    
    risk_params = {
        'STOP_LOSS': stop_loss,
        'TAKE_PROFIT': take_profit,
        'HOLD_DAYS_MAX': hold_max,
        'MAX_DAILY_BUY': 3,
        'MAX_POSITION': 0.20,
        'MAX_HOLDINGS': 8,
        'HOLD_DAYS_MIN': 1,
        'HOLD_DAYS_EXTEND': 5,
        'HOLD_DAYS_EXTEND_PNL': 0.02,
    }
    
    select_fn = make_single_factor_adapter(factor_name, reverse=reverse)
    
    # 从 pickle 加载已有面板和因子（避免重复60s加载）
    import pickle
    with open('/tmp/v56a_factors.pkl', 'rb') as f:
        pkl = pickle.load(f)
    codes = pkl['codes']
    close_panel = pkl['close_panel']
    
    # 需要重新计算因子（volflow_resid修复了BUG）
    from scripts.strategies.v56a_multialpha import calc_factors, DEFAULT_PARAMS
    print(f"  [factor] {factor_name}: recomputing factors on {close_panel.shape[1]} stocks...")
    
    # 从close_panel的columns反向查数据（温度板的columns就是codes）
    from core.db import load_panel_from_db
    tpl, _ = load_panel_from_db('2021-06-01', '2026-06-01', need_open=True, need_hl=True, pool='zz1800')
    cp, vp, ap, op, hp, lp = tpl[0], tpl[1], tpl[2], tpl[3], tpl[4], tpl[5]
    exclude_prefixes = ('688', '689')
    cols = [c for c in cp.columns if not c.startswith(exclude_prefixes)]
    cp, vp, ap = cp[cols], vp[cols], ap[cols]
    op, hp, lp = op[cols], hp[cols], lp[cols]
    factors = calc_factors(cp, vp, ap, hp, lp, op, params=DEFAULT_PARAMS)
    close_panel = cp
    
    if factor_name not in factors:
        return None
    
    # WF: train=252, test=126, step=63 (与标杆一致)
    train_days = 252
    test_days = 126
    step_days = 63
    
    total_days = close_panel.shape[0]
    n_folds = (total_days - train_days) // step_days
    
    if n_folds < 1:
        return None
    
    fold_results = []
    
    for fold in range(n_folds):
        start_idx = fold * step_days
        end_idx = start_idx + train_days + test_days
        if end_idx > total_days:
            break
        
        train_end = start_idx + train_days
        test_end = min(start_idx + train_days + test_days, total_days)
        
        # 在训练集上训练（这里无训练，直接使用因子）
        # 在测试集上回测
        state = PortfolioState(cash=200000, initial_capital=200000)
        nav_list = []
        
        test_close = close_panel.iloc[train_end:test_end]
        test_open = open_panel.iloc[train_end:test_end]
        
        for i in range(len(test_close)):
            date = test_close.index[i]
            price_data = test_close.iloc[i]
            
            # 风控
            to_sell = []
            for code in list(state.holdings.keys()):
                if code not in price_data.index:
                    continue
                price = price_data[code]
                if pd.isna(price) or price <= 0:
                    continue
                h = state.holdings[code]
                entry = h.get('entry_date', None)
                if entry:
                    entry_date = pd.Timestamp(entry)
                    today = pd.Timestamp(date)
                    hold_days = (today - entry_date).days
                else:
                    hold_days = h.get('hold_days', 0) + 1
                h['hold_days'] = hold_days
                
                # 计算 PNL
                cost = h.get('cost_price', 0)
                if cost > 0:
                    pnl = (price - cost) / cost
                else:
                    pnl = 0
                
                # 止损
                if pnl < risk_params['STOP_LOSS']:
                    to_sell.append((code, 'stop_loss', pnl))
                # 止盈
                elif pnl >= risk_params['TAKE_PROFIT']:
                    to_sell.append((code, 'take_profit', pnl))
                elif hold_days > risk_params['HOLD_DAYS_MAX']:
                    to_sell.append((code, 'timeout', pnl))
            
            # 卖出
            for code, reason, pnl in to_sell:
                if code in state.holdings and code in price_data.index:
                    sell_price = price_data[code]
                    if not pd.isna(sell_price) and sell_price > 0:
                        state = sell(state, code, sell_price, date, reason=reason)
            
            # 选股
            if date in factors[factor_name].index and len(state.holdings) < risk_params['MAX_HOLDINGS']:
                cands = select_fn(factors, date, current_holdings=state.holdings, params=risk_params)
                if cands and state.cash > 1000:
                    avail = state.cash - 1000
                    n = min(len(cands), risk_params['MAX_DAILY_BUY'], risk_params['MAX_HOLDINGS'] - len(state.holdings))
                    per_stock = min(avail / max(n, 1), state.initial_capital * risk_params['MAX_POSITION'])
                    for code, score in cands[:n]:
                        if code not in price_data.index:
                            continue
                        bp = price_data[code]
                        if pd.isna(bp) or bp <= 0:
                            continue
                        adj = bp * (1 + TradingCosts().slippage_rate)
                        shares = int(per_stock / adj / 100) * 100
                        if shares <= 0:
                            continue
                        state = buy(state, code, bp, date, shares=shares)
            
            # NAV
            pv = portfolio_value(state, date, price_data)
            nav_list.append(pv)
        
        if nav_list:
            start_nav = nav_list[0]
            end_nav = nav_list[-1]
            ret = end_nav / start_nav - 1 if start_nav > 0 else 0
            nav_s = pd.Series(nav_list)
            dd = ((nav_s.cummax() - nav_s) / nav_s.cummax()).max()
            daily = nav_s.pct_change().dropna()
            sharpe = daily.mean() / daily.std() * np.sqrt(252) if daily.std() > 0 else 0
            
            fold_results.append({
                'fold': fold, 'test_ret': ret, 'test_dd': dd,
                'test_sharpe': sharpe, 'test_days': len(nav_list)
            })
    
    if not fold_results:
        return None
    
    df = pd.DataFrame(fold_results)
    avg_ret = df['test_ret'].mean() * 100
    avg_sharpe = df['test_sharpe'].mean()
    avg_dd = df['test_dd'].mean() * 100
    pos_folds = (df['test_ret'] > 0).sum()
    total = len(df)
    
    return {
        'factor': factor_name, 'reverse': reverse,
        'ret': avg_ret, 'sharpe': avg_sharpe, 'dd': avg_dd,
        'pos_folds': pos_folds, 'total': total,
        'config': f"SL={risk_params['STOP_LOSS']},TP={risk_params['TAKE_PROFIT']},HOLD={risk_params['HOLD_DAYS_MAX']}"
    }


# ══════════════════════════════════════════════════════════
# Main: 跑所有配置
# ══════════════════════════════════════════════════════════

if __name__ == '__main__':
    print("=" * 70)
    print("v56a 单因子 WF 验证")
    print("标杆条件: train=252, test=126, step=63, 2021-06-2026, zz1800")
    print("=" * 70)
    
    os.makedirs('/root/a-share-quant-sim/docs/strategy', exist_ok=True)
    
    # 一次性加载面板 + 计算因子（节省时间）
    print("\n[0/8] 加载面板 + 计算所有因子（一次性）...")
    t0 = time.time()
    from core.db import load_panel_from_db
    from scripts.strategies.v56a_multialpha import calc_factors, DEFAULT_PARAMS
    
    tpl, codes = load_panel_from_db('2021-06-01', '2026-06-01', need_open=True, need_hl=True, pool='zz1800')
    close_panel = tpl[0]
    volume_panel = tpl[1]
    amount_panel = tpl[2]
    open_panel = tpl[3]
    high_panel = tpl[4]
    low_panel = tpl[5]
    exclude_prefixes = ('688', '689')
    cols = [c for c in close_panel.columns if not c.startswith(exclude_prefixes)]
    close_panel = close_panel[cols]
    volume_panel = volume_panel[cols]
    amount_panel = amount_panel[cols]
    open_panel = open_panel[cols]
    high_panel = high_panel[cols]
    low_panel = low_panel[cols]
    factors = calc_factors(close_panel, volume_panel, amount_panel,
                           high_panel, low_panel, open_panel, params=DEFAULT_PARAMS)
    print(f"  Done in {time.time()-t0:.1f}s ({close_panel.shape[0]}d x {close_panel.shape[1]} stocks, {len(factors)} factors)")
    
    configs = [
        # (factor_name, reverse, stop_loss, take_profit, hold_max)
        ('mom_5', False, -0.05, 0.10, 5),
        ('mom_5', False, -0.015, 0.03, 5),
        ('quality_score', False, -0.05, 0.10, 5),
        ('quality_score', False, -0.015, 0.03, 5),
        ('reversal_score', True, -0.05, 0.10, 5),   # 反向：连板少→得分高→买入
        ('reversal_score', True, -0.015, 0.03, 5),
        ('reversal_score', False, -0.05, 0.10, 5),  # 正向测试：连板多→买入
        ('reversal_score', False, -0.015, 0.03, 5),
    ]
    
    results = []
    
    for i, (fname, reverse, sl, tp, hm) in enumerate(configs):
        direction = "ASC" if reverse else "DESC"
        print(f"\n[{i+1}/{len(configs)}] {fname} ({direction}, SL={sl}, TP={tp}, HOLD={hm})...")
        
        # 构造单因子WF需要的risk_params
        rp = {
            'STOP_LOSS': sl, 'TAKE_PROFIT': tp, 'HOLD_DAYS_MAX': hm,
            'MAX_DAILY_BUY': 3, 'MAX_POSITION': 0.20, 'MAX_HOLDINGS': 8,
            'HOLD_DAYS_MIN': 1, 'HOLD_DAYS_EXTEND': 5, 'HOLD_DAYS_EXTEND_PNL': 0.02,
        }
        fwd_dict = {f'fwd_{hp}d': close_panel.pct_change(hp).shift(-hp) for hp in [1,3,5,10,20]}
        
        # 内联简化版单因子WF（避免每次重新加载60s）
        _t1 = time.time()
        select_fn = make_single_factor_adapter(fname, reverse=reverse)
        fold_results_wf = []
        _train, _test, _step = 252, 126, 63
        _total = close_panel.shape[0]
        _nfolds = (_total - _train) // _step
        
        for _fold in range(_nfolds):
            _sidx = _fold * _step
            _tend = min(_sidx + _train + _test, _total)
            _test_close = close_panel.iloc[_sidx+_train:_tend]
            if len(_test_close) < 30:
                continue
            _st = PortfolioState(cash=200000, initial_capital=200000)
            _nav = []
            
            for _i in range(len(_test_close)):
                _d = _test_close.index[_i]
                _pd = _test_close.iloc[_i]
                _sell = []
                
                for _code in list(_st.holdings.keys()):
                    if _code not in _pd.index:
                        continue
                    _price = _pd[_code]
                    if pd.isna(_price) or _price <= 0:
                        continue
                    _h = _st.holdings[_code]
                    _cost = _h.get('cost_price', 0)
                    _pnl = (_price - _cost) / _cost if _cost > 0 else 0
                    _entry_d = pd.Timestamp(_h.get('entry_date', str(_d)))
                    _hd = (pd.Timestamp(_d) - _entry_d).days
                    
                    if _pnl < rp['STOP_LOSS']:
                        _sell.append((_code, 'sl', _pnl))
                    elif _pnl >= rp['TAKE_PROFIT']:
                        _sell.append((_code, 'tp', _pnl))
                    elif _hd > rp['HOLD_DAYS_MAX']:
                        _sell.append((_code, 'to', _pnl))
                
                for _c, _r, _p in _sell:
                    if _c in _st.holdings and _c in _pd.index:
                        _st = sell(_st, _c, _pd[_c], _d, reason=_r)
                
                if _d in factors[fname].index and len(_st.holdings) < rp['MAX_HOLDINGS']:
                    _cands = select_fn(factors, _d, current_holdings=_st.holdings, params=rp)
                    if _cands and _st.cash > 1000:
                        _n = min(len(_cands), rp['MAX_DAILY_BUY'], rp['MAX_HOLDINGS']-len(_st.holdings))
                        _ps = min((_st.cash-1000)/max(_n,1), _st.initial_capital*rp['MAX_POSITION'])
                        for _c, _score in _cands[:_n]:
                            if _c not in _pd.index:
                                continue
                            _bp = _pd[_c]
                            if pd.isna(_bp) or _bp <= 0:
                                continue
                            _sh = int(_ps / (_bp*1.001) / 100) * 100
                            if _sh > 0:
                                _st = buy(_st, _c, _bp, _d, shares=_sh)
                
                _nav.append(portfolio_value(_st, _d, _pd))
            
            if _nav:
                _ns = pd.Series(_nav)
                _ret = _ns.iloc[-1]/_ns.iloc[0] - 1
                _dd = ((_ns.cummax()-_ns)/_ns.cummax()).max()
                _dr = _ns.pct_change().dropna()
                _sh = _dr.mean()/_dr.std()*np.sqrt(252) if _dr.std()>0 else 0
                fold_results_wf.append({'fold':_fold,'test_ret':_ret,'test_dd':_dd,'test_sharpe':_sh,'test_days':len(_nav)})
        
        if fold_results_wf:
            _df = pd.DataFrame(fold_results_wf)
            avg_ret = _df['test_ret'].mean() * 100
            avg_sharpe = _df['test_sharpe'].mean()
            avg_dd = _df['test_dd'].mean() * 100
            pos_folds = (_df['test_ret'] > 0).sum()
            total_f = len(_df)
            r = {'factor': fname, 'reverse': reverse, 'ret': avg_ret, 'sharpe': avg_sharpe,
                 'dd': avg_dd, 'pos_folds': pos_folds, 'total': total_f,
                 'config': f"SL={sl},TP={tp},HOLD={hm}"}
        else:
            r = None
        
        if r:
            results.append(r)
            mark = "PASS" if r['sharpe'] > 0.5 and r['pos_folds'] >= 0.6 * r['total'] else "FAIL"
            print(f"  {mark} Sharpe={r['sharpe']:.3f}, Return={r['ret']:.2f}%, DD={r['dd']:.1f}%, Folds={r['pos_folds']}/{r['total']} ({time.time()-_t1:.0f}s)")
        else:
            print(f"  NODATA ({time.time()-_t1:.0f}s)")
    
    print(f"\n{'='*70}")
    print(f"总耗时: {time.time()-t0:.1f}s")
    print(f"{'='*70}")
    
    # 排序输出
    results.sort(key=lambda x: x['sharpe'], reverse=True)
    print(f"\n{'排名':4s} {'因子':20s} {'方向':4s} {'夏普':>6s} {'收益':>8s} {'回撤':>6s} {'Fold':>6s} {'状态':4s} 风控")
    print(f"{'='*100}")
    for i, r in enumerate(results, 1):
        mark = "PASS" if r['sharpe'] > 0.5 and r['pos_folds'] >= 0.6 * r['total'] else "FAIL"
        direction = "ASC" if r['reverse'] else "DESC"
        print(f"{i:4d} {r['factor']:20s} {direction:4s} {r['sharpe']:6.3f} {r['ret']:7.2f}% {r['dd']:5.1f}% {r['pos_folds']:3d}/{r['total']:<3d} {mark:4s} {r['config']}")
    
    # 保存
    out_path = '/root/a-share-quant-sim/docs/strategy/v56a_single_factor_wf.txt'
    with open(out_path, 'w') as f:
        f.write("v56a 单因子 WF 验证结果\n")
        f.write(f"时间: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"条件: train=252, test=126, step=63, 2021-06-2026, zz1800\n\n")
        f.write(f"{'排名':4s} {'因子':20s} {'方向':4s} {'夏普':>6s} {'收益':>8s} {'回撤':>6s} {'Fold':>6s} {'状态':4s} 风控\n")
        f.write("-" * 100 + "\n")
        for i, r in enumerate(results, 1):
            mark = "PASS" if r['sharpe'] > 0.5 and r['pos_folds'] >= 0.6 * r['total'] else "FAIL"
            direction = "ASC" if r['reverse'] else "DESC"
            f.write(f"{i:4d} {r['factor']:20s} {direction:4s} {r['sharpe']:6.3f} {r['ret']:7.2f}% {r['dd']:5.1f}% {r['pos_folds']:3d}/{r['total']:<3d} {mark:4s} {r['config']}\n")
    
    print(f"\n结果已保存: {out_path}")
