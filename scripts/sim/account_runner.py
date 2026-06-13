#!/usr/bin/env python3
"""
scripts/sim/account_runner.py — 统一模拟盘入口
================================================
通过 --strategy 参数切换策略，无需为每个策略单独维护脚本。

用法:
    python scripts/sim/account_runner.py --strategy v27 intraday_signal
    python scripts/sim/account_runner.py --strategy v27 intraday_execute
    python scripts/sim/account_runner.py --strategy v27 report_only

    python scripts/sim/account_runner.py --strategy v11b intraday_signal
    python scripts/sim/account_runner.py --strategy v20c tail_signal

设计:
    - 账户操作（load/save/风控/执行）统一在此脚本
    - 选股逻辑通过 strategy_map 动态加载
    - 新增策略只需在 strategy_map.py 注册，不需要新建脚本
"""
import sys, os, json, time, logging, argparse
from datetime import datetime
import pandas as pd
import numpy as np

# 确保项目根目录在 path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "scripts"))

from core.account import PortfolioState, buy, sell, portfolio_value
from core.config import TradingCosts
from core.db import get_kline, get_all_codes
from core.strategy_map import load_strategy

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
PORTFOLIO_DIR = os.environ.get("PORTFOLIO_DIR", os.path.join(DATA_DIR, "portfolio"))
os.makedirs(PORTFOLIO_DIR, exist_ok=True)

_costs = TradingCosts()
SLIPPAGE_RATE = _costs.slippage_rate
COMMISSION_RATE = _costs.commission_rate
STAMP_TAX = 0.001

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("account_runner")


# ── 数据加载 ──────────────────────────────────────────────────────
def load_panel(codes, min_days=60):
    """从 DB 加载 K 线面板"""
    code_dfs = {}
    for code in codes:
        kl = get_kline(code)
        if kl and len(kl) > min_days:
            df = pd.DataFrame(kl)
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date').sort_index()
            df = df[df["volume"] > 0]
            if len(df) > min_days:
                code_dfs[code] = df
    if not code_dfs:
        return None
    return (
        pd.DataFrame({c: code_dfs[c]['close'] for c in code_dfs}),
        pd.DataFrame({c: code_dfs[c]['volume'] for c in code_dfs}),
        pd.DataFrame({c: code_dfs[c].get('amount', code_dfs[c]['close'] * code_dfs[c]['volume']) for c in code_dfs}),
        pd.DataFrame({c: code_dfs[c].get('high', code_dfs[c]['close']) for c in code_dfs}),
        pd.DataFrame({c: code_dfs[c].get('low', code_dfs[c]['close']) for c in code_dfs}),
        pd.DataFrame({c: code_dfs[c].get('open', code_dfs[c]['close']) for c in code_dfs}),
    )


# ── 账户操作 ──────────────────────────────────────────────────────
def load_account(account_id):
    """从 DB 加载账户状态，返回 PortfolioState"""
    from core.db import get_account, get_holdings, upsert_account
    acct = get_account(account_id)
    if not acct:
        # 首次运行：在 DB 创建账户记录
        init_cap = {1: 200000, 2: 100000, 3: 100000}.get(account_id, 100000)
        upsert_account(account_id=account_id, cash=init_cap, initial_capital=init_cap)
        return PortfolioState(cash=init_cap, initial_capital=init_cap, holdings={}, trade_log=[])

    holdings_raw = get_holdings(account_id)
    holdings = {}
    for code, h in holdings_raw.items():
        added = h.get("added_at", "")
        hd = 0
        if added:
            try:
                from datetime import datetime as dt
                buy_dt = dt.strptime(added[:10], "%Y-%m-%d")
                hd = max(0, (dt.now() - buy_dt).days)
            except Exception:
                pass
        holdings[code] = {
            "code": code,
            "name": h.get("name", ""),
            "shares": h.get("shares", 0),
            "cost_price": h.get("cost_price", 0),
            "hold_days": hd,
            "added_at": added,
        }

    state = PortfolioState(
        cash=acct["cash"],
        initial_capital=acct["initial_capital"],
        holdings=holdings,
        trade_log=[],
    )
    logger.info(f"加载账户{account_id}: 现金 ¥{state.cash:,.0f}, 持仓 {len(state.holdings)} 只")
    return state


def save_account(state, account_id):
    """保存账户状态到 DB"""
    from core.db import upsert_account, upsert_holding, delete_holding, get_holdings
    # 保存现金
    upsert_account(account_id=account_id, cash=state.cash, initial_capital=state.initial_capital)
    # 同步持仓
    db_holdings = set(get_holdings(account_id).keys())
    new_holdings = set(state.holdings.keys())
    # 删除已清仓
    for code in db_holdings - new_holdings:
        delete_holding(account_id, code)
    # 更新/新增持仓
    for code, h in state.holdings.items():
        upsert_holding(
            account_id, code,
            name=h.get("name", ""),
            shares=h.get("shares", 0),
            cost_price=h.get("cost_price", 0),
        )
    logger.info(f"账户{account_id}已保存: 现金 ¥{state.cash:,.0f}, 持仓 {len(state.holdings)} 只")


# ── 风控 ──────────────────────────────────────────────────────────
def check_risk(state, date, price_data, params):
    """风控检查：止损/止盈/超时"""
    to_sell = []
    for code, h in list(state.holdings.items()):
        if code not in price_data.index:
            continue
        cp = price_data[code]
        if pd.isna(cp) or cp <= 0:
            continue
        pnl = (cp - h['cost_price']) / h['cost_price']
        if pnl <= params.get("STOP_LOSS", -0.05):
            to_sell.append((code, 'stop_loss', pnl))
        elif pnl >= params.get("TAKE_PROFIT", 0.10):
            to_sell.append((code, 'take_profit', pnl))
        elif h.get('hold_days', 0) >= params.get("HOLD_DAYS_MAX", 8):
            to_sell.append((code, 'timeout', pnl))
    return to_sell


def execute_sells(state, to_sell, date, spot):
    """执行卖出，返回新 state"""
    for code, reason, pnl in to_sell:
        if code in spot and spot[code] > 0:
            state = sell(state, code, spot[code], date, reason)
    return state


def execute_buys(state, cands, date, spot, params):
    """执行买入，返回新 state"""
    max_buy = params.get("MAX_DAILY_BUY", 6)
    max_pos = params.get("MAX_POSITION", 0.25)
    max_hold = params.get("MAX_HOLDINGS", 8)

    available = state.cash - state.initial_capital * 0.05
    if available <= 0:
        return state

    nb = min(len(cands), max_buy, max_hold - len(state.holdings))
    per_stock = min(available / nb, state.initial_capital * max_pos) if nb > 0 else 0

    bought = 0
    for code, score in cands[:max_buy]:
        if len(state.holdings) >= max_hold or bought >= nb:
            break
        if code not in spot or spot[code] <= 0:
            continue
        price = spot[code]
        adj = price * (1 + COMMISSION_RATE + SLIPPAGE_RATE)
        shares = int(per_stock / adj / 100) * 100
        if shares <= 0 or shares * adj > state.cash:
            continue
        state = buy(state, code, price, date, shares)
        bought += 1
    return state


# ── 主流程 ──────────────────────────────────────────────────────
def run_signal(strategy_name, date):
    """信号生成：选股 + 风控"""
    t0 = time.time()
    strategy = load_strategy(strategy_name)
    params = strategy.get("params", {})
    account_id = strategy["account_id"]
    timing = strategy.get("timing", "intraday")

    logger.info(f"=== {strategy_name} 信号 {date} ===")

    # 加载数据
    codes = [c for c in get_all_codes() if not c.startswith(('688', '689', '8', '4', '2'))]
    panels = load_panel(codes)
    if not panels:
        logger.error("数据加载失败")
        return
    cp, vp, ap, hp, lp, op = panels

    # 风控
    state = load_account(account_id)
    price_data = cp.loc[date] if date in cp.index else pd.Series()
    to_sell = check_risk(state, date, price_data, params)

    # 选股（不同策略函数签名不同，分别适配）
    if strategy_name == "v20c":
        # v20c: calc_tail_pick_factors(cp, vp, ap, hp, lp) 无 params
        #        select_stocks_tail_pick(factors, date, cp, vp, ap, hp, lp, holdings)
        factors = strategy["calc_factors"](cp, vp, ap, hp, lp)
        raw_cands = strategy["select_stocks"](factors, date, cp, vp, ap, hp, lp, state.holdings)
        cands = [(c, 0.0) for c in raw_cands]  # v20c 返回 code list，补 score=0
    else:
        # v27: calc_factors(cp, vp, ap, hp, lp, op, params)
        #       select_stocks(factors, date, holdings, params) → [(code, score)]
        factors = strategy["calc_factors"](cp, vp, ap, hp, lp, op, params)
        cands = strategy["select_stocks"](factors, date, state.holdings, params)

    cands = cands[:params.get("MAX_HOLDINGS", 8)]

    # 生成计划
    plan = {
        'date': str(date),
        'strategy': strategy_name,
        'sell_plan': [c for c, _, _ in to_sell],
        'buy_plan': [{'code': c, 'score': round(s, 2)} for c, s in cands[:params.get("MAX_DAILY_BUY", 6)]],
        'timestamp': datetime.now().isoformat(),
    }
    plan_file = os.path.join(PORTFOLIO_DIR, f"trade_plan_{strategy_name}.json")
    with open(plan_file, 'w') as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    logger.info(f"计划: 卖 {len(plan['sell_plan'])} 只, 买 {len(plan['buy_plan'])} 只, 耗时 {time.time()-t0:.1f}s")


def run_execute(strategy_name, date):
    """执行交易：先卖后买"""
    import requests
    t0 = time.time()
    strategy = load_strategy(strategy_name)
    params = strategy.get("params", {})
    account_id = strategy["account_id"]

    logger.info(f"=== {strategy_name} 执行 {date} ===")

    state = load_account(account_id)

    # 加载计划
    plan_file = os.path.join(PORTFOLIO_DIR, f"trade_plan_{strategy_name}.json")
    try:
        with open(plan_file) as f:
            plan = json.load(f)
    except FileNotFoundError:
        logger.warning("无交易计划")
        return

    # 拉取实时价格
    codes = list(state.holdings.keys()) + [b['code'] for b in plan.get('buy_plan', [])]
    spot = {}
    for i in range(0, len(codes), 50):
        batch = codes[i:i+50]
        syms = ",".join([f"sh{c}" if c.startswith("6") else f"sz{c}" for c in batch])
        try:
            resp = requests.get(f"http://qt.gtimg.cn/q={syms}", timeout=5)
            resp.encoding = "gbk"
            for line in resp.text.split(";"):
                if "~" not in line: continue
                p = line.split("~")
                if len(p) > 50:
                    try: spot[p[2]] = float(p[3])
                    except: pass
        except: pass

    # 先卖
    for code in plan.get('sell_plan', []):
        if code in state.holdings and code in spot:
            state = sell(state, code, spot[code], date, 'plan')

    # 后买
    cands = [(b['code'], b.get('score', 0)) for b in plan.get('buy_plan', [])]
    state = execute_buys(state, cands, date, spot, params)

    save_account(state, account_id)
    logger.info(f"执行完成: 持仓 {len(state.holdings)} 只, 耗时 {time.time()-t0:.1f}s")


def run_report(strategy_name, date):
    """收盘报告"""
    strategy = load_strategy(strategy_name)
    account_id = strategy["account_id"]
    state = load_account(account_id)

    nav = state.cash
    for code, h in state.holdings.items():
        kl = get_kline(code)
        if kl:
            df = pd.DataFrame(kl)
            df['date'] = pd.to_datetime(df['date'])
            latest = df[df['date'] <= pd.Timestamp(date)].sort_values('date').iloc[-1]
            nav += h.get('shares', 0) * latest['close']

    logger.info(f"=== {strategy_name} 收盘报告 {date} === 持仓 {len(state.holdings)} 只 现金 ¥{state.cash:,.0f} 净值 ¥{nav:,.0f}")


# ── 入口 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="统一模拟盘入口")
    parser.add_argument("--strategy", required=True, choices=["v11b", "v27", "v20c"], help="策略名称")
    parser.add_argument("mode", choices=["intraday_signal", "intraday_execute", "tail_signal", "tail_execute", "report_only"], help="运行模式")
    parser.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="交易日期")
    args = parser.parse_args()

    if args.mode in ("intraday_signal", "tail_signal"):
        run_signal(args.strategy, args.date)
    elif args.mode in ("intraday_execute", "tail_execute"):
        run_execute(args.strategy, args.date)
    elif args.mode == "report_only":
        run_report(args.strategy, args.date)
