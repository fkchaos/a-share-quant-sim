#!/usr/bin/env python3
"""
账户2 模拟盘交易脚本 (v27)
===========================
策略：价量共振动量（mom_5 > 2% + pv_corr 价量共振 + gap/illiq/boll）
账户：数据库 account_id=2

时间线：
  11:45  intraday_signal  — 上午出信号（选股+风控）
  13:00  intraday_execute — 下午执行（先卖后买）
  15:30  report_only       — 收盘报告

回测验证（2022-2026）：
  全量：251%/5.72/-6.7%
  WF：15/15正收益，夏普8.66，回撤1.74% ✅

参数：SL=-1.5% TP=3% hold=5
"""
import sys, os, json, time, logging
from datetime import datetime
import pandas as pd
import numpy as np

sys.path.insert(0, "/root/a-share-quant-sim")
sys.path.insert(0, os.path.dirname(__file__))

from core.account import PortfolioState, buy, sell, portfolio_value
from core.config import TradingCosts
from core.db import get_kline, get_all_codes

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
PORTFOLIO_DIR = os.environ.get("PORTFOLIO_DIR", os.path.join(DATA_DIR, "portfolio"))
os.makedirs(PORTFOLIO_DIR, exist_ok=True)

V27_PLAN_FILE = os.path.join(PORTFOLIO_DIR, "trade_plan_v27.json")

from core.strategy_map import load_strategy

# v27 策略参数（从 strategy_map 统一读取）
_sp = load_strategy("v27")["params"]
STOP_LOSS = _sp["STOP_LOSS"]
TAKE_PROFIT = _sp["TAKE_PROFIT"]
MAX_HOLDINGS = _sp["MAX_HOLDINGS"]
MAX_DAILY_BUY = _sp["MAX_DAILY_BUY"]
MAX_POSITION = _sp["MAX_POSITION"]
HOLD_DAYS_MAX = _sp["HOLD_DAYS_MAX"]
HOLD_DAYS_MIN = _sp["HOLD_DAYS_MIN"]
MOM_THRESHOLD = _sp["MOM_THRESHOLD"]
_costs = TradingCosts(); SLIPPAGE_RATE = _costs.slippage_rate
COMMISSION_RATE = _costs.commission_rate; STAMP_TAX = 0.001

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("sim_v27")


def load_account():
    from core.db import load_account_for_sim, get_account, get_stock_name_map
    state, loaded = load_account_for_sim(account_id=2)
    if loaded:
        # 补全 holdings name
        name_map = get_stock_name_map()
        for code, h in state.holdings.items():
            if not h.get("name") or h["name"] == code:
                h["name"] = name_map.get(code, code)
        return state
    acct = get_account(2)
    capital = acct["initial_capital"] if acct else 100000
    return PortfolioState(cash=capital, initial_capital=capital, holdings={}, trade_log=[])


def save_account(state):
    from core.db import save_account_for_sim
    save_account_for_sim(state, account_id=2)
    logger.info(f"账户已保存: 现金 ¥{state.cash:,.0f}, 持仓 {len(state.holdings)} 只")


def load_panel(codes, min_days=60):
    code_dfs = {}
    for code in codes:
        kl = get_kline(code)
        if kl and len(kl) > min_days:
            df = pd.DataFrame(kl)
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date').sort_index()
            df = df[df["volume"] > 0]
            if len(df) > min_days: code_dfs[code] = df
    if not code_dfs: return None
    return (
        pd.DataFrame({c: code_dfs[c]['close'] for c in code_dfs}),
        pd.DataFrame({c: code_dfs[c]['volume'] for c in code_dfs}),
        pd.DataFrame({c: code_dfs[c].get('amount', code_dfs[c]['close'] * code_dfs[c]['volume']) for c in code_dfs}),
        pd.DataFrame({c: code_dfs[c].get('high', code_dfs[c]['close']) for c in code_dfs}),
        pd.DataFrame({c: code_dfs[c].get('low', code_dfs[c]['close']) for c in code_dfs}),
        pd.DataFrame({c: code_dfs[c].get('open', code_dfs[c]['close']) for c in code_dfs}),
    )


def calc_factors(cp, vp, ap, hp, lp, op=None):
    eps = 1e-10; ret = cp.pct_change(); m5 = cp.pct_change(5)
    prev = cp.shift(1); gap = (op - prev) / (prev + eps) if op is not None else ret * 0
    avg_amt = ap.rolling(20).mean(); illiq = 1.0 / (avg_amt / 1e8 + eps)
    ma20 = cp.rolling(20).mean(); std20 = cp.rolling(20).std(); bw = (4 * std20) / (ma20 + eps)
    v5 = vp.rolling(5).mean(); vr = v5 / (vp.rolling(20).mean() + eps)
    def _pcorr(w):
        rm = ret.rolling(w).mean(); vrm = vr.rolling(w).mean()
        return ((ret - rm) * (vr - vrm)).rolling(w).mean() / (ret.rolling(w).std() * vr.rolling(w).std() + eps)
    pv10 = _pcorr(10); pv20 = _pcorr(20)
    pl = cp.rolling(20).mean(); pt = cp.pct_change(20); vs = v5 / (vp.rolling(20).mean() + eps)
    vc = ret.rolling(5).std(); vh = ret.rolling(60).std(); va = vc / (vh + eps)
    def _zs(df): m = df.mean(axis=1); s = df.std(axis=1); return (df.sub(m, axis=0)).div(s + eps, axis=0)
    dr = (-_zs(pl) + -_zs(pt) + -_zs(vs) + _zs(va)) / 4.0
    return {'mom_5': m5, 'gap': gap, 'illiq': illiq, 'bw': bw, 'pv10': pv10, 'pv20': pv20, 'dr': dr, 'dr_thr': dr.quantile(0.9, axis=1)}


def select_stocks(factors, date):
    if date not in factors['mom_5'].index: return []
    m5 = factors['mom_5'].loc[date].dropna(); cands = []
    for code in m5.index:
        m = m5[code]
        if m <= MOM_THRESHOLD: continue
        if date in factors['pv10'].index and code in factors['pv10'].columns:
            if not pd.isna(factors['pv10'].loc[date, code]) and factors['pv10'].loc[date, code] < -0.5: continue
        if date in factors['dr_thr'].index and code in factors['dr'].columns:
            if factors['dr'].loc[date, code] > factors['dr_thr'].loc[date]: continue
        s = m * 100
        if date in factors['pv20'].index and code in factors['pv20'].columns:
            if not pd.isna(factors['pv20'].loc[date, code]) and factors['pv20'].loc[date, code] > 0: s += 0.5
        if date in factors['gap'].index and code in factors['gap'].columns:
            if not pd.isna(factors['gap'].loc[date, code]) and factors['gap'].loc[date, code] > 0.02: s += 0.5
        if date in factors['illiq'].index and code in factors['illiq'].columns:
            if not pd.isna(factors['illiq'].loc[date, code]) and factors['illiq'].loc[date, code] > 0: s += 0.8
        if date in factors['bw'].index and code in factors['bw'].columns:
            if not pd.isna(factors['bw'].loc[date, code]) and factors['bw'].loc[date, code] > 1.2: s += 0.3
        cands.append((code, s))
    cands.sort(key=lambda x: x[1], reverse=True)
    return cands


def intraday_signal(date):
    t0 = time.time(); logger.info(f"=== v27 上午信号 {date} ===")
    codes = [c for c in get_all_codes() if not c.startswith(('688', '689', '8', '4', '2'))]
    panels = load_panel(codes)
    if not panels: logger.error("数据加载失败"); return
    cp, vp, ap, hp, lp, op = panels
    factors = calc_factors(cp, vp, ap, hp, lp, op)
    state = load_account()
    to_sell = [(c, 'timeout') for c, h in state.holdings.items() if h.get('hold_days', 0) >= HOLD_DAYS_MAX]
    sell_codes = {c for c, _ in to_sell}
    cands = select_stocks(factors, date)
    # 排除卖出后仍持有的股票（卖出的不算占用仓位）
    remaining_after_sell = {c for c in state.holdings if c not in sell_codes}
    cands = [(c, s) for c, s in cands if c not in remaining_after_sell][:MAX_HOLDINGS]
    plan = {'date': str(date), 'strategy': 'v27', 'sell_plan': [c for c, _ in to_sell],
            'buy_plan': [{'code': c, 'score': round(s, 2)} for c, s in cands[:MAX_DAILY_BUY]],
            'timestamp': datetime.now().isoformat()}
    with open(V27_PLAN_FILE, 'w') as f: json.dump(plan, f, ensure_ascii=False, indent=2)
    logger.info(f"计划: 卖 {len(plan['sell_plan'])} 只, 买 {len(plan['buy_plan'])} 只, 耗时 {time.time()-t0:.1f}s")


def intraday_execute(date):
    import requests; t0 = time.time(); logger.info(f"=== v27 下午执行 {date} ===")
    state = load_account()
    try:
        with open(V27_PLAN_FILE) as f: plan = json.load(f)
    except FileNotFoundError: logger.warning("无交易计划"); return
    codes = list(state.holdings.keys()) + [b['code'] for b in plan.get('buy_plan', [])]
    spot = {}
    for i in range(0, len(codes), 50):
        batch = codes[i:i+50]; syms = ",".join([f"sh{c}" if c.startswith("6") else f"sz{c}" for c in batch])
        try:
            resp = requests.get(f"http://qt.gtimg.cn/q={syms}", timeout=5); resp.encoding = "gbk"
            for line in resp.text.split(";"):
                if "~" not in line: continue
                p = line.split("~")
                if len(p) > 50:
                    try: spot[p[2]] = float(p[3])
                    except: pass
        except: pass

    sold = []
    for code in plan.get('sell_plan', []):
        if code in state.holdings and code in spot:
            h = state.holdings[code]
            sell(state, code, spot[code], date, 'plan')
            sold.append((code, h.get('name', code), h.get('shares', 0), spot[code]))

    bought = []
    for bp in plan.get('buy_plan', []):
        code = bp['code']
        if code in spot and code not in state.holdings and spot[code] > 0:
            avail = state.cash - state.initial_capital * 0.1
            if avail <= 0: break
            per = min(avail / MAX_DAILY_BUY, state.initial_capital * MAX_POSITION)
            adj = spot[code] * (1 + COMMISSION_RATE + SLIPPAGE_RATE)
            sh = int(per / adj / 100) * 100
            if sh > 0 and sh * adj <= state.cash:
                buy(state, code, spot[code], date, sh)
                bought.append((code, bp.get('name', code), sh, spot[code]))

    save_account(state)

    # ── 输出摘要（print 到 stdout，cron 捕获）──
    print("=" * 50)
    print(f"v27 下午执行 — {date}")
    print(f"现金: ¥{state.cash:,.0f}  持仓: {len(state.holdings)} 只")
    print("-" * 50)
    if sold:
        print(f"🔴 卖出 {len(sold)} 只:")
        for code, name, shares, price in sold:
            print(f"  {code} {name} — {shares}股 @ {price:.2f}")
    if bought:
        print(f"🟢 买入 {len(bought)} 只:")
        for code, name, shares, price in bought:
            print(f"  {code} {name} — {shares}股 @ {price:.2f}")
    if not sold and not bought:
        print("⚪ 无操作")
    print("=" * 50)

    logger.info(f"执行完成: 卖 {len(sold)} / 买 {len(bought)} / 持仓 {len(state.holdings)} 只, 耗时 {time.time()-t0:.1f}s")


def report_only(date):
    state = load_account(); nav = state.cash
    for code, h in state.holdings.items():
        kl = get_kline(code)
        if kl:
            df = pd.DataFrame(kl); df['date'] = pd.to_datetime(df['date'])
            latest = df[df['date'] <= pd.Timestamp(date)].sort_values('date').iloc[-1]
            nav += h.get('shares', 0) * latest['close']
    logger.info(f"=== v27 收盘报告 {date} === 持仓 {len(state.holdings)} 只 现金 ¥{state.cash:,.0f} 净值 ¥{nav:,.0f}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "report_only"
    date = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime("%Y-%m-%d")
    {'intraday_signal': intraday_signal, 'intraday_execute': intraday_execute, 'report_only': report_only}.get(mode, lambda d: print(f"用法: {sys.argv[0]} [intraday_signal|intraday_execute|report_only] [YYYY-MM-DD]"))(date)
