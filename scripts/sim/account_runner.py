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
    from core.db import get_account, get_holdings, upsert_account, get_stock_name_map
    acct = get_account(account_id)
    if not acct:
        # 首次运行：在 DB 创建账户记录
        init_cap = {1: 200000, 2: 100000, 3: 100000}.get(account_id, 100000)
        upsert_account(account_id=account_id, cash=init_cap, initial_capital=init_cap)
        return PortfolioState(cash=init_cap, initial_capital=init_cap, holdings={}, trade_log=[])

    holdings_raw = get_holdings(account_id)
    name_map = get_stock_name_map()
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
            "name": h.get("name") or name_map.get(code, code),
            "shares": h.get("shares", 0),
            "cost_price": h.get("cost_price", 0),
            "hold_days": hd,
            "added_at": added,
            "entry_date": added[:10] if added else str(datetime.now().date()),
            "tp_taken": json.loads(h.get("tp_taken", "[]")) if isinstance(h.get("tp_taken"), str) else [],
            "highest_profit": 0.0,
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
    from core.db import upsert_account, upsert_holding, delete_holding, get_holdings, get_conn, get_stock_name_map
    # 保存现金
    upsert_account(account_id=account_id, cash=state.cash, initial_capital=state.initial_capital)
    # 同步持仓
    db_holdings = set(get_holdings(account_id).keys())
    new_holdings = set(state.holdings.keys())
    # 删除已清仓
    for code in db_holdings - new_holdings:
        delete_holding(account_id, code)
    # 从 stock_pool 获取名称
    name_map = get_stock_name_map()
    # 更新/新增持仓
    for code, h in state.holdings.items():
        name = h.get("name", "") or name_map.get(code, code)
        upsert_holding(
            account_id, code,
            name=name,
            shares=h.get("shares", 0),
            cost_price=h.get("cost_price", 0),
        )
    # 写入 trade_log
    if state.trade_log:
        with get_conn() as conn:
            for t in state.trade_log:
                code = t.get("code", "")
                name = t.get("name", "") or name_map.get(code, code)
                action = t.get("action", "")
                shares = t.get("shares", 0)
                price = t.get("price", 0)
                amount = t.get("amount", 0)
                reason = t.get("reason", "")
                trade_date = t.get("date", "")
                conn.execute(
                    "INSERT INTO trade_log(account_id,code,name,action,shares,price,amount,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (account_id, code, name, action, shares, price, amount, reason, trade_date),
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
    max_pos = params.get("MAX_POSITION", 0.30)
    max_hold = params.get("MAX_HOLDINGS", 8)

    available = state.cash - state.initial_capital * 0.03
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
        # v27/v11b: calc_factors(cp, vp, ap, hp, lp, op, params)
        #            select_stocks(factors, date, holdings, params) → [(code, score)]
        factors = strategy["calc_factors"](cp, vp, ap, hp, lp, op, params)
        # 确保 params 里有 initial_capital（v11b select_stocks 需要）
        _params = dict(params)
        _params.setdefault("initial_capital", state.initial_capital)
        cands = strategy["select_stocks"](factors, date, state.holdings, _params)

    # 限制选股数量：卖出后持仓 + 新股 <= MAX_HOLDINGS
    sell_codes_set = set(sell_codes)
    remaining_after_sell = {c for c in state.holdings if c not in sell_codes_set}
    max_new = max(0, params.get("MAX_HOLDINGS", 8) - len(remaining_after_sell))
    cands = cands[:max_new]

    # 估算每只买入预算（用于资金容量过滤和计算股数）
    max_buy = params.get("MAX_DAILY_BUY", 8)
    sell_codes = [c for c, _, _ in to_sell]
    sell_cash = 0
    if date in cp.index:
        sell_cash = sum(
            price_data.get(c, 0) * state.holdings[c].get('shares', state.holdings[c].get('qty', 0))
            for c in sell_codes if c in state.holdings and c in price_data.index
        )
    available = state.cash + sell_cash
    per_stock_filter = available / max_buy if max_buy > 0 else available  # 资金容量过滤用

    # 资金容量过滤：买不起（1手都买不起）的票排除
    if date in cp.index:
        filtered = []
        for code, score in cands:
            if code in cp.columns:
                price = cp.loc[date, code]
                if pd.isna(price) or price <= 0:
                    continue
                min_cost = price * 100  # 至少1手
                if min_cost > per_stock_filter:
                    logger.info(f"资金过滤排除 {code}: 1手需{min_cost:.0f} > 预算{per_stock_filter:.0f}")
                    continue
            filtered.append((code, score))
        cands = filtered

    # 生成计划：等权分配仓位
    remaining_after_sell = len(state.holdings) - len(to_sell)
    max_new_buys = min(params.get("MAX_DAILY_BUY", 6), params.get("MAX_HOLDINGS", 8) - remaining_after_sell)
    max_new_buys = max(max_new_buys, 0)
    buy_list = cands[:max_new_buys]
    n = len(buy_list)
    per_stock = available / n if n > 0 else available  # 每份仓位金额
    # 查股票名称（先取 holdings 已有的，再从 DB 补齐新选股的）
    name_map = {}
    for c in state.holdings:
        nm = state.holdings[c].get('name', '')
        if nm and nm != c:
            name_map[c] = nm
    try:
        from core.db import get_stock_name_map
        db_names = get_stock_name_map()
        for c in sell_codes:
            if c not in name_map:
                name_map[c] = db_names.get(c, c)
        for c, _ in buy_list:
            if c not in name_map:
                name_map[c] = db_names.get(c, c)
    except Exception:
        pass

    buy_plan = []
    for code, score in buy_list:
        qty = 0
        price = 0
        if date in cp.index and code in cp.columns:
            price = cp.loc[date, code]
            if not pd.isna(price) and price > 0 and per_stock > 0:
                qty = int(per_stock / price / 100) * 100
                if qty == 0:
                    qty = 100  # 至少1手
        buy_plan.append({
            'code': code,
            'name': name_map.get(code, ''),
            'score': round(score, 2),
            'price': round(price, 2),
            'qty': qty,
            'target_amount': round(per_stock, 2),
            'position_ratio': round(1.0 / n, 4) if n > 0 else 0,
        })
    plan = {
        'date': str(date),
        'strategy': strategy_name,
        'sell_plan': [
            {
                'code': c,
                'name': name_map.get(c, ''),
                'qty': state.holdings[c].get('shares', state.holdings[c].get('qty', 0)),
                'reason': reason,
                'pnl': round(pnl, 4),
            }
            for c, reason, pnl in to_sell if c in state.holdings
        ],
        'buy_plan': buy_plan,
        'timestamp': datetime.now().isoformat(),
    }
    plan_file = os.path.join(PORTFOLIO_DIR, f"trade_plan_{strategy_name}.json")
    with open(plan_file, 'w') as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    logger.info(f"计划: 卖 {len(plan['sell_plan'])} 只, 买 {len(plan['buy_plan'])} 只, 耗时 {time.time()-t0:.1f}s")

    # ── 输出信号摘要（print 到 stdout，cron 捕获）──
    print("=" * 60)
    print(f"{strategy_name} 信号 — {date}")
    print(f"现金: ¥{state.cash:,.0f}  持仓: {len(state.holdings)} 只")
    print("-" * 60)
    if plan.get('sell_plan'):
        print(f"🔴 卖出 {len(plan['sell_plan'])} 只:")
        for item in plan['sell_plan']:
            print(f"  {item['code']} {item.get('name', '')} — {item.get('shares', 'all')}股 @ {item.get('price', 0):.2f} ({item.get('reason', '')})")
    if plan.get('buy_plan'):
        print(f"🟢 买入 {len(plan['buy_plan'])} 只:")
        for item in plan['buy_plan']:
            est_shares = int(item.get('target_amount', 0) / item.get('reference_price', item.get('price', 1)) / 100) * 100 if item.get('reference_price', item.get('price', 0)) > 0 else 0
            print(f"  {item['code']} {item.get('name', '')} — ≈{est_shares}股 @ {item.get('reference_price', item.get('price', 0)):.2f} (目标¥{item.get('target_amount', 0):,.0f})")
    if plan.get('hold_plan'):
        add_items = [h for h in plan['hold_plan'] if h.get('action') == 'add']
        hold_items = [h for h in plan['hold_plan'] if h.get('action') != 'add']
        if add_items:
            print(f"🟡 补仓 {len(add_items)} 只:")
            for item in add_items:
                add_shares = int(item.get('add_amount', 0) / item.get('price', 1) / 100) * 100
                print(f"  {item['code']} {item.get('name', '')} — {add_shares}股 @ {item.get('price', 0):.2f}")
        if hold_items:
            print(f"➡️ 持有 {len(hold_items)} 只:")
            for item in hold_items:
                print(f"  {item['code']} {item.get('name', '')} — {item.get('current_shares', item.get('qty', '?'))}股 @ {item.get('price', 0):.2f}")
    if not plan.get('sell_plan') and not plan.get('buy_plan'):
        print("⚪ 无操作")
    print("=" * 60)


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
    sold = []
    for item in plan.get('sell_plan', []):
        code = item['code']
        if code in state.holdings and code in spot:
            h = state.holdings[code]
            state = sell(state, code, spot[code], date, 'plan')
            sold.append((code, h.get('name', code), h.get('shares', 0), spot[code]))

    # 后买
    bought = []
    buy_plan_map = {b['code']: b for b in plan.get('buy_plan', [])}
    cands = [(b['code'], b.get('score', 0)) for b in plan.get('buy_plan', [])]
    for code, score in cands:
        if code in spot and code not in state.holdings and spot[code] > 0:
            price = spot[code]
            adj = price * (1 + COMMISSION_RATE + SLIPPAGE_RATE)
            max_pos = params.get("MAX_POSITION", 0.30)
            max_hold = params.get("MAX_HOLDINGS", 12)
            max_buy = params.get("MAX_DAILY_BUY", 5)
            available = state.cash - state.initial_capital * 0.03
            if available <= 0:
                break
            nb = min(max_buy, max_hold - len(state.holdings))
            if nb <= 0:
                break
            per_stock = min(available / nb, state.initial_capital * max_pos)
            shares = int(per_stock / adj / 100) * 100
            if shares <= 0 or shares * adj > state.cash:
                continue
            state = buy(state, code, price, date, shares)
            bname = buy_plan_map.get(code, {}).get('name', code)
            bought.append((code, bname, price, shares))

    save_account(state, account_id)

    # ── 输出摘要（print 到 stdout，cron 捕获）──
    print("=" * 60)
    print(f"{strategy_name} 执行 — {date}")
    print(f"现金: ¥{state.cash:,.0f}  持仓: {len(state.holdings)} 只")
    print("-" * 60)
    if sold:
        print(f"🔴 卖出 {len(sold)} 只:")
        for code, name, shares, price in sold:
            print(f"  {code} {name} — {shares}股 @ {price:.2f}")
    if bought:
        print(f"🟢 买入 {len(bought)} 只:")
        for code, bname, price, shares in bought:
            print(f"  {code} {bname} — {shares}股 @ {price:.2f}")
    if not sold and not bought:
        print("⚪ 无操作")
    print("=" * 60)

    logger.info(f"执行完成: 卖 {len(sold)} / 买 {len(bought)} / 持仓 {len(state.holdings)} 只, 耗时 {time.time()-t0:.1f}s")


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
