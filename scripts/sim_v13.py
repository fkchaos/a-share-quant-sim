#!/usr/bin/env python3
"""
v13 模拟盘交易脚本
==================
基于 sim_daily_v7 框架，使用 v13_small_mid_short 策略。
账户路径：/root/data/portfolio_v13/
策略：小市值中短线反转（5日反转 + 量价异动 + 振幅收窄）

用法:
    python scripts/sim_v13.py intraday_signal     # 上午出信号
    python scripts/sim_v13.py intraday_execute    # 下午执行
    python scripts/sim_v13.py report_only          # 收盘报告
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

# ── Config ─────────────────────────────────────────────────────────
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
PORTFOLIO_DIR = os.path.join(DATA_DIR, "portfolio")
DAILY_DIR = os.path.join(DATA_DIR, "daily")
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
def load_account():
    """加载账户状态"""
    if os.path.exists(V13_ACCOUNT_FILE):
        with open(V13_ACCOUNT_FILE) as f:
            data = json.load(f)
        state = PortfolioState(
            cash=data.get("cash", INITIAL_CAPITAL),
            initial_capital=data.get("initial_capital", INITIAL_CAPITAL),
            holdings=data.get("holdings", {}),
            trade_log=data.get("trade_log", []),
        )
        return state
    return PortfolioState(cash=INITIAL_CAPITAL, initial_capital=INITIAL_CAPITAL, holdings={}, trade_log=[])


def save_account(state):
    """保存账户状态"""
    data = {
        "cash": state.cash,
        "initial_capital": state.initial_capital,
        "holdings": state.holdings,
        "trade_log": state.trade_log,
    }
    with open(V13_ACCOUNT_FILE, "w") as f:
        json.dump(data, f, indent=2, default=str, ensure_ascii=False)


# ── 数据加载 ──────────────────────────────────────────────────────
def load_daily_data():
    """加载日K线数据"""
    files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
    code_dfs = {}
    for f in files:
        code = f.replace(".csv", "")
        try:
            df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col="date", parse_dates=True)
            df = df[df["volume"] > 0]
            if len(df) > 20:
                code_dfs[code] = df
        except Exception:
            pass
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
    """v13 选股"""
    rev_5 = factors["rev_5"]
    vol_ratio = factors["vol_ratio"]
    vol_shrink = factors["vol_shrink"]
    range_ratio = factors["range_ratio"]

    # 条件1：5 日跌幅 > 2%（超跌）
    cond1 = {c for c, v in rev_5.items() if v < -0.02}

    # 条件2：放量（量比 > 1.3）或缩量企稳（量比 < 0.7）
    cond2_boost = {c for c, v in vol_ratio.items() if v > 1.3}
    cond2_shrink = {c for c, v in vol_shrink.items() if v < 0.7}
    cond2 = cond2_boost | cond2_shrink

    # 条件3：振幅收窄（振幅比 < 0.8）
    cond3 = {c for c, v in range_ratio.items() if v < 0.8}

    # 综合：超跌 + (放量或缩量企稳) 或 超跌 + 振幅收窄
    candidates = (cond1 & cond2) | (cond1 & cond3)

    # 排除当前持仓
    candidates -= set(holdings.keys())

    # 按反转幅度排序（跌幅最大的优先）
    candidates = sorted(candidates, key=lambda c: rev_5.get(c, 0))

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
                    "name": code,
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
                    "name": code,
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
                    "name": code,
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
    logger.info(f"现金: ¥{state.cash:,.0f}, 持仓: {len(state.holdings)} 只")

    # 加载日K线数据
    code_dfs = load_daily_data()
    logger.info(f"日K线数据: {len(code_dfs)} 只")

    # 获取价格数据（盘中用实时快照）
    holdings_codes = list(state.holdings.keys())
    price_data, spot_data = get_price_data(datetime.now(), code_dfs, intraday=True, codes=holdings_codes)

    if price_data.empty and spot_data:
        # 用实时快照补充
        for code, sd in spot_data.items():
            price_data[code] = sd["price"]

    # 风控检查
    sell_plan, to_remove = check_risk(state, price_data)
    if sell_plan:
        logger.info(f"风控触发: {len(sell_plan)} 只")
        for item in sell_plan:
            logger.info(f"  {item['code']} {item['reason']} @ {item['price']:.2f}")

    # 选股
    # 1. 加载中证 800 成分股
    zz800_codes = set()
    zz800_path = os.path.join(DATA_DIR, "zz800_constituents.csv")
    if os.path.exists(zz800_path):
        try:
            zz800_df = pd.read_csv(zz800_path)
            zz800_codes = set(zz800_df["code"].astype(str).str.zfill(6))
            logger.info(f"中证 800 成分股: {len(zz800_codes)} 只")
        except Exception as e:
            logger.warning(f"中证 800 成分股加载失败: {e}，使用全市场")
    else:
        logger.warning(f"中证 800 成分股文件不存在: {zz800_path}，使用全市场")

    # 2. 流动性筛选：用 20 日平均成交额（从中证 800 中筛选）
    liquid_stocks = []
    for code, df in code_dfs.items():
        # 限定在中证 800 成分股
        if zz800_codes and code not in zz800_codes:
            continue
        if len(df) >= 20 and "amount" in df.columns:
            avg_amount = df["amount"].rolling(20).mean().iloc[-1]
            if 3000000 < avg_amount < 100000000:  # 300万-1亿
                liquid_stocks.append(code)
        elif len(df) >= 20:
            # 没有 amount 字段，用 volume * close 近似
            avg_vol = df["volume"].rolling(20).mean().iloc[-1]
            avg_close = df["close"].rolling(20).mean().iloc[-1]
            avg_amount = avg_vol * avg_close
            if 3000000 < avg_amount < 100000000:
                liquid_stocks.append(code)

    factors = calc_v13_factors(code_dfs, liquid_stocks)
    candidates = select_stocks_v13(factors, state.holdings)

    logger.info(f"流动性池: {len(liquid_stocks)} 只, 选股结果: {len(candidates)} 只")
    if candidates:
        for c in candidates:
            rev = factors["rev_5"].get(c, 0)
            logger.info(f"  {c} 5日跌幅={rev:.1%}")

    # 生成 buy_plan
    buy_plan = []
    hold_plan = []

    if candidates and state.cash > INITIAL_CAPITAL * 0.1 and len(state.holdings) < MAX_HOLDINGS:
        available_cash = state.cash - INITIAL_CAPITAL * 0.1
        per_stock = min(
            available_cash / min(len(candidates), MAX_DAILY_BUY),
            INITIAL_CAPITAL * MAX_POSITION,
        )

        for code in candidates[:MAX_DAILY_BUY]:
            if code in price_data.index and price_data[code] > 0:
                buy_plan.append(
                    {
                        "code": code,
                        "name": code,
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
                        "name": code,
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

    logger.info(f"✅ 计划已保存: 卖{len(sell_plan)} 买{len(buy_plan)} 持{len(hold_plan)}")
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
            "entry_date": str(datetime.now()),
            "hold_days": 0,
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

    # 更新持仓天数
    for code in state.holdings:
        if "hold_days" in state.holdings[code]:
            state.holdings[code]["hold_days"] = state.holdings[code].get("hold_days", 0) + 1
        else:
            state.holdings[code]["hold_days"] = 1

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
    total_ret = (nav - INITIAL_CAPITAL) / INITIAL_CAPITAL

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
                logger.info(f"  {code} {h['shares']}股 市值¥{mv:,.0f} 权重{w:.1%} 盈亏{pnl:.1%}")

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
