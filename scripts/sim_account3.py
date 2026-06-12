#!/usr/bin/env python3
"""
账户3 模拟盘交易脚本 (v20)
==========================
策略：尾盘缩量企稳 → 尾盘买入 → 持有1-3天 → 尾盘卖出
账户：数据库 account_id=3

时间线：
  14:40  tail_signal   — 尾盘选股 + 卖出检查（纯信号，不操作账户）
  14:55  tail_execute  — 先卖后买（执行信号计划）
  15:30  report_only   — 收盘报告

用法:
    python scripts/sim_account3.py tail_signal
    python scripts/sim_account3.py tail_execute
    python scripts/sim_account3.py report_only
"""
import sys, os, json, logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.dirname(SCRIPT_DIR))

from core.config import TradingCosts

# ── Config ─────────────────────────────────────────────────────────
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
PORTFOLIO_DIR = os.environ.get("PORTFOLIO_DIR", os.path.join(DATA_DIR, "portfolio"))
os.makedirs(PORTFOLIO_DIR, exist_ok=True)

V20_PLAN_FILE = os.path.join(PORTFOLIO_DIR, "trade_plan_v20.json")

STOP_LOSS = -0.05
TAKE_PROFIT = 0.25
MAX_HOLDINGS = 8
MAX_DAILY_BUY = 6
MAX_POSITION = 0.20
HOLD_DAYS_MAX = 5
HOLD_DAYS_MIN = 1

_costs = TradingCosts()
SLIPPAGE_RATE = _costs.slippage_rate
COMMISSION_RATE = _costs.commission_rate
STAMP_TAX = _costs.stamp_tax_rate

# ── Logging ─────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(os.path.join(PORTFOLIO_DIR, "sim_v20.log")),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("sim_v20")


# ── Data Loading ────────────────────────────────────────────────────
def load_daily_data():
    """从数据库加载最近一年的日K线（对齐 v13 格式）"""
    from core.db import get_all_codes, get_kline_df

    # 获取全部股票代码（daily_kline 里的 800 只，已排除科创板/北交所）
    codes = get_all_codes()
    if not codes:
        log.warning("数据库无股票代码")
        return {}

    # 加载最近一年的数据
    start = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    df = get_kline_df(codes=codes, start_date=start)

    if df.empty:
        log.warning("数据库无数据")
        return {}

    # 构建 {code: DataFrame} 格式
    result = {}
    for code, grp in df.groupby("code"):
        grp = grp.set_index("date").sort_index()
        grp.index = pd.DatetimeIndex(grp.index)
        result[code] = grp

    log.info(f"数据加载: {len(result)} 只股票, {df.shape[0]} 条记录, 日期范围 {df['date'].min()} ~ {df['date'].max()}")
    return result


def _build_panels_from_dfs(code_dfs):
    """从 {code: DataFrame} 构建 panel 字典"""
    all_dates = set()
    for df in code_dfs.values():
        all_dates.update(df.index.tolist())
    all_dates = sorted(all_dates)

    codes = sorted(code_dfs.keys())
    panels = {}
    for field in ["close", "volume", "amount", "high", "low", "open"]:
        panel = pd.DataFrame(index=all_dates, columns=codes, dtype=float)
        for code, df in code_dfs.items():
            if field in df.columns:
                panel[code] = df[field]
        panels[field] = panel

    return panels


# ── Factor Calculation ─────────────────────────────────────────────
def calc_factors(panels):
    """计算 v20 选股因子"""
    close = panels["close"]
    volume = panels["volume"]
    amount = panels["amount"]
    high = panels["high"]
    low = panels["low"]

    vol_avg5 = volume.rolling(5, min_periods=3).mean()
    vol_ratio = volume / vol_avg5

    daily_range = (high - low) / close
    avg_range5 = daily_range.rolling(5, min_periods=3).mean()
    range_ratio = daily_range / avg_range5

    amount_avg20 = amount.rolling(20, min_periods=10).mean()
    amount_ratio = amount / amount_avg20

    ma5 = close.rolling(5, min_periods=3).mean()
    price_vs_ma5 = close / ma5

    pct_change = close.pct_change()
    limit_up = (pct_change > 0.095).astype(float)
    recent_limit_up = limit_up.rolling(20, min_periods=5).max()

    return {
        "vol_ratio": vol_ratio,
        "range_ratio": range_ratio,
        "amount_ratio": amount_ratio,
        "price_vs_ma5": price_vs_ma5,
        "recent_limit_up": recent_limit_up,
    }


# ── Stock Selection ────────────────────────────────────────────────
def select_stocks(panels, factors, date, current_holdings=None):
    """尾盘选股 — 软约束加权评分排序（v20c）"""
    close = panels["close"]
    volume = panels["volume"]
    amount = panels["amount"]

    if date not in factors["vol_ratio"].index:
        return []

    # 流动性筛选
    avg_amount = amount.rolling(20, min_periods=10).mean() / 1e4
    if date in avg_amount.index:
        day_amount = avg_amount.loc[date]
        liquid_mask = (day_amount > 300) & (day_amount < 10000)
        liquid_stocks = set(day_amount[liquid_mask].dropna().index)
    else:
        liquid_stocks = set(close.columns)

    vol_ratio = factors["vol_ratio"].loc[date].dropna()
    range_ratio = factors["range_ratio"].loc[date].dropna()
    amount_ratio = factors["amount_ratio"].loc[date].dropna()
    price_vs_ma5 = factors["price_vs_ma5"].loc[date].dropna()
    recent_limit_up = factors["recent_limit_up"].loc[date].dropna()

    # 排除科创板/北交所/老三板
    liquid_stocks = {c for c in liquid_stocks if not any(c.startswith(p) for p in ('688', '689', '8', '4', '2'))}

    if current_holdings:
        liquid_stocks = liquid_stocks - set(current_holdings)

    candidates = []
    for code in liquid_stocks:
        if code not in vol_ratio.index:
            continue

        vr = vol_ratio.get(code, 999)
        rr = range_ratio.get(code, 999)
        ar = amount_ratio.get(code, 0)
        pm = price_vs_ma5.get(code, 0)
        lu = recent_limit_up.get(code, 0)

        # 硬性排除底线
        if vr > 1.5:
            continue
        if ar < 0.15:
            continue

        score = 0.0

        # 1. 缩量加分（核心信号，权重最高）
        if vr < 1.5:
            score += max(0, 3.0 - vr * 2.0)

        # 2. 振幅收窄加分
        if rr < 1.0:
            score += (1.0 - rr) * 2.0
        elif rr < 1.2:
            score += max(0, (1.2 - rr) / 0.2 * 0.5)

        # 3. 活跃度加分（适中最好）
        if 0.15 < ar < 3.0:
            score += max(0, 1.0 - abs(ar - 1.0) * 0.8)

        # 4. 趋势加分
        if pm > 1.0:
            score += min((pm - 1.0) * 5.0, 1.0)
        elif pm > 0.98:
            score += 0.2

        # 5. 股性加分
        if lu > 0:
            score += 0.8

        if score > 0:
            candidates.append((code, score))

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [c for c, s in candidates[:MAX_HOLDINGS]]


# ── Portfolio State ────────────────────────────────────────────────
def load_portfolio():
    """加载账户状态（从数据库，account_id=3）"""
    from core.db import get_account, get_holdings
    acct = get_account(3)
    if acct:
        holdings = get_holdings(3)
        holdings_out = {}
        for code, h in holdings.items():
            holdings_out[code] = {
                "shares": h["shares"],
                "cost_price": h["cost_price"],
                "name": h.get("name", code),
                "cost": h["cost_price"],  # 兼容字段
                "hold_days": h.get("hold_days", 0),
                "buy_date": h.get("buy_date", ""),
            }
        return {
            "cash": acct["cash"],
            "initial_capital": acct["initial_capital"],
            "holdings": holdings_out,
            "nav_history": [],
            "trade_log": [],
        }
    # 首次运行：从 DB 读取 initial_capital
    from core.db import get_account as get_acct
    acct = get_acct(3)
    capital = acct["initial_capital"] if acct else 100000
    return {
        "cash": capital,
        "initial_capital": capital,
        "holdings": {},
        "nav_history": [],
        "trade_log": [],
    }


def save_portfolio(state):
    """保存账户状态（写数据库，account_id=3）"""
    from core.db import get_conn, clear_holdings, get_stock_name_map
    name_map = get_stock_name_map()
    with get_conn() as conn:
        conn.execute(
            "UPDATE account SET cash=?, updated_at=datetime('now') WHERE id=3",
            (state["cash"],),
        )
    clear_holdings(3)
    for code, h in state.get("holdings", {}).items():
        name = h.get("name", "") if isinstance(h, dict) else ""
        if not name or name == code:
            name = name_map.get(code, code)
        shares = h.get("shares", 0) if isinstance(h, dict) else 0
        cost = h.get("cost_price", 0) if isinstance(h, dict) else 0
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO holdings(account_id,code,name,shares,cost_price) VALUES(?,?,?,?,?)",
                (3, code, name, int(shares), float(cost)),
            )
    log.info(f"账户已保存: 现金 ¥{state['cash']:,.0f}, 持仓 {len(state.get('holdings', {}))} 只")


# ── Trade Plan ─────────────────────────────────────────────────────
def load_plan():
    if os.path.exists(V20_PLAN_FILE):
        with open(V20_PLAN_FILE, "r") as f:
            return json.load(f)
    return {"pending_buy": [], "pending_sell": [], "date": None}


def save_plan(plan):
    with open(V20_PLAN_FILE, "w") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False, default=str)


# ── Commands ───────────────────────────────────────────────────────
def cmd_tail_signal():
    """14:40 尾盘选股 + 卖出检查 — 生成当日操作计划（纯信号，不操作账户）"""
    log.info("=" * 60)
    log.info(f"v20 模拟盘 — 尾盘信号 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    log.info("=" * 60)

    state = load_portfolio()
    log.info(f"已加载账户: 现金 ¥{state['cash']:,.0f}, 持仓 {len(state['holdings'])} 只")

    # 加载数据
    code_dfs = load_daily_data()
    if not code_dfs:
        log.warning("无数据，跳过信号")
        return

    # 从 DB 获取股票名称映射
    from core.db import get_stock_name_map
    name_map = get_stock_name_map()

    panels = _build_panels_from_dfs(code_dfs)
    factors = calc_factors(panels)
    close = panels["close"]
    today = close.index[-1]
    log.info(f"选股日期: {today.date()}")

    # ── 卖出检查 ──
    holdings = state["holdings"]
    sell_plan = []
    to_remove = []
    for code, h in list(holdings.items()):
        if code not in close.columns:
            continue
        current_price = close.loc[today, code]
        if pd.isna(current_price) or current_price <= 0:
            continue
        pnl_pct = (current_price - h["cost_price"]) / h["cost_price"]
        hold_days = h.get("hold_days", 0)

        if pnl_pct <= STOP_LOSS:
            sell_plan.append({
                "code": code,
                "name": h.get("name") or name_map.get(code, code),
                "shares": "all",
                "price": float(current_price),
                "reason": "止损",
            })
            to_remove.append(code)
        elif pnl_pct >= TAKE_PROFIT:
            sell_plan.append({
                "code": code,
                "name": h.get("name") or name_map.get(code, code),
                "shares": "all",
                "price": float(current_price),
                "reason": "止盈",
            })
            to_remove.append(code)
        elif hold_days >= HOLD_DAYS_MAX:
            sell_plan.append({
                "code": code,
                "name": h.get("name") or name_map.get(code, code),
                "shares": "all",
                "price": float(current_price),
                "reason": "超时",
            })
            to_remove.append(code)

    # ── 选股 ──
    current_holding_codes = set(holdings.keys()) - set(to_remove)
    candidates = select_stocks(panels, factors, today, current_holding_codes)

    # ── 生成 buy_plan ──
    buy_plan = []
    hold_plan = []

    if candidates and state["cash"] > 0:
        # 按评分排序，取前 MAX_DAILY_BUY 只生成计划（不限制 available_slots，由执行时决定）
        n_plan = min(len(candidates), MAX_DAILY_BUY)
        per_stock = min(
            state["cash"] * 0.9 / n_plan,
            state["cash"] * MAX_POSITION,
        )
        for code in candidates[:n_plan]:
            if code not in close.columns:
                continue
            buy_price = close.loc[today, code]
            if pd.isna(buy_price) or buy_price <= 0:
                continue
            buy_plan.append({
                "code": code,
                "name": name_map.get(code, code),
                "target_amount": float(per_stock),
                "price": float(buy_price),
            })

    # hold_plan
    for code, h in holdings.items():
        if code in to_remove or code not in close.columns:
            continue
        p = close.loc[today, code]
        if pd.isna(p) or p <= 0:
            continue
        mv = h["shares"] * p
        hold_plan.append({
            "code": code,
            "name": h.get("name") or name_map.get(code, code),
            "current_shares": h["shares"],
            "price": float(p),
            "current_weight": 0,
            "target_weight": 0,
            "action": "hold",
            "add_amount": 0,
        })

    # ── 保存计划 ──
    # 检查今天的计划是否已存在且未被执行过（避免 tail_signal 多次运行覆盖）
    existing_plan = load_plan()
    if existing_plan.get("date") == str(today.date()) and (
        existing_plan.get("buy_plan") or existing_plan.get("sell_plan")
    ):
        log.info(f"今日计划已存在，跳过覆盖 (生成于 {existing_plan.get('generated_at', '?')})")
        # 仍然输出摘要，但不覆盖计划
        print("=" * 50)
        print(f"v20 尾盘信号 — {today.date()}")
        print(f"现金: ¥{state['cash']:,.0f}  持仓: {len(state['holdings'])} 只")
        print(f"⚠️ 今日计划已存在，未覆盖 (生成于 {existing_plan.get('generated_at', '?')})")
        if existing_plan.get("sell_plan"):
            print(f"🔴 卖出 {len(existing_plan['sell_plan'])} 只:")
            for item in existing_plan["sell_plan"]:
                print(f"  {item['code']} {item.get('name','')} — {item.get('reason','')} @ {item.get('price',0):.2f}")
        if existing_plan.get("buy_plan"):
            print(f"🟢 买入 {len(existing_plan['buy_plan'])} 只:")
            for item in existing_plan["buy_plan"]:
                print(f"  {item['code']} {item.get('name','')} — 目标 ¥{item.get('target_amount',0):,.0f} @ {item.get('price',0):.2f}")
        print("=" * 50)
        return

    plan = {
        "generated_at": str(datetime.now()),
        "date": str(today.date()),
        "mode": "tail_signal",
        "sell_plan": sell_plan,
        "hold_plan": hold_plan,
        "buy_plan": buy_plan,
    }
    save_plan(plan)

    # ── 输出操作建议摘要（print 到 stdout，cron 捕获） ──
    print("=" * 50)
    print(f"v20 尾盘信号 — {today.date()}")
    print(f"现金: ¥{state['cash']:,.0f}  持仓: {len(state['holdings'])} 只")
    print("-" * 50)
    if sell_plan:
        print(f"🔴 卖出 {len(sell_plan)} 只:")
        for item in sell_plan:
            print(f"  {item['code']} {item['name']} — {item['reason']} @ {item['price']:.2f}")
    if buy_plan:
        print(f"🟢 买入 {len(buy_plan)} 只:")
        for item in buy_plan:
            print(f"  {item['code']} {item.get('name','')} — 目标 ¥{item['target_amount']:,.0f} @ {item['price']:.2f}")
    if hold_plan:
        print(f"🟡 持有 {len(hold_plan)} 只:")
        for item in hold_plan:
            print(f"  {item['code']} {item['name']} — {item['current_shares']}股 @ {item['price']:.2f}")
    if not sell_plan and not buy_plan and not hold_plan:
        print("⚪ 无操作")
    print("=" * 50)

    log.info(f"信号完成: 卖 {len(sell_plan)} 只 / 买 {len(buy_plan)} 只 / 持有 {len(hold_plan)} 只")


def cmd_tail_execute():
    """14:45 尾盘执行 — 先卖后买"""
    log.info("=" * 50)
    log.info(f"v20 尾盘执行 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")

    plan = load_plan()
    if not plan.get("buy_plan") and not plan.get("sell_plan"):
        log.info("无待执行计划")
        print("⚪ v20 无待执行计划")
        return

    log.info(f"计划日期: {plan.get('date')}")

    state = load_portfolio()
    holdings = state["holdings"]
    cash = state["cash"]

    # ── 先卖 ──
    sold = []
    if plan.get("sell_plan"):
        from core.db import get_kline_df
        for item in plan["sell_plan"]:
            code = item["code"]
            if code not in holdings:
                continue
            h = holdings[code]
            sell_price = item.get("price", 0)
            if sell_price <= 0:
                # 用最新收盘价
                df = get_kline_df(codes=[code])
                if not df.empty:
                    sell_price = float(df.iloc[-1]["close"])
                else:
                    continue
            sv = h["shares"] * sell_price * (1 - COMMISSION_RATE - STAMP_TAX - SLIPPAGE_RATE)
            cash += sv
            del holdings[code]
            sold.append((code, item.get("name", code), item.get("reason", ""), sell_price))
            log.info(f"  卖出 {code}: {item.get('reason','')} @ {sell_price:.2f}")

    # ── 后买 ──
    bought = []
    if plan.get("buy_plan") and cash > 0:
        # 计算可用空位
        available_slots = MAX_HOLDINGS - len(holdings)
        # 按评分排序（buy_plan 已按评分排序），取可用空位数量
        max_buys = min(len(plan["buy_plan"]), MAX_DAILY_BUY, max(available_slots, 0))
        for item in plan["buy_plan"][:max_buys]:
            code = item["code"]
            target = item.get("target_amount", 0)
            buy_price = item.get("price", 0)
            if buy_price <= 0 or target <= 0:
                continue
            adj = buy_price * (1 + COMMISSION_RATE + SLIPPAGE_RATE)
            shares = int(target / adj / 100) * 100
            if shares <= 0:
                continue
            cost = shares * adj
            if cost > cash:
                shares = int(cash / adj / 100) * 100
                cost = shares * adj
            if shares <= 0 or cost > cash:
                continue
            cash -= cost
            holdings[code] = {
                "shares": shares,
                "cost_price": float(buy_price),
                "name": item.get("name", code),
                "cost": float(buy_price),
                "hold_days": 0,
                "buy_date": str(datetime.now().date()),
            }
            bought.append((code, item.get("name", code), shares, buy_price))
            log.info(f"  买入 {code}: {shares}股 @ {buy_price:.2f}")

    state["cash"] = cash
    state["holdings"] = holdings
    save_portfolio(state)

    # 清空计划（防止 tail_signal 误判"已存在"）
    plan["buy_plan"] = []
    plan["sell_plan"] = []
    plan["hold_plan"] = []
    plan["pending_buy"] = []
    plan["pending_sell"] = []
    save_plan(plan)

    # 输出摘要
    print("=" * 50)
    print(f"v20 尾盘执行完成")
    if sold:
        print(f"🔴 卖出 {len(sold)} 只:")
        for code, name, reason, price in sold:
            print(f"  {code} {name} — {reason} @ {price:.2f}")
    if bought:
        print(f"🟢 买入 {len(bought)} 只:")
        for code, name, shares, price in bought:
            print(f"  {code} {name} — {shares}股 @ {price:.2f}")
    if not sold and not bought:
        print("⚪ 无操作")
    print(f"现金: ¥{cash:,.0f}  持仓: {len(holdings)} 只")
    print("=" * 50)

    log.info(f"执行完成: 卖 {len(sold)} / 买 {len(bought)} / 现金 ¥{cash:,.0f}")


def cmd_report():
    """收盘报告"""
    log.info("=" * 50)
    log.info(f"v20 收盘报告 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")

    state = load_portfolio()
    holdings = state["holdings"]
    cash = state["cash"]

    # 计算持仓市值
    from core.db import get_kline_df
    portfolio_val = cash
    holding_details = []
    for code, h in holdings.items():
        df = get_kline_df(codes=[code])
        if not df.empty:
            p = float(df.iloc[-1]["close"])
            mv = h["shares"] * p
            portfolio_val += mv
            pnl = (p - h["cost_price"]) / h["cost_price"] if h["cost_price"] > 0 else 0
            holding_details.append((code, h.get("name", code), h["shares"], p, h["cost_price"], pnl, mv))

    initial = state["initial_capital"]
    total_pnl = (portfolio_val - initial) / initial

    print("=" * 50)
    print(f"v20 收盘报告 — {datetime.now().strftime('%Y-%m-%d')}")
    print(f"现金: ¥{cash:,.0f}")
    print(f"总资产: ¥{portfolio_val:,.0f}  收益率: {total_pnl:+.2%}")
    print("-" * 50)
    if holding_details:
        print(f"持仓 {len(holding_details)} 只:")
        for code, name, shares, price, cost, pnl, mv in holding_details:
            print(f"  {code} {name}  {shares}股  @ {price:.2f}  成本{cost:.2f}  盈亏{pnl:+.1%}  市值¥{mv:,.0f}")
    else:
        print("无持仓")
    print("=" * 50)

    log.info(f"收盘报告: 总资产 ¥{portfolio_val:,.0f}  收益率 {total_pnl:+.2%}")
    return portfolio_val


# ── Main ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python scripts/sim_v20.py [tail_signal|tail_execute|report_only]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "tail_signal":
        cmd_tail_signal()
    elif cmd in ("tail_execute", "tail_buy", "morning_execute"):
        cmd_tail_execute()
    elif cmd == "tail_sell":
        # 兼容旧命令，实际执行已经合并到 tail_execute
        cmd_tail_execute()
    elif cmd == "report_only":
        cmd_report()
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)
