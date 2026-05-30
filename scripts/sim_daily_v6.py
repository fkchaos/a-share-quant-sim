"""
模拟盘交易 - 每日操作脚本 (v6, core-based)
=========================================
v6 变更：
  - 交易逻辑全部使用 core.account（PortfolioState + buy/sell/check_stop_loss）
  - 因子计算使用 core.factors.calc_factors_single
  - 评分使用 core.scoring.score_all_stocks
  - 与 run_backtest.py 共用同一套交易代码 → 回测/模拟盘 100% 一致
  - 删除了对 sim_account.SimAccount 的依赖
  - daily_operation() 拆分为 Pipeline 步骤（load→check→rebalance→report）
  - print → logging（控制台 + 文件双输出）

保留：
  - P0-1: A股交易约束（涨跌停/停牌/T+1 检查）
  - P0-2: 数据质量门禁
  - P0-3: 组合换手率上限控制
  - P1-1: 行业仓位上限约束
  - P1-2: 指数趋势展示
"""
import sys, os, pandas as pd, numpy as np, json, time, logging
from datetime import datetime

sys.path.insert(0, "/root")
sys.path.insert(0, os.path.dirname(__file__))

# ── Core engine (shared with run_backtest.py) ─────────────────────
from core.account import PortfolioState, buy, sell, check_stop_loss, portfolio_value
from core.config import config as core_config, STRATEGY_PROFILES

# ── Auxiliary modules ──────────────────────────────────────────────
from constraints import build_trade_context
from data_quality import DataQualityAuditor, print_quality_report
from portfolio_controls import cap_daily_turnover
from industry import get_industry, portfolio_industry_breakdown, cap_industry_weights
from indices import get_index_trends, IndexBenchmarkService

# ── Logging ────────────────────────────────────────────────────────
from sim_logging import get_logger

# ── Config ─────────────────────────────────────────────────────────
DATA_DIR = "data"
PORTFOLIO_DIR = os.path.join(DATA_DIR, "portfolio")
DAILY_DIR = os.path.join(DATA_DIR, "daily")
SIGNAL_DIR = os.path.join(DATA_DIR, "signals")
os.makedirs(PORTFOLIO_DIR, exist_ok=True)

# Strategy params — from STRATEGY_PROFILES (single source of truth)
_PROFILE = "v4_industry_cap"  # ← 切换策略：v4_baseline | v4_industry_cap | v5_tp_decay
_strategy_profile = STRATEGY_PROFILES[_PROFILE]

REBAL_FREQ = _strategy_profile.rebalance_freq
STOP_LOSS = _strategy_profile.stop_loss
TOP_N = _strategy_profile.top_n
MAX_INDUSTRY_WEIGHT = _strategy_profile.max_industry_weight
MAX_DAILY_TURNOVER = _strategy_profile.max_daily_turnover
MAX_SINGLE_WEIGHT = _strategy_profile.max_position  # 单只最大仓位（与回测一致）

# Trading costs
SLIPPAGE_RATE = core_config.costs.slippage_rate
COMMISSION_RATE = core_config.costs.commission_rate
INITIAL_CAPITAL = core_config.costs.initial_capital

logger.debug(f"策略 profile: {_PROFILE}, top_n={TOP_N}, freq={REBAL_FREQ}, sl={STOP_LOSS}, "
             f"ind_cap={MAX_INDUSTRY_WEIGHT}, turnover_cap={MAX_DAILY_TURNOVER}")

logger = get_logger("sim_daily")


# ═══════════════════════════════════════════════════════════════════
# Pipeline 步骤函数
# ═══════════════════════════════════════════════════════════════════

def step_update_data():
    """Step 0: 更新行情数据"""
    logger.info("📥 更新行情数据...")
    import subprocess
    result = subprocess.run(
        [sys.executable, os.path.join(os.path.expanduser("~"), "update_daily_data.py")],
        capture_output=True, text=True, timeout=300
    )
    for line in result.stdout.split('\n'):
        if any(k in line for k in ['📋', '📅', '✅', '🔄', '📊', '最新', '失败', '新增', '⚠️']):
            logger.info(f"  {line.strip()}")
    if result.returncode != 0:
        logger.warning(f"数据更新可能有问题: {result.stderr[:200]}")
    else:
        logger.info("数据更新完成")
    return result.returncode == 0


def step_load_account():
    """Step 1: 加载账户状态"""
    account_file = os.path.join(PORTFOLIO_DIR, "account.json")
    if os.path.exists(account_file):
        with open(account_file) as f:
            data = json.load(f)
        state = PortfolioState()
        state.cash = data['cash']
        state.initial_capital = data.get('initial_capital', INITIAL_CAPITAL)
        state.holdings = data['holdings']
        state.trade_log = data.get('trade_log', [])
        state.nav_history = data.get('nav_history', [])
        for code in state.holdings:
            state.holdings[code]['shares'] = int(state.holdings[code]['shares'])
        for entry in state.nav_history:
            if 'nav' not in entry and 'portfolio_value' in entry:
                entry['nav'] = entry['portfolio_value']
        logger.info(f"已加载账户: 现金 ¥{state.cash:,.0f}, 持仓 {len(state.holdings)} 只")
        return state, True
    logger.info(f"初始资金: ¥{INITIAL_CAPITAL:,.0f}")
    return PortfolioState(cash=INITIAL_CAPITAL, initial_capital=INITIAL_CAPITAL), False


def step_load_prices():
    """Step 2: 加载当日价格数据"""
    files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
    if not files:
        logger.error("没有找到日K线数据")
        return None, None, None, None

    sample_df = pd.read_csv(os.path.join(DAILY_DIR, files[0]), index_col='date', parse_dates=True)
    latest_date = sample_df.index[-1]
    logger.info(f"最新数据日期: {latest_date.date()}")

    price_data = pd.Series(dtype=float)
    high_data = pd.Series(dtype=float)
    low_data = pd.Series(dtype=float)
    code_dataframes = {}

    for f in files:
        code = f.replace(".csv", "")
        df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
        if latest_date in df.index:
            price_data[code] = df.loc[latest_date, 'close']
            if 'high' in df.columns:
                high_data[code] = df.loc[latest_date, 'high']
            if 'low' in df.columns:
                low_data[code] = df.loc[latest_date, 'low']
            code_dataframes[code] = df

    return latest_date, price_data, code_dataframes, files


def step_check_stop_loss(state, date, price_data, names):
    """Step 3: 止损检查"""
    prev_holdings = set(state.holdings.keys())
    state = check_stop_loss(state, date, price_data)
    stopped = prev_holdings - set(state.holdings.keys())
    if stopped:
        logger.warning(f"止损触发! {len(stopped)} 只")
        for code in stopped:
            logger.warning(f"  {code} {names.get(code, code)} 已止损卖出")
    return state, stopped


def step_data_quality(files, date):
    """Step 4: 数据质量门禁"""
    code_list = [f.replace(".csv", "") for f in files]
    auditor = DataQualityAuditor(code_list, daily_dir=DAILY_DIR, as_of=date)
    quality_result = auditor.audit()
    print_quality_report(quality_result)
    return not quality_result.approved


def step_rebalance(state, date, price_data, code_dataframes, files, loaded, names):
    """Step 5: 调仓（含 P0-1/P0-3/P1-1 约束）"""
    trade_count_file = os.path.join(PORTFOLIO_DIR, "trade_count.txt")
    trade_count = 0
    if os.path.exists(trade_count_file):
        with open(trade_count_file) as f:
            trade_count = int(f.read().strip())

    need_rebalance = (trade_count % REBAL_FREQ == 0) or not loaded
    if not need_rebalance:
        logger.info(f"非调仓日 (距下次调仓 {REBAL_FREQ - trade_count % REBAL_FREQ} 天)")
        return state, trade_count, None, None

    logger.info("🔄 调仓日")

    # 生成评分
    from core.scoring import score_all_stocks
    from core.factors import calc_factors_single
    all_factors = {}
    for f in files:
        code = f.replace(".csv", "")
        df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
        if len(df) > 120:
            all_factors[code] = calc_factors_single(df)
    scores = score_all_stocks(all_factors)

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_stocks = [code for code, _ in sorted_scores[:TOP_N]]

    logger.info(f"目标持仓 (Top {TOP_N}):")
    for i, code in enumerate(top_stocks):
        name = names.get(code, '—')
        s = scores.get(code, 0)
        p = price_data.get(code, 0)
        logger.info(f"  {i+1}. {code} {name:<10} 评分={s:.3f} 价格={p:.2f}")

    # P0-1: 批量构建 TradeContext
    trade_contexts = {}
    for code in top_stocks:
        if code in code_dataframes:
            ctx = build_trade_context(code, code_dataframes[code], date)
            if ctx:
                trade_contexts[code] = ctx

    # 卖出非目标持仓
    to_sell = [c for c in list(state.holdings.keys()) if c not in top_stocks]
    sell_blocked_codes = []
    if to_sell:
        logger.info(f"卖出 {len(to_sell)} 只:")
        for code in to_sell:
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    ctx = trade_contexts.get(code)
                    if code in code_dataframes and code not in trade_contexts:
                        ctx = build_trade_context(code, code_dataframes[code], date)
                    if ctx:
                        blocked, reason = ctx.is_sell_blocked()
                        if blocked:
                            logger.info(f"  ⏭️  {code} {names.get(code, code)} 【{reason}】暂无法卖出")
                            sell_blocked_codes.append(code)
                            continue
                    old_shares = state.holdings.get(code, {}).get('shares', 0)
                    state = sell(state, code, p, date, 'SELL')
                    sold = old_shares > 0 and code not in state.holdings
                    if sold:
                        logger.info(f"  ❌ {code} {names.get(code, code)} 已卖出")

    # ── 权重分配（委托 core.account.allocate_weights）──
    from core.account import allocate_weights
    # 计算每只股票的 20 日波动率用于 vol_inverse 加权
    _vol_series = pd.Series(dtype=float)
    for _f in files:
        _code = _f.replace(".csv", "")
        _df = pd.read_csv(os.path.join(DAILY_DIR, _f), index_col='date', parse_dates=True)
        if len(_df) >= 21:
            _ret = _df['close'].pct_change().tail(20)
            _vol_series[_code] = _ret.std()

    _weight_method = 'vol_inverse' if core_config.risk.top_n > 0 else 'equal'
    # 先算初始权重（equal 或 vol_inverse）
    # 注意：max_position 约束在 cap_daily_turnover 之前应用
    target_weights = allocate_weights(
        top_stocks, price_data,
        method=_weight_method,
        vol_series=_vol_series,
        max_position=MAX_SINGLE_WEIGHT,  # 单只极限权重
    )
    # target_weights 已经归一化，sum ≈ 1.0
    weight_per_stock = 1.0 / TOP_N  # 等权重参考值（用于后续补仓比较）

    # P0-3/P1-1: 换手率控制 + 行业仓位上限
    current_pv = portfolio_value(state, date, price_data)
    price_dict = price_data.to_dict()

    turnover_info = None
    industry_info = None
    if target_weights:
        target_weights, turnover_info = cap_daily_turnover(
            None, target_weights, price_dict, max_turnover=MAX_DAILY_TURNOVER,
            current_state=state,
        )
        if turnover_info and turnover_info.get("applied"):
            logger.info(f"换手率控制: {turnover_info['requested_turnover']:.1%}→{turnover_info['max_turnover']:.1%}")

        code_industry_map = {c: get_industry(c, names.get(c, "")) for c in target_weights}
        target_weights, industry_info = cap_industry_weights(
            target_weights, code_industry_map, MAX_INDUSTRY_WEIGHT
        )
        if industry_info and industry_info.get("applied"):
            violated = industry_info.get("violated_industries", {})
            logger.info(f"行业仓位上限触发: {', '.join(f'{k}({v:.1%})' for k,v in violated.items())}")

    # 补仓到目标权重
    for code in top_stocks:
        if code in state.holdings and code in price_data.index:
            p = price_data[code]
            if pd.isna(p) or p <= 0:
                continue
            info = state.holdings[code]
            current_mv = info['shares'] * p
            current_w = current_mv / current_pv if current_pv > 0 else 0
            target_w = target_weights.get(code, weight_per_stock)
            if current_w < target_w * 0.8:
                target_mv = current_pv * target_w
                add_mv = target_mv - current_mv
                if add_mv > 10000:
                    adj_p = p * (1 + SLIPPAGE_RATE)
                    add_shares = int(add_mv / adj_p / 100) * 100
                    if add_shares > 0:
                        state = buy(state, code, p, date, shares=add_shares)
                        logger.info(f"  🔺 {code} {names.get(code, code)} 补仓 {add_shares} 股")

    # 买入新股票
    new_targets = [c for c in top_stocks if c not in state.holdings and c not in sell_blocked_codes]
    for code in new_targets:
        if code in price_data.index:
            p = price_data[code]
            if not pd.isna(p) and p > 0:
                ctx = trade_contexts.get(code)
                if ctx:
                    blocked, reason = ctx.is_buy_blocked()
                    if blocked:
                        logger.info(f"  ⏭️  {code} {names.get(code, code)} 【{reason}】无法买入")
                        continue
                old_holdings = set(state.holdings.keys())
                state = buy(state, code, p, date)
                if code in state.holdings and code not in old_holdings:
                    logger.info(f"  ✅ {code} {names.get(code, code)} 买入 @ {p:.2f}")
                else:
                    logger.info(f"  ⏭️  {code} {names.get(code, code)} 资金不足跳过")

    return state, 0, turnover_info, industry_info


def step_save_state(state, trade_count):
    """Step 6: 保存账户状态"""
    data = {
        'cash': state.cash,
        'initial_capital': state.initial_capital,
        'holdings': state.holdings,
        'trade_log': state.trade_log,
        'nav_history': state.nav_history,
        'last_update': str(datetime.now()),
    }
    with open(os.path.join(PORTFOLIO_DIR, "account.json"), 'w') as f:
        json.dump(data, f, indent=2, default=str)
    trade_count_file = os.path.join(PORTFOLIO_DIR, "trade_count.txt")
    with open(trade_count_file, 'w') as f:
        f.write(str(trade_count + 1))


def step_report(state, date, price_data, turnover_info, industry_info, quality_blocked, names):
    """Step 7: 生成收盘报告"""
    final_pv = portfolio_value(state, date, price_data)
    final_ret = (final_pv / state.initial_capital) - 1

    if len(state.nav_history) > 0:
        prev_nav = state.nav_history[-1].get('nav', state.nav_history[-1].get('portfolio_value', final_pv))
        daily_ret = (final_pv / prev_nav) - 1 if prev_nav > 0 else 0
    else:
        daily_ret = 0

    state.nav_history.append({
        'date': str(date),
        'nav': final_pv,
        'daily_return': daily_ret,
        'total_return': final_ret,
    })

    logger.info(f"📊 收盘报告")
    logger.info(f"  日期: {date.date()}")
    logger.info(f"  总净值: ¥{final_pv:,.0f}")
    logger.info(f"  今日收益: {daily_ret:+.2%}")
    logger.info(f"  总收益率: {final_ret:+.2%}")
    logger.info(f"  持仓: {len(state.holdings)} 只")
    logger.info(f"  现金占比: {state.cash/final_pv:.1%}")

    if turnover_info and turnover_info.get("applied"):
        logger.info(f"  换手率: {turnover_info['requested_turnover']:.1%}→{turnover_info['max_turnover']:.1%}")
    if industry_info and industry_info.get("applied"):
        logger.info(f"  行业上限: 触发 {len(industry_info.get('violated_industries',{}))} 个行业")

    # P1-2: 指数趋势
    try:
        index_trends = get_index_trends(os.path.join(DATA_DIR, "cache", "indices"))
        if index_trends:
            logger.info(IndexBenchmarkService.format_trends(index_trends))
    except Exception as e:
        logger.warning(f"指数趋势获取失败: {e}")

    # P1-1: 行业分布
    try:
        if state.holdings:
            code_industry_map = {c: get_industry(c, names.get(c, "")) for c in state.holdings}
            breakdown = portfolio_industry_breakdown(state.holdings, price_data, code_industry_map)
            if breakdown:
                for ind, w in list(breakdown.items())[:10]:
                    bar = "█" * int(w * 40)
                    logger.info(f"  {ind:<12} {w:>6.1%} {bar}")
    except Exception:
        pass

    if quality_blocked:
        logger.warning("数据质量门禁有阻塞问题，本次交易可能受影响")

    return {
        'date': str(date),
        'nav': final_pv,
        'daily_return': daily_ret,
        'total_return': final_ret,
        'holdings_count': len(state.holdings),
    }


def step_tomorrow_plan(state, date, price_data, names):
    """Step 8: 明日操作计划"""
    trade_count_file = os.path.join(PORTFOLIO_DIR, "trade_count.txt")
    trade_count = 0
    if os.path.exists(trade_count_file):
        with open(trade_count_file) as f:
            trade_count = int(f.read().strip())

    next_count = trade_count + 1
    is_rebal = (next_count % REBAL_FREQ == 0)

    if is_rebal:
        logger.info("⚡ 明天是调仓日！")
        from core.scoring import score_all_stocks
        from core.factors import calc_factors_single
        files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
        all_factors = {}
        for f in files:
            code = f.replace(".csv", "")
            df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
            if len(df) > 120:
                all_factors[code] = calc_factors_single(df)
        scores = score_all_stocks(all_factors)
        sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        top_stocks = [code for code, _ in sorted_scores[:TOP_N]]
        current_pv = portfolio_value(state, date, price_data)

        to_sell = [c for c in list(state.holdings.keys()) if c not in top_stocks]
        to_keep = [c for c in top_stocks if c in state.holdings]
        to_buy = [c for c in top_stocks if c not in state.holdings]

        if to_sell:
            logger.info(f"📉 卖出 ({len(to_sell)} 只):")
            for code in to_sell:
                if code in price_data.index and code in state.holdings:
                    p = price_data[code]
                    info = state.holdings[code]
                    mv = info['shares'] * p
                    pnl = (p - info['cost_price']) / info['cost_price']
                    logger.info(f"  ❌ {code} {names.get(code, code):<10} {info['shares']:>6}股  市值¥{mv:>10,.0f}  盈亏{pnl:>7.2%}")

        if to_keep:
            logger.info(f"🔄 保留/补仓 ({len(to_keep)} 只):")
            weight_per = 1.0 / TOP_N
            for code in to_keep:
                if code in price_data.index:
                    p = price_data[code]
                    info = state.holdings[code]
                    current_mv = info['shares'] * p
                    current_w = current_mv / current_pv if current_pv > 0 else 0
                    target_mv = current_pv * weight_per
                    diff = target_mv - current_mv
                    action = f"补仓¥{diff:,.0f}" if diff > 10000 else "持有不动"
                    logger.info(f"  🔺 {code} {names.get(code, code):<10} 当前权重{current_w:.1%}  目标{weight_per:.1%}  {action}")

        if to_buy:
            logger.info(f"✅ 新买入 ({len(to_buy)} 只):")
            for code in to_buy:
                if code in price_data.index:
                    p = price_data[code]
                    logger.info(f"  ✅ {code} {names.get(code, code):<10} 参考价={p:.2f}")


# ═══════════════════════════════════════════════════════════════════
# 主入口：Pipeline 编排
# ═══════════════════════════════════════════════════════════════════

def daily_operation():
    """每日操作 — Pipeline 编排"""
    logger.info("=" * 70)
    logger.info(f"v6 模拟交易 - {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    logger.info("=" * 70)

    # Step 0: 更新数据
    step_update_data()

    # Step 1: 加载账户
    state, loaded = step_load_account()

    # Step 2: 加载价格
    result = step_load_prices()
    if result[0] is None:
        return None
    latest_date, price_data, code_dataframes, files = result

    # 加载股票名称
    names = {}
    try:
        hs300 = pd.read_csv("/root/hs300_constituents.csv")
        names = dict(zip(hs300['品种代码'].astype(str).str.zfill(6), hs300['品种名称']))
    except Exception:
        pass

    # Step 3: 止损检查
    state, stopped = step_check_stop_loss(state, latest_date, price_data, names)

    # Step 4: 数据质量门禁
    quality_blocked = step_data_quality(files, latest_date)

    # Step 5: 调仓
    state, trade_count, turnover_info, industry_info = step_rebalance(
        state, latest_date, price_data, code_dataframes, files, loaded, names
    )

    # Step 6: 保存状态
    step_save_state(state, trade_count)

    # Step 7: 收盘报告
    report = step_report(state, latest_date, price_data, turnover_info, industry_info, quality_blocked, names)

    # Step 8: 明日计划
    step_tomorrow_plan(state, latest_date, price_data, names)

    # 保存 nav_history（step_report 已更新）
    step_save_state(state, trade_count)

    logger.info("=" * 70)
    return report


if __name__ == "__main__":
    try:
        report = daily_operation()
    except Exception as e:
        logger.error(f"❌ 错误: {e}", exc_info=True)
        import traceback
        traceback.print_exc()
