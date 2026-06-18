#!/usr/bin/env python3
"""
scripts/strategies/v11b_select.py — v11b 选股逻辑
==================================================
从 sim_account1.py 抽离的选股核心，复用 StrategyEngine。

接口与 v27/v20c 一致：
  - calc_factors(cp, vp, ap, hp, lp, op, params) → factors dict
  - select_stocks(factors, date, holdings, params) → [(code, score)]

但 v11b 实际走 StrategyEngine.score_single() + filter_stocks()，
所以 calc_factors 返回的是 panel 格式，select_stocks 内部调 engine。
"""
import sys, os, json, logging
import numpy as np
import pandas as pd

sys.path.insert(0, os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.config import STRATEGY_PROFILES, TradingCosts
from core.factors import calc_factors_panel_v11b
from core.strategy import StrategyEngine
from core.account import portfolio_value
from scripts.tools.constraints import build_trade_context
from scripts.tools.portfolio_controls import cap_daily_turnover
from scripts.tools.industry import get_industry, cap_industry_weights

logger = logging.getLogger("v11b_select")

_PROFILE = "v11b_zz800_union"
_strategy_profile = STRATEGY_PROFILES[_PROFILE]

REBAL_FREQ = _strategy_profile.rebalance_freq
STOP_LOSS = _strategy_profile.stop_loss
TOP_N = _strategy_profile.top_n
MAX_INDUSTRY_WEIGHT = _strategy_profile.max_industry_weight
MAX_DAILY_TURNOVER = _strategy_profile.max_daily_turnover
MAX_SINGLE_WEIGHT = _strategy_profile.max_position

_costs = TradingCosts()
SLIPPAGE_RATE = _costs.slippage_rate
COMMISSION_RATE = _costs.commission_rate

# StrategyEngine 实例（模块级单例）
_engine_mode = "ensemble"
_strategy_engine = StrategyEngine(
    profile=_PROFILE,
    mode=_engine_mode,
    hybrid_alpha=0.8,
    model_dir=os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "ml_models",
)


def calc_factors(cp, vp, ap, hp, lp, op=None, params=None):
    """
    计算 v11b 因子（panel 向量化）
    返回: {factor_name: DataFrame(date × codes)}
    注意：包含 'close' key，供 select_stocks 获取价格
    """
    factors = calc_factors_panel_v11b(cp, vp, hp, lp)
    factors["close"] = cp  # 价格数据，select_stocks 需要
    return factors


def select_stocks(factors, date, holdings, params=None, cp=None):
    """
    选股：StrategyEngine 评分 + 过滤
    cp: close panel (DataFrame date × codes)，用于获取价格。
        如果为 None，尝试从 factors 里找 'close' 因子。
    返回: [(code, score)]
    """
    # 确定最新日期
    _latest_date = None
    for fpanel in factors.values():
        if date in fpanel.index:
            _latest_date = date
            break
    if _latest_date is None:
        for fpanel in factors.values():
            if len(fpanel.index) > 0:
                _latest_date = fpanel.index[-1]
                break
    if _latest_date is None:
        return []

    # 构建 {code: {factor: float}} 格式
    all_factors = {}
    all_codes = set(holdings.keys())
    for fpanel in factors.values():
        all_codes.update(fpanel.columns)

    for code in all_codes:
        all_factors[code] = {}
        for fname, fpanel in factors.items():
            if _latest_date in fpanel.index and code in fpanel.columns:
                all_factors[code][fname] = fpanel.loc[_latest_date, code]

    # StrategyEngine 评分
    scores = _strategy_engine.score_single(all_factors)

    # 获取价格数据
    close_panel = factors.get("close")
    if close_panel is not None and _latest_date in close_panel.index:
        price_data = close_panel.loc[_latest_date]
    elif cp is not None and _latest_date in cp.index:
        price_data = cp.loc[_latest_date]
    else:
        price_data = pd.Series()

    current_pv = 0
    if holdings:
        for code, h in holdings.items():
            if code in price_data.index and not pd.isna(price_data[code]) and price_data[code] > 0:
                current_pv += h.get("shares", 0) * price_data[code]
        if current_pv == 0:
            current_pv = sum(h.get("shares", 0) * 100 for h in holdings.values())
    # 空持仓时用 initial_capital 作为组合净值（用于计算最小买入门槛）
    if current_pv == 0:
        current_pv = (params or {}).get("initial_capital", 100000)

    # 股票名称
    from core.db import get_stock_name_map
    names = get_stock_name_map()

    top_stocks, filtered_scores = _strategy_engine.filter_stocks(
        scores=scores,
        price_data=price_data,
        portfolio_value=current_pv,
        current_holdings=holdings,
        stock_names_map=names,
        get_industry_fn=get_industry,
    )

    result = [(code, float(scores.get(code, 0))) for code in top_stocks]
    logger.info(f"v11b 选股: {len(scores)} → {len(result)} 只")
    return result


def generate_plan(state, date, price_data, code_dataframes, names, risk_sell=None):
    """
    生成操作计划（从 step_generate_signal 抽离）
    返回: plan dict
    """
    from core.db import get_stock_name_map
    if not names:
        names = get_stock_name_map()

    trade_count_file = os.path.join(
        os.environ.get("PORTFOLIO_DIR", os.path.join(os.environ.get("PROJECT_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data" + "/portfolio"), "trade_count.txt"
    )
    trade_count = 0
    if os.path.exists(trade_count_file):
        with open(trade_count_file) as f:
            trade_count = int(f.read().strip())

    need_rebalance = (trade_count % REBAL_FREQ == 0) or not state.holdings
    current_pv = portfolio_value(state, date, price_data) if state.holdings else state.cash

    sell_plan = list(risk_sell or [])

    if not need_rebalance:
        logger.info(f"非调仓日 (距下次调仓 {REBAL_FREQ - trade_count % REBAL_FREQ} 天)")
        hold_plan = []
        for code in state.holdings:
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    info = state.holdings[code]
                    mv = info["shares"] * p
                    w = mv / current_pv if current_pv > 0 else 0
                    hold_plan.append({
                        "code": code, "name": names.get(code, code),
                        "current_shares": info["shares"], "price": float(p),
                        "current_weight": w, "target_weight": w,
                        "action": "hold", "add_amount": 0,
                    })
        return {
            "generated_at": str(pd.Timestamp.now()),
            "date": str(date), "trade_count": trade_count,
            "mode": "intraday_signal", "no_rebalance": True,
            "total_nav": float(current_pv),
            "sell_plan": sell_plan, "hold_plan": hold_plan, "buy_plan": [],
        }

    logger.info("🔄 调仓日 — 生成操作计划")

    # 因子计算
    _panels = code_dataframes.get("_panels")
    if _panels is None:
        from core.db import load_panel_from_db
        _panels, _ = load_panel_from_db(need_hl=True)

    _cp, _vp = _panels[0], _panels[1]
    _hp = _panels[3] if len(_panels) > 3 else None
    _lp = _panels[4] if len(_panels) > 4 else None
    _factors_panel = calc_factors_panel_v11b(_cp, _vp, _hp, _lp)

    _latest_date = _cp.index[-1]
    all_factors = {}
    for code in _cp.columns:
        all_factors[code] = {
            fname: fpanel.loc[_latest_date, code]
            for fname, fpanel in _factors_panel.items()
            if _latest_date in fpanel.index and code in fpanel.columns
        }

    scores = _strategy_engine.score_single(all_factors)
    top_stocks, filtered_scores = _strategy_engine.filter_stocks(
        scores=scores, price_data=price_data,
        portfolio_value=current_pv, current_holdings=state.holdings,
        stock_names_map=names, get_industry_fn=get_industry,
    )

    logger.info(f"选股: {len(scores)} → {len(top_stocks)} 只")

    weight_per_stock = 1.0 / TOP_N
    target_weights = {c: weight_per_stock for c in top_stocks}

    # 换手率 / 行业上限
    price_dict = price_data.to_dict() if hasattr(price_data, "to_dict") else {}
    if target_weights and price_dict:
        target_weights, _ = cap_daily_turnover(
            None, target_weights, price_dict,
            max_turnover=MAX_DAILY_TURNOVER, current_state=state,
        )
        code_ind_map = {c: get_industry(c, names.get(c, "")) for c in target_weights}
        target_weights, _ = cap_industry_weights(
            target_weights, code_ind_map, MAX_INDUSTRY_WEIGHT
        )

    to_sell = [c for c in list(state.holdings.keys()) if c not in top_stocks]
    to_keep = [c for c in top_stocks if c in state.holdings]
    to_buy = [c for c in top_stocks if c not in state.holdings]

    sell_plan, buy_plan, hold_plan = [], [], []
    REBALANCE_THRESHOLD = 0.8
    MIN_ADD_AMOUNT = 10000

    for code in to_sell:
        if code in price_data.index and code in state.holdings:
            p = price_data[code]
            info = state.holdings[code]
            sell_plan.append({
                "code": code, "name": names.get(code, code),
                "shares": info["shares"], "price": float(p),
                "cost_price": info["cost_price"], "reason": "非目标持仓",
            })

    for code in to_keep:
        if code in price_data.index:
            p = price_data[code]
            info = state.holdings[code]
            current_mv = info["shares"] * p
            current_w = current_mv / current_pv if current_pv > 0 else 0
            target_w = target_weights.get(code, weight_per_stock)
            target_mv = current_pv * target_w
            add_mv = target_mv - current_mv
            action = "add" if current_w < target_w * REBALANCE_THRESHOLD and add_mv > MIN_ADD_AMOUNT else "hold"
            hold_plan.append({
                "code": code, "name": names.get(code, code),
                "current_shares": info["shares"], "price": float(p),
                "current_weight": current_w, "target_weight": target_w,
                "action": action, "add_amount": max(0, float(add_mv)) if action == "add" else 0,
            })

    for code in to_buy:
        if code in price_data.index:
            p = price_data[code]
            target_w = target_weights.get(code, weight_per_stock)
            target_mv = current_pv * target_w
            buy_plan.append({
                "code": code, "name": names.get(code, code),
                "reference_price": float(p), "target_weight": target_w,
                "target_amount": float(target_mv),
            })

    return {
        "generated_at": str(pd.Timestamp.now()),
        "date": str(date), "trade_count": trade_count,
        "mode": "intraday_signal",
        "total_nav": float(current_pv) if current_pv else float(state.cash),
        "sell_plan": sell_plan, "hold_plan": hold_plan, "buy_plan": buy_plan,
    }
