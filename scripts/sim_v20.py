#!/usr/bin/env python3
"""
v20 模拟盘交易脚本
==================
基于 v13 模拟盘框架，使用 v20_tail_pick 尾盘缩量企稳策略。
账户路径：/root/data/portfolio/
策略：尾盘缩量企稳 → 尾盘买入 → 持有1-3天 → 尾盘卖出

时间线：
  T日 14:45  tail_signal   — 尾盘选股（用接近完整的数据）
  T日 14:50  tail_buy      — 尾盘买入（信号和买入同一天，间隔5分钟）
  T+1~3日 14:50 tail_sell   — 尾盘卖出检查（止盈/止损/超时）

用法:
    python scripts/sim_v20.py tail_signal       # 14:45 尾盘选股
    python scripts/sim_v20.py tail_buy          # 14:50 尾盘买入
    python scripts/sim_v20.py tail_sell         # 14:50 尾盘卖出
    python scripts/sim_v20.py report_only       # 收盘报告
"""
import sys, os, json, time, logging
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

sys.path.insert(0, "/root/a-share-quant-sim")
sys.path.insert(0, os.path.dirname(__file__))

from core.account import PortfolioState, buy, sell, portfolio_value
from core.config import TradingCosts

# ── Config ─────────────────────────────────────────────────────────
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
PORTFOLIO_DIR = os.environ.get("PORTFOLIO_DIR", os.path.join(DATA_DIR, "portfolio"))
DAILY_DIR = os.path.join(DATA_DIR, "daily")
os.makedirs(PORTFOLIO_DIR, exist_ok=True)

V20_ACCOUNT_FILE = os.path.join(PORTFOLIO_DIR, "account_v20.json")
V20_PLAN_FILE = os.path.join(PORTFOLIO_DIR, "trade_plan_v20.json")

INITIAL_CAPITAL = 200000
STOP_LOSS = -0.05
TAKE_PROFIT = 0.05
MAX_HOLDINGS = 8
MAX_DAILY_BUY = 6
MAX_POSITION = 0.20
HOLD_DAYS_MAX = 3
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
    """加载日K数据"""
    from core.data import load_and_build_panel
    from core.config import MarketFilter

    loaded, codes = load_and_build_panel(
        "2021-01-01", "2026-05-31",
        need_open=True, need_hl=True,
        market_filter=MarketFilter(),
    )
    return {
        "close": loaded[0], "volume": loaded[1], "amount": loaded[2],
        "high": loaded[3], "low": loaded[4], "open": loaded[5],
    }


# ── Factor Calculation ─────────────────────────────────────────────
def calc_factors(data):
    """计算 v20 选股因子"""
    close = data["close"]
    volume = data["volume"]
    amount = data["amount"]
    high = data["high"]
    low = data["low"]

    vol_avg5 = volume.rolling(5).mean()
    vol_ratio = volume / vol_avg5

    daily_range = (high - low) / close
    avg_range5 = daily_range.rolling(5).mean()
    range_ratio = daily_range / avg_range5

    amount_avg20 = amount.rolling(20).mean()
    amount_ratio = amount / amount_avg20

    ma5 = close.rolling(5).mean()
    price_vs_ma5 = close / ma5

    pct_change = close.pct_change()
    limit_up = (pct_change > 0.095).astype(float)
    recent_limit_up = limit_up.rolling(20).max()

    return {
        "vol_ratio": vol_ratio, "range_ratio": range_ratio,
        "amount_ratio": amount_ratio, "price_vs_ma5": price_vs_ma5,
        "recent_limit_up": recent_limit_up,
    }


# ── Stock Selection ────────────────────────────────────────────────
def select_stocks(data, factors, date, current_holdings=None):
    """尾盘选股 — 缩量企稳"""
    close = data["close"]
    volume = data["volume"]
    amount = data["amount"]
    high = data["high"]
    low = data["low"]

    if date not in factors["vol_ratio"].index:
        return []

    # 流动性筛选
    avg_amount = amount.rolling(20).mean() / 1e4
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

    candidates = []
    for code in liquid_stocks:
        if code not in vol_ratio.index:
            continue

        vr = vol_ratio.get(code, 999)
        if vr > 0.8:  # 缩量
            continue
        rr = range_ratio.get(code, 999)
        if rr > 0.8:  # 振幅收窄
            continue
        ar = amount_ratio.get(code, 0)
        if ar < 0.5 or ar > 3.0:  # 成交额不太冷清也不太异常
            continue
        pm = price_vs_ma5.get(code, 0)
        if pm < 1.0:  # 价格 > MA5
            continue
        lu = recent_limit_up.get(code, 0)
        if lu < 1.0:  # 近期有涨停
            continue

        score = (1.0 / (vr + 0.1)) * 2.0 + (1.0 / (rr + 0.1)) * 1.0 + lu * 0.5
        candidates.append((code, score))

    if current_holdings:
        candidates = [(c, s) for c, s in candidates if c not in current_holdings]

    candidates.sort(key=lambda x: x[1], reverse=True)
    return [c for c, s in candidates[:MAX_HOLDINGS]]


# ── Portfolio State ────────────────────────────────────────────────
def load_portfolio():
    """加载账户状态"""
    if os.path.exists(V20_ACCOUNT_FILE):
        with open(V20_ACCOUNT_FILE, "r") as f:
            return json.load(f)
    return {
        "cash": INITIAL_CAPITAL,
        "holdings": {},
        "nav_history": [],
        "trade_log": [],
        "created": datetime.now().isoformat(),
    }


def save_portfolio(state):
    """保存账户状态"""
    with open(V20_ACCOUNT_FILE, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False, default=str)


# ── Trade Plan ─────────────────────────────────────────────────────
def load_plan():
    """加载交易计划"""
    if os.path.exists(V20_PLAN_FILE):
        with open(V20_PLAN_FILE, "r") as f:
            return json.load(f)
    return {"pending_buy": [], "pending_sell": [], "date": None}


def save_plan(plan):
    """保存交易计划"""
    with open(V20_PLAN_FILE, "w") as f:
        json.dump(plan, f, indent=2, ensure_ascii=False, default=str)


# ── Commands ───────────────────────────────────────────────────────
def cmd_tail_signal():
    """14:45 尾盘选股 — 生成当日买入计划"""
    log.info("=" * 60)
    log.info(f"v20 模拟盘 — 尾盘信号 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    log.info("=" * 60)

    state = load_portfolio()
    log.info(f"已加载账户: 现金 ¥{state['cash']:,.0f}, 持仓 {len(state['holdings'])} 只")

    # 卖出检查（止盈/止损/超时）
    data = load_daily_data()
    close = data["close"]
    today = close.index[-1]
    log.info(f"选股日期: {today.date()}")

    holdings = state["holdings"]
    cash = state["cash"]
    to_sell = []
    for code, h in list(holdings.items()):
        if code not in close.columns:
            continue
        current_price = close.loc[today, code]
        if pd.isna(current_price) or current_price <= 0:
            continue
        pnl_pct = (current_price - h["cost"]) / h["cost"]
        hold_days = h.get("hold_days", 0)
        if pnl_pct <= STOP_LOSS:
            to_sell.append((code, "止损", pnl_pct, current_price))
        elif pnl_pct >= TAKE_PROFIT:
            to_sell.append((code, "止盈", pnl_pct, current_price))
        elif hold_days >= HOLD_DAYS_MAX:
            to_sell.append((code, "超时", pnl_pct, current_price))

    # 选股
    factors = calc_factors(data)
    current_holdings = set(holdings.keys())
    candidates = select_stocks(data, factors, today, current_holdings)

    # 保存计划
    plan = {
        "pending_buy": candidates,
        "pending_sell": [c for c, _, _, _ in to_sell],
        "date": str(today.date()),
        "created": datetime.now().isoformat(),
    }
    save_plan(plan)

    # 输出操作建议摘要
    log.info("")
    log.info("📊 操作建议:")
    if to_sell:
        log.info(f"  🔴 卖出 {len(to_sell)} 只:")
        for code, reason, pnl, price in to_sell:
            log.info(f"    {code} — {reason} @ {price:.2f} (盈亏{pnl:+.1%})")
    if candidates:
        log.info(f"  🟢 买入 {len(candidates)} 只:")
        for c in candidates:
            vr = factors["vol_ratio"].loc[today, c] if today in factors["vol_ratio"].index and c in factors["vol_ratio"].columns else 0
            rr = factors["range_ratio"].loc[today, c] if today in factors["range_ratio"].index and c in factors["range_ratio"].columns else 0
            log.info(f"    {c} — 量比={vr:.2f} 振幅比={rr:.2f}")
    if not to_sell and not candidates:
        log.info("  ⚪ 无操作")
    log.info(f"")
    log.info(f"📊 运行完成, 信号: 卖 {len(to_sell)} 只 / 买 {len(candidates)} 只")

    return candidates


def cmd_morning_execute():
    """14:50 尾盘买入 — 执行昨日收盘后选股计划"""
    log.info("=" * 50)
    log.info("v20 尾盘买入 (14:50)")

    plan = load_plan()
    if not plan["pending_buy"]:
        log.info("无待买入计划")
        return

    data = load_daily_data()
    open_data = data["open"]
    close = data["close"]

    today = close.index[-1]
    log.info(f"执行日期: {today.date()}")
    log.info(f"计划来源: {plan['date']}")

    state = load_portfolio()
    cash = state["cash"]
    holdings = state["holdings"]

    if cash <= 0:
        log.warning("现金不足，跳过买入")
        return

    available_cash = cash * 0.9
    n_buy = min(len(plan["pending_buy"]), MAX_DAILY_BUY, MAX_HOLDINGS - len(holdings))
    if n_buy <= 0:
        log.info("持仓已满，跳过买入")
        return

    per_stock = min(available_cash / n_buy, cash * MAX_POSITION)

    bought = []
    for code in plan["pending_buy"][:n_buy]:
        if code not in open_data.columns:
            continue
        buy_price = open_data.loc[today, code]
        if pd.isna(buy_price) or buy_price <= 0:
            continue

        # 涨停检查
        prev_close = close.loc[close.index[-2], code] if len(close) > 1 else None
        if prev_close and not pd.isna(prev_close) and prev_close > 0:
            if buy_price >= prev_close * 1.10 * 0.99:
                log.info(f"  {code} 涨停，跳过")
                continue

        adj = buy_price * (1 + COMMISSION_RATE + SLIPPAGE_RATE)
        shares = int(per_stock / adj / 100) * 100
        if shares <= 0:
            continue
        cost = shares * adj
        if cost > cash:
            continue

        cash -= cost
        holdings[code] = {
            "shares": shares, "cost": float(buy_price),
            "hold_days": 0, "buy_date": str(today.date()),
        }
        bought.append(code)
        log.info(f"  买入 {code}: {shares}股 @ {buy_price:.2f}")

    state["cash"] = cash
    state["holdings"] = holdings
    save_portfolio(state)

    # 清空计划
    plan["pending_buy"] = []
    save_plan(plan)

    log.info(f"买入完成: {len(bought)} 只, 剩余现金: {cash:,.0f}")


def cmd_tail_sell():
    """14:50 尾盘卖出 — 检查止盈止损超时"""
    log.info("=" * 50)
    log.info("v20 尾盘卖出检查 (14:50)")

    data = load_daily_data()
    close = data["close"]
    today = close.index[-1]

    state = load_portfolio()
    holdings = state["holdings"]
    cash = state["cash"]

    if not holdings:
        log.info("无持仓")
        return

    to_sell = []
    for code, h in list(holdings.items()):
        if code not in close.columns:
            continue
        current_price = close.loc[today, code]
        if pd.isna(current_price) or current_price <= 0:
            continue

        pnl_pct = (current_price - h["cost"]) / h["cost"]
        h["hold_days"] += 1

        if pnl_pct <= STOP_LOSS:
            to_sell.append((code, "stop_loss", pnl_pct))
        elif pnl_pct >= TAKE_PROFIT:
            to_sell.append((code, "stop_profit", pnl_pct))
        elif h["hold_days"] >= HOLD_DAYS_MAX:
            to_sell.append((code, "timeout", pnl_pct))

    sold = []
    for code, reason, pnl_pct in to_sell:
        if code not in close.columns:
            continue
        sell_price = close.loc[today, code]
        if pd.isna(sell_price) or sell_price <= 0:
            continue

        h = holdings[code]
        sv = h["shares"] * sell_price * (1 - COMMISSION_RATE - STAMP_TAX - SLIPPAGE_RATE)
        cash += sv
        del holdings[code]
        sold.append((code, reason, pnl_pct))
        log.info(f"  卖出 {code}: {reason} pnl={pnl_pct*100:.1f}%")

    state["cash"] = cash
    state["holdings"] = holdings
    save_portfolio(state)

    log.info(f"卖出完成: {len(sold)} 只, 剩余现金: {cash:,.0f}")


def cmd_report():
    """收盘报告"""
    log.info("=" * 50)
    log.info("v20 收盘报告")

    data = load_daily_data()
    close = data["close"]
    today = close.index[-1]

    state = load_portfolio()
    holdings = state["holdings"]
    cash = state["cash"]

    # 计算持仓市值
    portfolio_val = cash
    for code, h in holdings.items():
        if code in close.columns:
            p = close.loc[today, code]
            if not pd.isna(p) and p > 0:
                portfolio_val += h["shares"] * p

    log.info(f"日期: {today.date()}")
    log.info(f"现金: {cash:,.0f}")
    log.info(f"持仓: {len(holdings)} 只")
    log.info(f"总资产: {portfolio_val:,.0f}")

    # 记录 NAV 历史
    state["nav_history"].append({
        "date": str(today.date()),
        "nav": portfolio_val,
    })
    save_portfolio(state)

    return portfolio_val


# ── Main ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python scripts/sim_v20.py [tail_signal|tail_buy|tail_sell|report_only]")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "tail_signal":
        cmd_tail_signal()
    elif cmd in ("tail_buy", "morning_execute"):
        cmd_morning_execute()
    elif cmd == "tail_sell":
        cmd_tail_sell()
    elif cmd == "report_only":
        cmd_report()
    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)
