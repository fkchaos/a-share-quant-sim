#!/usr/bin/env python3
"""
scripts/sim/account_runner.py — 统一模拟盘入口
===================================================

账户-策略分离架构：
  - 账户在 DB 中绑定策略（account.strategy 字段）
  - 运行时通过 --account-id 指定账户，自动读取绑定的策略
  - 支持子命令管理账户：create / switch / list

用法:
  # 信号生成（自动读取账户绑定的策略）
  python scripts/sim/account_runner.py --account-id 1 intraday_signal
  python scripts/sim/account_runner.py --account-id 2 intraday_signal

  # 执行交易
  python scripts/sim/account_runner.py --account-id 2 intraday_execute

  # 收盘报告
  python scripts/sim/account_runner.py --account-id 2 report_only

  # 账户管理子命令
  python scripts/sim/account_runner.py create --account-id 4 --name "我的账户" --cash 500000
  python scripts/sim/account_runner.py create --account-id 4 --name "我的账户" --cash 500000 --force
  python scripts/sim/account_runner.py switch --account-id 4 --strategy v27
  python scripts/sim/account_runner.py list

设计:
  - 账户操作（load/save/风控/执行）统一在此脚本
  - 选股逻辑通过 strategy_map 动态加载
  - 新增策略只需在 strategy_map.py 注册，不需要新建脚本
  - 账户和策略解耦：一个账户可以随时切换策略
"""
import sys, os, json, time, logging, argparse
from datetime import datetime
import pandas as pd
import numpy as np

# 确保项目根目录在 path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from core.account import PortfolioState, buy, sell, portfolio_value
from core.config import TradingCosts
from core.db import get_kline, get_all_codes, get_tradeable_codes, get_account, list_accounts, create_account, switch_strategy, upsert_account
from core.strategy_map import load_strategy, list_strategy_names
from scripts.backtest.strategy_adapter import get_adapter

DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
PORTFOLIO_DIR = os.environ.get("PORTFOLIO_DIR", os.path.join(DATA_DIR, "portfolio"))
os.makedirs(PORTFOLIO_DIR, exist_ok=True)

_costs = TradingCosts()
SLIPPAGE_RATE = _costs.slippage_rate
COMMISSION_RATE = _costs.commission_rate
STAMP_TAX = 0.001

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("account_runner")


# ── 策略名白名单（活跃策略，不含已退役的）────────────────────────
ACTIVE_STRATEGIES = ["v11b", "v27", "v28"]


def _resolve_strategy(account_id):
    """从账户表读取绑定的策略，返回策略名"""
    acct = get_account(account_id)
    if not acct:
        raise ValueError(f"账户 {account_id} 不存在，请先创建账户")
    strategy = acct.get("strategy", "")
    if not strategy:
        raise ValueError(f"账户 {account_id} 未绑定策略，请先执行: account_runner.py switch --account-id {account_id} --strategy <name>")
    return strategy


# ── 数据加载 ──────────────────────────────────────────────────────
def load_panel(codes, min_days=60, pool="zz800"):
    """从 DB 加载 K 线面板（使用 load_panel_from_db 加速）

    替代逐只 get_kline + 逐只 DataFrame 构建的旧方案。
    旧方案：4918 只 × 逐个 get_kline → 内存峰值 500MB+
    新方案：单次 SQL 查询 + pivot_table → 约 30MB

    Args:
        codes: 兼容旧接口，仅用于判定 pool（不再使用全部 codes）
        min_days: 兼容旧接口
        pool: 股票池 (zz800 / full_a)
    """
    from core.db import load_panel_from_db, get_latest_date
    from datetime import datetime, timedelta

    latest = get_latest_date()
    if latest is None:
        return None

    # 取最近 2 年数据，足够所有因子计算（最长 lookback 约 60 天）
    start = (datetime.strptime(latest, '%Y-%m-%d') - timedelta(days=730)).strftime('%Y-%m-%d')

    try:
        panels, _ = load_panel_from_db(
            start_date=start, end_date=latest,
            need_open=True, need_hl=True,
            pool=pool
        )
    except Exception as e:
        print(f"[load_panel] 加载失败: {e}")
        return None

    if panels[0].empty:
        print(f"[load_panel] 面板为空")
        return None

    # load_panel_from_db 返回 (close, vol, amt, open, high, low)
    # 老接口需要 (close, vol, amt, high, low, open) — 重排
    cp, vp, ap, op, hp, lp = panels
    return (cp, vp, ap, hp, lp, op)


# ── 账户操作 ──────────────────────────────────────────────────────
def load_account(account_id, stale_days=30):
    """从 DB 加载账户状态，返回 PortfolioState

    参数:
        account_id: 账户ID
        stale_days: 持仓股票超过多少天无K线数据则视为退市/停牌，自动清理
    """
    from core.db import get_holdings, upsert_account, get_stock_name_map, get_kline_latest
    acct = get_account(account_id)
    if not acct:
        raise ValueError(f"账户 {account_id} 不存在")

    holdings_raw = get_holdings(account_id)
    name_map = get_stock_name_map()
    holdings = {}
    stale_codes = []  # 记录退市/停牌持仓
    for code, h in holdings_raw.items():
        # 检查持仓股票最新交易日
        latest_kl = get_kline_latest(code)
        if latest_kl is None:
            # 完全没有K线数据，跳过
            stale_codes.append((code, "无K线数据"))
            continue
        last_date = latest_kl.get("date", "")
        if last_date:
            try:
                from datetime import datetime as dt
                gap = (dt.now() - dt.strptime(last_date, "%Y-%m-%d")).days
                if gap > stale_days:
                    stale_codes.append((code, f"最后交易日 {last_date}，已 {gap} 天无数据"))
                    continue
            except Exception:
                pass

        added = h.get("added_at", "")
        hd = 0
        if added:
            try:
                from datetime import datetime as dt
                from core.db import get_index_kline
                buy_date = added[:10]
                # 用中证800指数K线计算交易日天数
                idx_kl = get_index_kline("sh000001")
                if idx_kl:
                    dates = sorted([r["date"] for r in idx_kl if r["volume"] > 0])
                    if buy_date in dates and str(dt.now().date()) in dates:
                        buy_idx = dates.index(buy_date)
                        today_idx = dates.index(str(dt.now().date()))
                        hd = max(0, today_idx - buy_idx)
                    else:
                        # 回退到日历天数
                        buy_dt = dt.strptime(buy_date, "%Y-%m-%d")
                        hd = max(0, (dt.now() - buy_dt).days)
                else:
                    buy_dt = dt.strptime(buy_date, "%Y-%m-%d")
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

    # 清理退市/停牌持仓
    if stale_codes:
        from core.db import delete_holding
        for code, reason in stale_codes:
            delete_holding(account_id, code)
            logger.warning(f"持仓清理: 账户{account_id} {code} 已移除（{reason}）")
        print(f"⚠️ 持仓清理: 账户{account_id} 移除 {len(stale_codes)} 只退市/停牌股票")
        for code, reason in stale_codes:
            print(f"  {code}: {reason}")

    state = PortfolioState(
        cash=acct["cash"],
        initial_capital=acct["initial_capital"],
        holdings=holdings,
        trade_log=[],
    )
    logger.info(f"加载账户{account_id}: 现金 ¥{state.cash:,.0f}, 持仓 {len(state.holdings)} 只, 策略={acct.get('strategy','')}")
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
        with get_conn("trade_log") as conn:
            for t in state.trade_log:
                code = t.get("code", "")
                name = t.get("name", "") or name_map.get(code, code)
                action = t.get("action", "")
                shares = t.get("shares", 0)
                price = t.get("price", 0)
                amount = t.get("cost", t.get("amount", 0))
                reason = t.get("reason", "")
                trade_date = t.get("date", "")
                if not amount:
                    amount = shares * price
                conn.execute(
                    "INSERT INTO trade_log(account_id,code,name,action,shares,price,amount,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (account_id, code, name, action, shares, price, amount, reason, trade_date),
                )
    logger.info(f"账户{account_id}已保存: 现金 ¥{state.cash:,.0f}, 持仓 {len(state.holdings)} 只")


def execute_sells(state, to_sell, date, spot):
    """执行卖出，返回新 state"""
    for code, reason, pnl in to_sell:
        if code in spot and spot[code] > 0:
            state = sell(state, code, spot[code], date, reason)
    return state


def execute_buys(state, cands, date, spot, params):
    """执行买入，返回新 state"""
    max_buy = params["MAX_DAILY_BUY"]
    max_pos = params["MAX_POSITION"]
    max_hold = params["MAX_HOLDINGS"]

    position_scale = params.get("POSITION_SCALE", 1.0)
    available = state.cash * position_scale - state.initial_capital * 0.03
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


# ── 交易日判断 ────────────────────────────────────────────────────
def is_trade_day(date_str):
    """
    判断是否为交易日：
    1. 周一到周五（weekday 0-4）
    2. DB 中有该日期的 K 线数据（排除节假日）

    注意：如果当天数据未入库，说明数据更新有问题，跳过是正确的。

    参数:
        date_str: 日期字符串，格式 'YYYY-MM-DD'

    返回:
        bool: 是否为交易日
    """
    from datetime import datetime
    from core.db import get_latest_date, get_kline_df

    # 检查星期（周一=0 ... 周五=4）
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        return False
    if dt.weekday() > 4:
        return False

    # 检查 DB 最新数据日期
    latest = get_latest_date()
    if latest is None:
        return False

    # 当天数据必须已入库
    if latest < date_str:
        return False

    # 检查该日期是否有 K 线数据（至少 100 条记录，排除数据缺失）
    df = get_kline_df(start_date=date_str)
    if df is None or len(df) < 100:
        return False

    return True


# ── 主流程 ──────────────────────────────────────────────────────
def run_signal(account_id, date, strategy_name=None):
    """信号生成：选股 + 风控，输出结构化 JSON"""
    import traceback, json
    t0 = time.time()

    try:
        # 交易日检查
        if not is_trade_day(date):
            result = {"type": "signal", "account_id": account_id, "date": str(date), "is_trading_day": False, "status": "skip", "reason": "非交易日"}
            print(json.dumps(result, ensure_ascii=False))
            return result

        plan = _run_signal_impl(account_id, date, strategy_name)
        if plan is None:
            result = {"type": "signal", "account_id": account_id, "date": str(date), "is_trading_day": True, "status": "empty", "reason": "无交易计划"}
            print(json.dumps(result, ensure_ascii=False))
            return result

        # 构建结构化输出
        state = load_account(account_id)
        result = {
            "type": "signal",
            "account_id": account_id,
            "date": str(date),
            "is_trading_day": True,
            "status": "ok",
            "strategy": plan.get("strategy", ""),
            "regime": plan.get("regime", ""),
            "regime_multiplier": plan.get("regime_multiplier", 1.0),
            "cash": state.cash,
            "holdings_count": len(state.holdings),
            "sells": [
                {"code": s["code"], "name": s.get("name", ""), "shares": s.get("qty", 0), "reason": s.get("reason", ""), "pnl_pct": round(s.get("pnl", 0) * 100, 2)}
                for s in plan.get("sell_plan", [])
            ],
            "buys": [
                {"code": b["code"], "name": b.get("name", ""), "shares": b.get("qty", 0), "price": b.get("price", 0), "target_amount": b.get("target_amount", 0)}
                for b in plan.get("buy_plan", [])
            ],
            "holds": [
                {"code": h["code"], "name": h.get("name", ""), "shares": h.get("current_shares", 0), "price": h.get("price", 0), "cost_price": h.get("cost_price", 0)}
                for h in plan.get("hold_plan", [])
            ],
            "duration": round(time.time() - t0, 1),
        }
        print(json.dumps(result, ensure_ascii=False))
        return result

    except Exception as e:
        duration = int(time.time() - t0)
        tb = traceback.format_exc()
        logger.error(f"run_signal 异常: {e}\n{tb}")
        result = {"type": "signal", "account_id": account_id, "date": str(date), "status": "error", "error": str(e), "duration": duration}
        print(json.dumps(result, ensure_ascii=False))
        return result


def _run_signal_impl(account_id, date, strategy_name=None):
    """信号生成实现（被 run_signal 包裹）"""
    t0 = time.time()

    # 如果没指定策略名，从账户表读取
    if strategy_name is None:
        strategy_name = _resolve_strategy(account_id)

    strategy = load_strategy(strategy_name)
    params = dict(strategy.get("params", {}))
    timing = strategy.get("timing", "intraday")

    # 账户级配置覆盖策略参数（如 POSITION_SCALE）
    _acct_cfg = get_account(account_id)
    if _acct_cfg:
        for k, v in _acct_cfg.get("params", {}).items():
            params[k] = v

    # 为需要 float_shares 的策略加载流通股本数据
    if "float_shares_map" not in params:
        try:
            from core.db import get_float_shares_map, get_float_shares_map_full
            # v43 使用全A股票池，其他策略使用 zz800
            if strategy_name == "v43":
                params["float_shares_map"] = get_float_shares_map_full()
            else:
                params["float_shares_map"] = get_float_shares_map()
        except Exception:
            params["float_shares_map"] = {}

    # v43 额外注入全A股票池数据（用于选股过滤）
    if strategy_name == "v43":
        try:
            from core.db import get_stock_pool_full
            params["stock_pool_full"] = get_stock_pool_full()
        except Exception:
            params["stock_pool_full"] = []

    logger.info(f"=== 账户{account_id} / {strategy_name} 信号 {date} === (POSITION_SCALE={params.get('POSITION_SCALE', 1.0)})")

    # 从策略配置读取股票池
    pool = strategy.get("pool", "zz800")
    panels = load_panel(None, pool=pool)
    if not panels:
        logger.error("数据加载失败")
        return
    cp, vp, ap, hp, lp, op = panels

    # 风控
    state = load_account(account_id)
    price_data = cp.loc[date] if date in cp.index else pd.Series()
    # 取前一日收盘价用于涨停判断
    prev_close = None
    if date in cp.index:
        idx_pos = cp.index.get_loc(date)
        if isinstance(idx_pos, (int, np.integer)) and idx_pos > 0:
            prev_close = cp.iloc[idx_pos - 1]

    # 风控（用 strategy_adapter 统一接口）
    adapter = get_adapter()
    to_sell = adapter.risk_check(strategy_name, state, date, price_data,
                                  params, prev_close=prev_close)

    # 计算因子面板（动态加载策略的 calc_factors）
    calc_factors_fn = strategy.get("calc_factors_fn")
    factor_sell = []
    factor_defer = []
    sell_penalty_tracker = state._sell_penalty_tracker if hasattr(state, '_sell_penalty_tracker') else {}
    factor_exit_fn = strategy.get("factor_exit_fn")

    if calc_factors_fn:
        from core.strategy_map import _load_func
        calc_factors_func = _load_func(calc_factors_fn)
        factors = calc_factors_func(cp, vp, ap, hp, lp, op, params)
        # 如果策略有 factor_exit_fn，用计算好的 factors 执行
        if factor_exit_fn and date in cp.index:
            factor_sell, factor_defer, sell_penalty_tracker = factor_exit_fn(
                factors, date, state.holdings, params, sell_penalty_tracker
            )
    else:
        # 兼容旧策略（如 v40 factor_exit）
        from scripts.strategies.v40_factor_exit import calc_factors as v40_calc_factors
        factors = v40_calc_factors(cp, vp, ap, hp, lp, op, params)
        if factor_exit_fn and date in cp.index:
            factor_sell, factor_defer, sell_penalty_tracker = factor_exit_fn(
                factors, date, state.holdings, params, sell_penalty_tracker
            )
        if factor_sell or factor_defer:
            logger.info(f"因子恶化: 确认卖出{len(factor_sell)}只, 延迟卖出{len(factor_defer)}只")
        # 延迟卖出的股票：从 sell_codes 中移除（不卖）
        factor_defer_codes = set(c for c, _ in factor_defer)
        to_sell = [(c, r, p) for c, r, p in to_sell if c not in factor_defer_codes]
        # 因子恶化确认卖出：追加到 to_sell
        for code, score in factor_sell:
            if code not in [c for c, _, _ in to_sell]:
                to_sell.append((code, 'factor_decay', 0.0))
        # 更新 state tracker
        state._sell_penalty_tracker = sell_penalty_tracker

    # 选股（用 strategy_adapter 统一接口）
    cands = adapter.select(strategy_name, None, date,
                           cp, vp, ap, hp, lp, op,
                           current_holdings=state.holdings,
                           params=params)

    # 市场状态识别 → 仓位乘数（用 strategy_adapter 统一接口）
    regime_label, regime_mult = adapter.calc_regime(strategy_name, cp, date, params)
    logger.info(f"市场状态: {regime_label}, 仓位乘数: {regime_mult}")

    sell_codes = [c for c, _, _ in to_sell]

    # 限制选股数量：卖出后持仓 + 新股 <= MAX_HOLDINGS
    sell_codes_set = set(sell_codes)
    remaining_after_sell = {c for c in state.holdings if c not in sell_codes_set}
    max_new = max(0, params["MAX_HOLDINGS"] - len(remaining_after_sell))
    cands = cands[:max_new]

    # 估算每只买入预算（用于资金容量过滤和计算股数）
    max_buy = params["MAX_DAILY_BUY"]
    sell_cash = 0
    if date in cp.index:
        sell_cash = sum(
            price_data.get(c, 0) * state.holdings[c].get('shares', state.holdings[c].get('qty', 0))
            for c in sell_codes if c in state.holdings and c in price_data.index
        )
    available = state.cash + sell_cash
    # 静态仓位控制：POSITION_SCALE  default 1.0，可调
    # 保留现金 = initial_capital * (1 - POSITION_SCALE)，确保不满仓
    position_scale = params.get("POSITION_SCALE", 1.0)
    available = available * regime_mult * position_scale
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

    # 生成计划：等权分配仓位，单只不超过 MAX_POSITION 上限
    remaining_after_sell = len(state.holdings) - len(to_sell)
    max_new_buys = min(params.get("MAX_DAILY_BUY", 6), params.get("MAX_HOLDINGS", 8) - remaining_after_sell)
    max_new_buys = max(max_new_buys, 0)
    buy_list = cands[:max_new_buys]
    n = len(buy_list)
    # 当前总资产 = 现金 + 持仓市值（用前一日收盘价估算）
    total_value = state.cash
    if date in cp.index:
        for code, h in state.holdings.items():
            shares = h.get('shares', h.get('qty', 0))
            price = price_data.get(code, 0)
            total_value += price * shares
    max_pos = params.get("MAX_POSITION", 0.125)
    per_stock = min(available / n, total_value * max_pos) if n > 0 else 0  # 等权分配，受 MAX_POSITION 约束
    # 查股票名称（先取 holdings 已有的，再从 DB 补齐新选股的）
    name_map = {}
    for c in state.holdings:
        nm = state.holdings[c].get('name', '')
        if nm and nm != c:
            name_map[c] = nm
    try:
        from core.db import get_stock_name_map
        db_names = get_stock_name_map(pool=pool)
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

    # 生成持有计划：当前持仓中不在 sell_codes 且不在 buy_list 的
    buy_codes_set = {c for c, _ in buy_list}
    hold_plan = []
    for code, h in state.holdings.items():
        if code not in sell_codes_set and code not in buy_codes_set:
            price = 0
            if date in cp.index and code in cp.columns:
                price = cp.loc[date, code]
                if pd.isna(price) or price <= 0:
                    price = h.get('cost_price', 0)
            hold_plan.append({
                'code': code,
                'name': name_map.get(code, code),
                'current_shares': h.get('shares', h.get('qty', 0)),
                'price': round(price, 2),
                'cost_price': round(h.get('cost_price', 0), 2),
                'action': 'hold',
            })

    plan = {
        'date': str(date),
        'account_id': account_id,
        'strategy': strategy_name,
        'regime': regime_label,
        'regime_multiplier': regime_mult,
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
        'hold_plan': hold_plan,
        'timestamp': datetime.now().isoformat(),
    }
    plan_file = os.path.join(PORTFOLIO_DIR, f"trade_plan_{account_id}.json")
    with open(plan_file, 'w') as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)

    logger.info(f"计划: 卖 {len(plan['sell_plan'])} 只, 买 {len(plan['buy_plan'])} 只, 耗时 {time.time()-t0:.1f}s")

    return plan

    # ── 输出信号摘要（print 到 stdout，cron 捕获）──
    print("=" * 60)
    print(f"账户{account_id} / {strategy_name} 信号 — {date}")
    print(f"市场状态: {regime_label} (仓位乘数 {regime_mult})")
    print(f"现金: ¥{state.cash:,.0f}  持仓: {len(state.holdings)} 只")
    print("-" * 60)
    if plan.get('sell_plan'):
        print(f"🔴 卖出 {len(plan['sell_plan'])} 只:")
        for item in plan['sell_plan']:
            print(f"  {item['code']} {item.get('name', '')} — {item.get('qty', 0)}股 ({item.get('reason', '')} {item.get('pnl', 0)*100:+.1f}%)")
    if plan.get('buy_plan'):
        print(f"🟢 买入 {len(plan['buy_plan'])} 只:")
        for item in plan['buy_plan']:
            est_shares = item.get('qty', 0)
            print(f"  {item['code']} {item.get('name', '')} — {est_shares}股 @ {item.get('price', 0):.2f} (目标¥{item.get('target_amount', 0):,.0f})")
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
                shares = item.get('current_shares', item.get('qty', '?'))
                price = item.get('price', 0)
                cost = item.get('cost_price', 0)
                if cost > 0 and price > 0:
                    pnl_pct = (price - cost) / cost * 100
                    print(f"  {item['code']} {item.get('name', '')} — {shares}股 @ {price:.2f} (成本{cost:.2f}, {pnl_pct:+.1f}%)")
                else:
                    print(f"  {item['code']} {item.get('name', '')} — {shares}股 @ {price:.2f}")
    if not plan.get('sell_plan') and not plan.get('buy_plan') and not plan.get('hold_plan'):
        print("⚪ 无操作")
    print("=" * 60)


def run_execute(account_id, date, strategy_name=None):
    """执行交易：先卖后买，输出结构化 JSON"""
    import requests, traceback, json
    t0 = time.time()

    try:
        # 交易日检查
        if not is_trade_day(date):
            result = {"type": "execute", "account_id": account_id, "date": str(date), "is_trading_day": False, "status": "skip", "reason": "非交易日"}
            print(json.dumps(result, ensure_ascii=False))
            return result

        details = _run_execute_impl(account_id, date, strategy_name)

        state = load_account(account_id)
        # 构建持仓明细
        holdings_detail = []
        for code, h in state.holdings.items():
            cost_price = h.get("cost_price", 0)
            shares = h.get("shares", 0)
            # 获取当前价（用收盘价，简化处理）
            current_price = h.get("current_price", cost_price)
            mv = shares * current_price
            pnl_i_pct = (current_price / cost_price - 1) * 100 if cost_price > 0 else 0
            holdings_detail.append({
                "code": code,
                "name": h.get("name", ""),
                "shares": shares,
                "cost_price": round(cost_price, 2),
                "current_price": round(current_price, 2),
                "market_value": round(mv, 2),
                "pnl_pct": round(pnl_i_pct, 2),
            })

        result = {
            "type": "execute",
            "account_id": account_id,
            "date": str(date),
            "is_trading_day": True,
            "status": "ok",
            "cash": round(state.cash, 2),
            "holdings_count": len(state.holdings),
            "executed": len([d for d in details if d.get("action") in ("BUY", "SELL")]),
            "skipped": len([d for d in details if d.get("action") == "SKIP"]),
            "details": details,
            "holdings": holdings_detail,
            "duration": round(time.time() - t0, 1),
        }
        print(json.dumps(result, ensure_ascii=False))
        return result

    except Exception as e:
        duration = int(time.time() - t0)
        tb = traceback.format_exc()
        logger.error(f"run_execute 异常: {e}\n{tb}")
        result = {"type": "execute", "account_id": account_id, "date": str(date), "status": "error", "error": str(e), "duration": duration}
        print(json.dumps(result, ensure_ascii=False))
        return result


def _run_execute_impl(account_id, date, strategy_name=None):
    """交易执行实现，返回交易详情列表"""
    import requests
    t0 = time.time()
    details = []

    if strategy_name is None:
        strategy_name = _resolve_strategy(account_id)

    strategy = load_strategy(strategy_name)
    params = strategy.get("params", {})

    state = load_account(account_id)

    # 加载计划
    plan_file = os.path.join(PORTFOLIO_DIR, f"trade_plan_{account_id}.json")
    try:
        with open(plan_file) as f:
            plan = json.load(f)
    except FileNotFoundError:
        logger.warning("无交易计划")
        return details

    # 拉取实时价格（含前收盘价，用于涨停判断）
    codes = list(state.holdings.keys()) + [b['code'] for b in plan.get('buy_plan', [])]
    spot = {}        # code -> 当前价
    spot_prev = {}   # code -> 前收盘价
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
                    try:
                        code = p[2]
                        spot[code] = float(p[3])          # 当前价
                        spot_prev[code] = float(p[4])      # 前收盘价
                    except: pass
        except: pass

    regime_mult = plan.get('regime_multiplier', 1.0)

    # 先卖
    for item in plan.get('sell_plan', []):
        code = item['code']
        if code in state.holdings and code in spot:
            h = state.holdings[code]
            state = sell(state, code, spot[code], date, 'plan')
            details.append({
                "action": "SELL",
                "code": code,
                "name": h.get('name', code),
                "shares": h.get('shares', 0),
                "price": round(spot[code], 2),
                "reason": item.get("reason", ""),
            })
        else:
            details.append({"action": "SKIP", "code": code, "reason": "价格缺失或持仓不存在"})

    # 后买
    buy_plan_map = {b['code']: b for b in plan.get('buy_plan', [])}
    cands = [(b['code'], b.get('score', 0)) for b in plan.get('buy_plan', [])]
    for code, score in cands:
        if code in spot and code not in state.holdings and spot[code] > 0:
            price = spot[code]

            # 涨停检测：当前价 >= 前收盘价 * 1.095（主板10%涨停，留一点余量）
            is_limit_up = False
            if code in spot_prev and spot_prev[code] > 0:
                limit_up_price = spot_prev[code] * 1.095
                if price >= limit_up_price:
                    is_limit_up = True

            adj = price * (1 + COMMISSION_RATE + SLIPPAGE_RATE)
            max_pos = params.get("MAX_POSITION", 0.30)
            max_hold = params.get("MAX_HOLDINGS", 12)
            max_buy = params.get("MAX_DAILY_BUY", 5)
            available = state.cash - state.initial_capital * 0.03
            available = available * regime_mult
            if available <= 0:
                break
            nb = min(max_buy, max_hold - len(state.holdings))
            if nb <= 0:
                break
            per_stock = min(available / nb, state.initial_capital * max_pos)
            shares = int(per_stock / adj / 100) * 100
            if shares <= 0 or shares * adj > state.cash:
                details.append({"action": "SKIP", "code": code, "reason": "资金不足"})
                continue
            state = buy(state, code, price, date, shares)
            bname = buy_plan_map.get(code, {}).get('name', code)
            buy_note = "涨停排队" if is_limit_up else ""
            details.append({
                "action": "BUY",
                "code": code,
                "name": bname,
                "shares": shares,
                "price": round(price, 2),
                "note": buy_note,
            })
        else:
            details.append({"action": "SKIP", "code": code, "reason": "价格缺失或已持仓"})

    save_account(state, account_id)

    logger.info(f"执行完成: 卖 {len([d for d in details if d['action']=='SELL'])} / 买 {len([d for d in details if d['action']=='BUY'])} / 持仓 {len(state.holdings)} 只, 耗时 {time.time()-t0:.1f}s")

    return details


def run_report(account_id, date, strategy_name=None):
    """收盘报告，输出结构化 JSON（不需要策略信息，仅展示账户状态）"""
    import json
    if strategy_name is None:
        acct = get_account(account_id)
        strategy_name = (acct.get("strategy", "") if acct else "") or "unknown"

    state = load_account(account_id)

    nav = state.cash
    holdings_detail = []
    for code, h in state.holdings.items():
        kl = get_kline(code)
        mv = 0
        if kl:
            df = pd.DataFrame(kl)
            df['date'] = pd.to_datetime(df['date'])
            latest = df[df['date'] <= pd.Timestamp(date)].sort_values('date').iloc[-1]
            mv = h.get('shares', 0) * latest['close']
            nav += h.get('shares', 0) * latest['close']
        cost = h.get('cost_price', 0)
        pnl_i = mv - cost * h.get('shares', 0)
        pnl_i_pct = pnl_i / (cost * h.get('shares', 0)) * 100 if cost * h.get('shares', 0) > 0 else 0
        holdings_detail.append({
            "code": code,
            "name": h.get('name', ''),
            "shares": h.get('shares', 0),
            "cost_price": cost,
            "market_value": round(mv, 2),
            "pnl_pct": round(pnl_i_pct, 2),
        })

    total_mv = nav - state.cash
    pnl = nav - state.initial_capital
    pnl_pct = pnl / state.initial_capital * 100 if state.initial_capital > 0 else 0
    _acct_cfg = get_account(account_id)
    _ps = (_acct_cfg or {}).get("params", {}).get("POSITION_SCALE", 1.0)

    result = {
        "type": "report",
        "account_id": account_id,
        "date": str(date),
        "is_trading_day": is_trade_day(date),
        "strategy": strategy_name,
        "cash": round(state.cash, 2),
        "nav": round(nav, 2),
        "total_market_value": round(total_mv, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "holdings_count": len(state.holdings),
        "position_scale": _ps,
        "holdings": holdings_detail,
    }
    print(json.dumps(result, ensure_ascii=False))
    return result


# ── 账户管理子命令 ─────────────────────────────────────────────────
def cmd_list_accounts():
    """列出所有账户"""
    accounts = list_accounts()
    if not accounts:
        print("暂无账户，使用 create 子命令创建")
        return

    print("=" * 70)
    print(f"{'ID':>4}  {'名称':<12}  {'策略':<10}  {'现金':>12}  {'初始资金':>12}  {'更新时间'}")
    print("-" * 70)
    for acct in accounts:
        print(f"{acct['id']:>4}  {acct.get('name',''):<12}  {acct.get('strategy',''):<10}  ¥{acct['cash']:>10,.0f}  ¥{acct['initial_capital']:>10,.0f}  {acct.get('updated_at','')}")
    print("=" * 70)
    print(f"可用策略: {', '.join(list_strategy_names())}")
    print(f"活跃策略: {', '.join(ACTIVE_STRATEGIES)}")
    print()
    print("账户级配置:")
    for acct in accounts:
        cfg = acct.get("params", {})
        ps = cfg.get("POSITION_SCALE", 1.0)
        print(f"  账户{acct['id']}: POSITION_SCALE={ps:.2f}")


def cmd_create_account(account_id, name, cash, strategy="", force=False, position_scale=1.0):
    """创建新账户"""
    if strategy and strategy not in list_strategy_names():
        print(f"❌ 未知策略: {strategy}")
        print(f"可用策略: {', '.join(list_strategy_names())}")
        return

    from core.db import get_conn, clear_holdings

    # 强制覆盖：先清空持仓和交易记录，再删除重建
    if force:
        # 清空该账户的持仓和交易记录
        clear_holdings(account_id)
        with get_conn("trade_log") as conn:
            conn.execute("DELETE FROM trade_log WHERE account_id=?", (account_id,))
        # 删除账户本身（如果存在）
        with get_conn("account") as conn:
            conn.execute("DELETE FROM account WHERE id=?", (account_id,))
        print(f"  ⚠️ 已清空账户 {account_id} 的持仓和交易记录")

    ok = create_account(account_id, name=name, cash=cash, initial_capital=cash, strategy=strategy)
    if ok:
        # 保存账户级配置（POSITION_SCALE 等）
        if position_scale != 1.0:
            upsert_account(account_id, params={"POSITION_SCALE": position_scale})
        print(f"✅ 账户 {account_id} 创建成功: 名称={name}, 资金=¥{cash:,}, 策略={strategy or '未绑定'}, POSITION_SCALE={position_scale:.2f}")
    else:
        print(f"⚠️ 账户 {account_id} 已存在，跳过创建（使用 --force 强制覆盖）")


def cmd_switch_strategy(account_id, strategy):
    """切换账户策略"""
    if strategy not in list_strategy_names():
        print(f"❌ 未知策略: {strategy}")
        print(f"可用策略: {', '.join(list_strategy_names())}")
        return

    ok = switch_strategy(account_id, strategy)
    if ok:
        print(f"✅ 账户 {account_id} 策略已切换为: {strategy}")
    else:
        print(f"⚠️ 账户 {account_id} 不存在")


# ── 入口 ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="统一模拟盘入口（账户-策略分离）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 创建账户并绑定策略
  python scripts/sim/account_runner.py create --account-id 4 --name "我的账户" --cash 500000 --strategy v27

  # 切换策略
  python scripts/sim/account_runner.py switch --account-id 4 --strategy v11b

  # 查看所有账户
  python scripts/sim/account_runner.py list

  # 信号生成（自动读取账户绑定的策略）
  python scripts/sim/account_runner.py run --account-id 2 intraday_signal

  # 执行交易
  python scripts/sim/account_runner.py run --account-id 2 intraday_execute

  # 收盘报告
  python scripts/sim/account_runner.py run --account-id 2 report_only

  # 临时指定策略（覆盖账户绑定）
  python scripts/sim/account_runner.py run --account-id 2 --strategy v27 intraday_signal
        """
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="子命令")

    # list 子命令
    subparsers.add_parser("list", help="列出所有账户")

    # create 子命令
    p_create = subparsers.add_parser("create", help="创建新账户")
    p_create.add_argument("--account-id", type=int, required=True, help="账户ID")
    p_create.add_argument("--name", type=str, default="", help="账户名称")
    p_create.add_argument("--cash", type=float, default=100000, help="初始资金（默认: 100000）")
    p_create.add_argument("--strategy", type=str, default="", help="绑定策略（可选）")
    p_create.add_argument("--force", action="store_true", help="强制覆盖已有账户（清空持仓和交易记录）")

    # switch 子命令
    p_switch = subparsers.add_parser("switch", help="切换账户策略")
    p_switch.add_argument("--account-id", type=int, required=True, help="账户ID")
    p_switch.add_argument("--strategy", type=str, required=True, help="目标策略名")

    # run 子命令（运行模式）
    p_run = subparsers.add_parser("run", help="运行信号/执行/报告")
    p_run.add_argument("mode", choices=["intraday_signal", "intraday_execute", "tail_signal", "tail_execute", "report_only"], help="运行模式")
    p_run.add_argument("--account-id", type=int, default=1, help="账户ID（默认: 1）")
    p_run.add_argument("--strategy", type=str, default=None, help="临时指定策略（覆盖账户绑定）")
    p_run.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"), help="交易日期")

    args = parser.parse_args()

    # 子命令处理
    if args.subcommand == "list":
        cmd_list_accounts()
    elif args.subcommand == "create":
        cmd_create_account(args.account_id, args.name, args.cash, args.strategy, args.force)
    elif args.subcommand == "switch":
        cmd_switch_strategy(args.account_id, args.strategy)
    elif args.subcommand == "run":
        if args.mode in ("intraday_signal", "tail_signal"):
            run_signal(args.account_id, args.date, args.strategy)
        elif args.mode in ("intraday_execute", "tail_execute"):
            run_execute(args.account_id, args.date, args.strategy)
        elif args.mode == "report_only":
            run_report(args.account_id, args.date, args.strategy)
    else:
        parser.print_help()
