#!/usr/bin/env python3
"""
账户2 模拟盘交易脚本 (v13)
==========================
策略：小市值中短线反转（5日反转 + 量价异动 + 振幅收窄）
账户：数据库 account_id=2

时间线：
  11:45  intraday_signal  — 上午出信号（选股+风控）
  13:00  intraday_execute — 下午执行（先卖后买）
  15:30  report_only       — 收盘报告

用法:
    python scripts/sim_account2.py intraday_signal
    python scripts/sim_account2.py intraday_execute
    python scripts/sim_account2.py report_only
"""
import sys, os, json, time, logging
from datetime import datetime
import pandas as pd
import numpy as np

sys.path.insert(0, "/root/a-share-quant-sim")
sys.path.insert(0, os.path.dirname(__file__))

from core.account import PortfolioState, buy, sell, portfolio_value
from core.config import TradingCosts
from constraints import build_trade_context
from indices import get_index_trends
from core.db import get_kline, get_all_codes

# ── Config ─────────────────────────────────────────────────────────
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
PORTFOLIO_DIR = os.environ.get("PORTFOLIO_DIR", os.path.join(DATA_DIR, "portfolio"))
os.makedirs(PORTFOLIO_DIR, exist_ok=True)

# v13 使用独立的 account 文件，避免与 v7 (v11b) 冲突
V13_ACCOUNT_FILE = os.path.join(PORTFOLIO_DIR, "account_v13.json")
V13_TRADE_COUNT_FILE = os.path.join(PORTFOLIO_DIR, "trade_count_v13.txt")
V13_PLAN_FILE = os.path.join(PORTFOLIO_DIR, "trade_plan_v13.json")

# v13 策略参数
INITIAL_CAPITAL = 200000
STOP_LOSS = -0.05
TAKE_PROFIT = 0.05
MAX_HOLDINGS = 8
MAX_DAILY_BUY = 6
MAX_POSITION = 0.20
HOLD_DAYS_MAX = 5
HOLD_DAYS_MIN = 2

# 交易成本
_costs = TradingCosts()
SLIPPAGE_RATE = _costs.slippage_rate
COMMISSION_RATE = _costs.commission_rate
STAMP_TAX = 0.001  # 印花税千一

# ── Logging ────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("sim_v13")


# ── 账户操作 ──────────────────────────────────────────────────────
# account.json 标准格式:
# {
#   "cash": 200000.0,
#   "initial_capital": 200000,
#   "holdings": {
#     "600522": {
#       "shares": 300,          # 股数
#       "cost_price": 42.25,    # 成本价
#       "entry_date": "2026-06-02",  # 买入日期 (YYYY-MM-DD)
#       "hold_days": 3,         # 已持仓天数
#     }
#   },
#   "trade_log": [...],
#   "meta": {"strategy": "v13", "created_at": "2026-06-05"}
# }

def load_account():
    """加载账户状态（从数据库，account_id=2）"""
    from core.db import load_account_for_sim
    state, loaded = load_account_for_sim(account_id=2)
    if loaded:
        return state
    # 首次运行：从 DB 读取 initial_capital
    from core.db import get_account as get_acct
    acct = get_acct(2)
    capital = acct["initial_capital"] if acct else 100000
    return PortfolioState(cash=capital, initial_capital=capital, holdings={}, trade_log=[])


def save_account(state):
    """保存账户状态（写数据库，account_id=2）"""
    from core.db import save_account_for_sim, clear_holdings, get_conn
    # 先写基本字段
    save_account_for_sim(state, account_id=2)
    # 补充 v13 特有字段（entry_date, hold_days, highest_profit）
    with get_conn() as conn:
        for code, h in state.holdings.items():
            if isinstance(h, dict):
                conn.execute(
                    "UPDATE holdings SET tp_taken=? WHERE account_id=2 AND code=?",
                    (json.dumps({
                        "entry_date": h.get("entry_date", ""),
                        "hold_days": h.get("hold_days", 0),
                        "highest_profit": h.get("highest_profit", 0.0),
                    }), code),
                )
    logger.info(f"账户已保存: 现金 ¥{state.cash:,.0f}, 持仓 {len(state.holdings)} 只")


# ── 数据加载 ──────────────────────────────────────────────────────
def load_daily_data():
    """加载日K线数据，从 DB 读取，返回 {code: df} 并打印数据最后更新时间"""
    code_dfs = {}
    latest_dates = {}
    codes = get_all_codes()
    for code in codes:
        kl = get_kline(code)
        if kl and len(kl) > 20:
            df = pd.DataFrame(kl)
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date').sort_index()
            df = df[df["volume"] > 0]
            if len(df) > 20:
                code_dfs[code] = df
                latest_dates[code] = df.index[-1]
    # 打印数据最后更新时间
    if latest_dates:
        newest = max(latest_dates.values())
        oldest = min(latest_dates.values())
        today = datetime.now().date()
        days_behind = (today - newest.date()).days
        ts = newest.strftime("%Y-%m-%d %H:%M:%S") if hasattr(newest, 'strftime') else str(newest)
        logger.info(f"📅 DB 数据: {len(code_dfs)} 只 | 最新: {ts} | 最旧: {oldest.date()} | 滞后: {days_behind}天")
    else:
        logger.warning("📅 DB 数据: 无有效数据")
    return code_dfs


def load_realtime_spot(codes):
    """拉取实时行情"""
    try:
        import requests
        results = {}
        # 分批请求，每批 50 只
        for i in range(0, len(codes), 50):
            batch = codes[i : i + 50]
            symbols = ",".join([f"sh{c}" if c.startswith("6") else f"sz{c}" for c in batch])
            url = f"http://qt.gtimg.cn/q={symbols}"
            resp = requests.get(url, timeout=5)
            resp.encoding = "gbk"
            lines = resp.text.split(";")
            for line in lines:
                if "~" not in line:
                    continue
                parts = line.split("~")
                if len(parts) > 50:
                    code = parts[2]
                    try:
                        price = float(parts[3]) if parts[3] else 0
                        open_price = float(parts[5]) if parts[5] else price
                        results[code] = {"price": price, "open": open_price}
                    except (ValueError, IndexError):
                        pass
        return results
    except Exception as e:
        logger.warning(f"实时行情拉取失败: {e}")
        return {}


def get_price_data(date, code_dfs, intraday=False, codes=None):
    """获取价格数据"""
    price_data = pd.Series(dtype=float)
    spot_data = {}

    if intraday and codes:
        # 盘中模式：拉实时快照
        spot_data = load_realtime_spot(codes)
        for code, sd in spot_data.items():
            price_data[code] = sd["open"] if sd.get("open", 0) > 0 else sd["price"]
    else:
        # 日终模式：用最近收盘价
        for code, df in code_dfs.items():
            if date in df.index:
                price_data[code] = df.loc[date, "close"]
            elif len(df) > 0:
                # 用最近一个交易日
                latest = df.index[-1]
                if latest <= date:
                    price_data[code] = df.loc[latest, "close"]

    return price_data, spot_data


# ── 选股因子 ──────────────────────────────────────────────────────
def calc_v13_factors(code_dfs, liquid_stocks):
    """计算 v13 选股因子"""
    factors = {}
    factors["rev_5"] = {}
    factors["vol_ratio"] = {}
    factors["vol_shrink"] = {}
    factors["range_ratio"] = {}

    for code in liquid_stocks:
        if code not in code_dfs:
            continue
        df = code_dfs[code]
        if len(df) < 20:
            continue

        close = df["close"]
        volume = df["volume"]
        high = df["high"]
        low = df["low"]

        # 5 日反转
        rev_5 = close.iloc[-1] / close.iloc[-6] - 1 if len(close) >= 6 else 0
        factors["rev_5"][code] = rev_5

        # 量比（当日量 / 10 日均量）
        vol_avg = volume.rolling(10).mean().iloc[-1]
        vol_ratio = volume.iloc[-1] / vol_avg if vol_avg > 0 else 1.0
        factors["vol_ratio"][code] = vol_ratio

        # 缩量企稳（当日量 / 前一日量）
        vol_shrink = volume.iloc[-1] / volume.iloc[-2] if len(volume) >= 2 and volume.iloc[-2] > 0 else 1.0
        factors["vol_shrink"][code] = vol_shrink

        # 振幅比（当日振幅 / 5 日平均振幅）
        daily_range = (high.iloc[-1] - low.iloc[-1]) / close.iloc[-1]
        avg_range = ((high - low) / close).rolling(5).mean().iloc[-1]
        range_ratio = daily_range / avg_range if avg_range > 0 else 1.0
        factors["range_ratio"][code] = range_ratio

    return factors


def select_stocks_v13(factors, holdings):
    """v13 选股 — 评分排序制"""
    rev_5 = factors["rev_5"]
    vol_ratio = factors["vol_ratio"]
    vol_shrink = factors["vol_shrink"]
    range_ratio = factors["range_ratio"]

    scores = {}
    for code in rev_5:
        r = rev_5[code]
        if r >= -0.02:  # 跌幅不够 2% 跳过
            continue
        score = abs(r) * 100  # 基础分：跌幅绝对值
        if vol_ratio.get(code, 1.0) > 1.3:
            score += 0.5
        if vol_shrink.get(code, 1.0) < 0.7:
            score += 0.3
        if range_ratio.get(code, 1.0) < 0.8:
            score += 0.2
        scores[code] = score

    # 排除当前持仓
    for code in holdings:
        scores.pop(code, None)

    # 按评分降序
    candidates = sorted(scores.keys(), key=lambda c: scores[c], reverse=True)
    return candidates[:MAX_DAILY_BUY]


# ── 风控检查 ──────────────────────────────────────────────────────
def check_risk(state, price_data):
    """风控检查：止损/止盈/超时，返回 sell_plan"""
    sell_plan = []
    to_remove = []

    for code, h in state.holdings.items():
        if code not in price_data.index:
            continue
        p = price_data[code]
        if pd.isna(p) or p <= 0:
            continue

        cost = h["cost_price"]
        pnl_pct = (p - cost) / cost

        # 止损
        if pnl_pct <= STOP_LOSS:
            sell_plan.append(
                {
                    "code": code,
                    "name": zz800_names.get(code, code),
                    "shares": "all",
                    "price": float(p),
                    "reason": "止损",
                }
            )
            to_remove.append(code)
            continue

        # 止盈
        if pnl_pct >= TAKE_PROFIT:
            sell_plan.append(
                {
                    "code": code,
                    "name": zz800_names.get(code, code),
                    "shares": "all",
                    "price": float(p),
                    "reason": "止盈",
                }
            )
            to_remove.append(code)
            continue

        # 超时
        hold_days = h.get("hold_days", 0)
        if hold_days >= HOLD_DAYS_MAX:
            sell_plan.append(
                {
                    "code": code,
                    "name": zz800_names.get(code, code),
                    "shares": "all",
                    "price": float(p),
                    "reason": "超时",
                }
            )
            to_remove.append(code)

    return sell_plan, to_remove


def check_limit_down(code, code_dfs, date, price_data):
    """检查是否跌停"""
    if code not in code_dfs:
        return False
    df = code_dfs[code]
    idx = df.index.get_loc(date) if date in df.index else -1
    if idx <= 0:
        return False
    prev_close = df.iloc[idx - 1]["close"]
    limit_down = prev_close * 0.90
    current_price = price_data.get(code, 0)
    return current_price <= limit_down * 1.01


# ── 上午信号 ──────────────────────────────────────────────────────
def run_intraday_signal():
    """上午出信号：加载数据 → 风控 → 选股 → 生成 plan"""
    logger.info("=" * 60)
    logger.info(f"v13 模拟盘 — 上午信号 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    logger.info("=" * 60)

    # 加载账户
    state = load_account()
    nav = portfolio_value(state, None, pd.Series())
    logger.info(f"已加载账户: 现金 ¥{state.cash:,.0f}, 持仓 {len(state.holdings)} 只")

    # 加载日K线数据
    code_dfs = load_daily_data()

    # 获取价格数据（盘中用实时快照）
    holdings_codes = list(state.holdings.keys())
    price_data, spot_data = get_price_data(datetime.now(), code_dfs, intraday=True, codes=holdings_codes)

    if price_data.empty and spot_data:
        for code, sd in spot_data.items():
            price_data[code] = sd["price"]

    # 风控检查
    sell_plan, to_remove = check_risk(state, price_data)
    if sell_plan:
        logger.info(f"风控触发: {len(sell_plan)} 只")
        for item in sell_plan:
            logger.info(f"  {item['code']} {item['reason']} @ {item['price']:.2f}")

    # 选股
    zz800_codes = set()
    zz800_names = {}
    zz800_path = os.path.join(DATA_DIR, "zz800_constituents.csv")
    if os.path.exists(zz800_path):
        try:
            zz800_df = pd.read_csv(zz800_path)
            zz800_codes = set(zz800_df["code"].astype(str).str.zfill(6))
            zz800_names = dict(zip(zz800_df["code"].astype(str).str.zfill(6), zz800_df["name"]))
        except Exception as e:
            logger.warning(f"中证 800 成分股加载失败: {e}，使用全市场")
    else:
        logger.warning(f"中证 800 成分股文件不存在: {zz800_path}，使用全市场")

    liquid_stocks = []
    for code, df in code_dfs.items():
        if zz800_codes and code not in zz800_codes:
            continue
        if len(df) >= 20 and "amount" in df.columns:
            avg_amount = df["amount"].rolling(20).mean().iloc[-1]
            if 3000000 < avg_amount < 100000000:
                liquid_stocks.append(code)
        elif len(df) >= 20:
            avg_vol = df["volume"].rolling(20).mean().iloc[-1]
            avg_close = df["close"].rolling(20).mean().iloc[-1]
            avg_amount = avg_vol * avg_close
            if 3000000 < avg_amount < 100000000:
                liquid_stocks.append(code)

    liquid_stocks = [c for c in liquid_stocks if not (c.startswith('688') or c.startswith('689'))]

    factors = calc_v13_factors(code_dfs, liquid_stocks)
    candidates = select_stocks_v13(factors, state.holdings)

    # 输出选股结果
    logger.info(f"流动性池: {len(liquid_stocks)} 只, 选股结果: {len(candidates)} 只")
    if candidates:
        for c in candidates:
            rev = factors["rev_5"].get(c, 0)
            vr = factors["vol_ratio"].get(c, 0)
            logger.info(f"  {c} 5日跌幅={rev:.1%} 量比={vr:.2f}")

    # 生成 buy_plan
    buy_plan = []
    hold_plan = []

    if candidates and state.cash > 0 and len(state.holdings) < MAX_HOLDINGS:
        available_cash = state.cash - state.cash * 0.1
        per_stock = min(
            available_cash / min(len(candidates), MAX_DAILY_BUY),
            state.cash * MAX_POSITION,
        )

        for code in candidates[:MAX_DAILY_BUY]:
            if code in price_data.index and price_data[code] > 0:
                buy_plan.append(
                    {
                        "code": code,
                        "name": zz800_names.get(code, code),
                        "target_amount": float(per_stock),
                        "price": float(price_data[code]),
                    }
                )

    # hold_plan
    for code, h in state.holdings.items():
        if code not in to_remove and code in price_data.index:
            p = price_data[code]
            if not pd.isna(p) and p > 0:
                mv = h["shares"] * p
                w = mv / nav if nav > 0 else 0
                hold_plan.append(
                    {
                        "code": code,
                        "name": zz800_names.get(code, code),
                        "current_shares": h["shares"],
                        "price": float(p),
                        "current_weight": w,
                        "target_weight": w,
                        "action": "hold",
                        "add_amount": 0,
                    }
                )

    # 保存 plan
    plan = {
        "generated_at": str(datetime.now()),
        "date": str(datetime.now().date()),
        "mode": "intraday_signal",
        "total_nav": float(nav),
        "sell_plan": sell_plan,
        "hold_plan": hold_plan,
        "buy_plan": buy_plan,
    }

    plan_file = os.path.join(PORTFOLIO_DIR, "trade_plan_v13.json")
    with open(plan_file, "w") as f:
        json.dump(plan, f, indent=2, default=str, ensure_ascii=False)

    # 输出操作建议摘要
    logger.info("")
    logger.info("📊 操作建议:")
    if sell_plan:
        logger.info(f"  🔴 卖出 {len(sell_plan)} 只:")
        for item in sell_plan:
            logger.info(f"    {item['code']} {item.get('name','')} — {item['reason']} @ {item['price']:.2f}")
    if buy_plan:
        logger.info(f"  🟢 买入 {len(buy_plan)} 只:")
        for item in buy_plan:
            logger.info(f"    {item['code']} {item.get('name','')} — 目标金额 ¥{item['target_amount']:,.0f} @ {item['price']:.2f}")
    if hold_plan:
        logger.info(f"  🟡 持有 {len(hold_plan)} 只:")
        for item in hold_plan:
            logger.info(f"    {item['code']} {item.get('name','')} — {item['current_shares']}股 @ {item['price']:.2f} (权重{item['current_weight']:.1%})")
    logger.info("")
    logger.info(f"📊 运行完成, 信号: 卖 {len(sell_plan)} 只 / 买 {len(buy_plan)} 只 / 持 {len(hold_plan)} 只")

    return plan


# ── 下午执行 ──────────────────────────────────────────────────────
def run_intraday_execute():
    """下午执行：加载 plan → 执行交易 → 更新账户"""
    logger.info("=" * 60)
    logger.info(f"v13 模拟盘 — 下午执行 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    logger.info("=" * 60)

    # 加载账户
    state = load_account()
    logger.info(f"现金: ¥{state.cash:,.0f}, 持仓: {len(state.holdings)} 只")

    # 加载 plan
    if not os.path.exists(V13_PLAN_FILE):
        logger.error("没有找到操作计划，请先运行上午信号")
        return None

    with open(V13_PLAN_FILE) as f:
        plan = json.load(f)

    # 日期校验
    plan_date = str(plan.get("date", "")).split(" ")[0]
    today_str = str(datetime.now().date())
    if plan_date != today_str:
        logger.warning(f"计划日期 {plan_date} 与今天 {today_str} 不符，跳过执行")
        return None

    # 加载日K线数据
    code_dfs = load_daily_data()

    # 获取价格数据（下午开盘价）
    all_codes = set()
    for item in plan.get("sell_plan", []):
        all_codes.add(item["code"])
    for item in plan.get("buy_plan", []):
        all_codes.add(item["code"])
    for item in plan.get("hold_plan", []):
        all_codes.add(item["code"])

    price_data, spot_data = get_price_data(datetime.now(), code_dfs, intraday=True, codes=list(all_codes))

    # 补充持仓价格
    for code in state.holdings:
        if code not in price_data.index and code in code_dfs:
            df = code_dfs[code]
            if len(df) > 0:
                price_data[code] = df["close"].iloc[-1]

    exec_results = []

    # 1. 卖出（风控 + 调仓）
    for item in plan.get("sell_plan", []):
        code = item["code"]
        if code not in state.holdings:
            logger.info(f"  ⏭️ {code} 不在持仓中，跳过")
            continue
        if code not in price_data.index or pd.isna(price_data[code]) or price_data[code] <= 0:
            logger.warning(f"  ⚠️ {code} 价格无效，跳过")
            continue

        # 跌停检查
        if check_limit_down(code, code_dfs, datetime.now(), price_data):
            logger.warning(f"  ⏭️ {code} 跌停，无法卖出")
            exec_results.append({"code": code, "action": "sell", "status": "blocked", "reason": "跌停"})
            continue

        sell_price = price_data[code]
        h = state.holdings[code]
        sell_value = h["shares"] * sell_price * (1 - COMMISSION_RATE - STAMP_TAX - SLIPPAGE_RATE)
        state.cash += sell_value
        state.trade_log.append(
            {
                "date": str(datetime.now()),
                "code": code,
                "action": "sell",
                "price": sell_price,
                "shares": h["shares"],
                "reason": item.get("reason", ""),
            }
        )
        del state.holdings[code]
        logger.info(f"  📉 {code} 卖出 {h['shares']}股 @ {sell_price:.2f} ({item.get('reason', '')})")
        exec_results.append({"code": code, "action": "sell", "status": "done", "shares": h["shares"], "price": float(sell_price)})

    # 2. 买入
    for item in plan.get("buy_plan", []):
        code = item["code"]
        if code in state.holdings:
            continue
        if code not in price_data.index or pd.isna(price_data[code]) or price_data[code] <= 0:
            continue

        # 涨停检查
        if code in code_dfs:
            df = code_dfs[code]
            if len(df) >= 2:
                prev_close = df["close"].iloc[-2] if len(df) >= 2 else df["close"].iloc[-1]
                limit_up = prev_close * 1.10
                if price_data[code] >= limit_up * 0.99:
                    logger.warning(f"  ⏭️ {code} 涨停，无法买入")
                    exec_results.append({"code": code, "action": "buy", "status": "blocked", "reason": "涨停"})
                    continue

        buy_price = price_data[code]
        target_mv = item.get("target_amount", 0)
        adj_p = buy_price * (1 + SLIPPAGE_RATE + COMMISSION_RATE)
        shares = int(target_mv / adj_p / 100) * 100
        if shares <= 0:
            continue

        cost = shares * adj_p
        if cost > state.cash:
            logger.info(f"  ⏭️ {code} 资金不足，跳过")
            exec_results.append({"code": code, "action": "buy", "status": "skipped", "reason": "资金不足"})
            continue

        state.cash -= cost
        state.holdings[code] = {
            "shares": shares,
            "cost_price": buy_price,
            "entry_date": str(datetime.now().date()),
            "hold_days": 0,
            "name": item.get("name", code),
        }
        state.trade_log.append(
            {
                "date": str(datetime.now()),
                "code": code,
                "action": "buy",
                "price": buy_price,
                "shares": shares,
            }
        )
        logger.info(f"  ✅ {code} 买入 {shares}股 @ {buy_price:.2f}")
        exec_results.append({"code": code, "action": "buy", "status": "done", "shares": shares, "price": float(buy_price)})

    # 更新持仓天数（用 entry_date 自动计算）
    today = datetime.now().date()
    for code, h in state.holdings.items():
        entry = h.get("entry_date", "")
        if entry:
            try:
                entry_date = datetime.strptime(str(entry)[:10], "%Y-%m-%d").date()
                h["hold_days"] = (today - entry_date).days
            except Exception:
                h["hold_days"] = h.get("hold_days", 0) + 1
        else:
            h["hold_days"] = h.get("hold_days", 0) + 1

    # 保存账户
    save_account(state)

    # 清除 plan
    try:
        os.remove(plan_file)
        logger.info("已清除已执行计划")
    except Exception:
        pass

    # 报告
    nav = portfolio_value(state, datetime.now(), price_data)
    logger.info(f"执行后: 现金 ¥{state.cash:,.0f}, 持仓 {len(state.holdings)} 只, 净值 ¥{nav:,.0f}")

    return {"nav": float(nav), "cash": float(state.cash), "holdings": len(state.holdings), "results": exec_results}


# ── 收盘报告 ──────────────────────────────────────────────────────
def run_report_only():
    """收盘报告：只读账户状态，不修改"""
    logger.info("=" * 60)
    logger.info(f"v13 模拟盘 — 收盘报告 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    logger.info("=" * 60)

    state = load_account()
    code_dfs = load_daily_data()

    # 获取最新价格
    price_data = pd.Series(dtype=float)
    for code, df in code_dfs.items():
        if code in state.holdings and len(df) > 0:
            price_data[code] = df["close"].iloc[-1]

    nav = portfolio_value(state, datetime.now(), price_data)
    total_ret = (nav - state.initial_capital) / state.initial_capital

    # 从 DB 获取股票名称
    from core.db import get_stock_name_map
    name_map = get_stock_name_map()

    logger.info(f"日期: {datetime.now().strftime('%Y-%m-%d')}")
    logger.info(f"总净值: ¥{nav:,.0f}")
    logger.info(f"总收益率: {total_ret:.1%}")
    logger.info(f"持仓: {len(state.holdings)} 只")
    logger.info(f"现金占比: {state.cash / nav:.1%}" if nav > 0 else "N/A")

    if state.holdings:
        logger.info("持仓明细:")
        for code, h in state.holdings.items():
            p = price_data.get(code, 0)
            if p > 0:
                mv = h["shares"] * p
                w = mv / nav if nav > 0 else 0
                pnl = (p - h["cost_price"]) / h["cost_price"]
                name = name_map.get(code, code)
                logger.info(f"  {code} {name:<8} {h['shares']:>6}股  市值¥{mv:>10,.0f}  权重{w:.1%}  盈亏{pnl:+.1%}")

    return {"nav": float(nav), "return": float(total_ret), "holdings": len(state.holdings)}


# ── 主入口 ────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python scripts/sim_v13.py [intraday_signal|intraday_execute|report_only]")
        sys.exit(1)

    mode = sys.argv[1]
    if mode == "intraday_signal":
        run_intraday_signal()
    elif mode == "intraday_execute":
        run_intraday_execute()
    elif mode == "report_only":
        run_report_only()
    else:
        print(f"未知模式: {mode}")
        sys.exit(1)
