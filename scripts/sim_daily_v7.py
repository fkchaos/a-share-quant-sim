"""
模拟盘交易 - 每日操作脚本 (v7, intraday support)
=============================================
v7 变更（vs v6）：
  - 支持盘中双阶段模式：上午出信号 + 下午开盘执行
  - 上午(11:35): 拉腾讯实时快照(上半天数据) + 日K线 → 算因子评分 → 生成操作计划
  - 下午(13:00): 拉开盘价 → 按计划执行交易 → 更新账户
  - 收盘(18:00): 日终报告（复用 v6 逻辑）
  - 新增腾讯实时行情接口 (qt.gtimg.cn)，89ms 响应
  - 新增批量实时快照拉取函数 fetch_tencent_spot_batch()
  - MODE 参数控制运行模式: intraday_signal | intraday_execute | day_end

复用 v6 所有核心逻辑:
  - 交易逻辑: core.account（PortfolioState + buy/sell/check_stop_loss）
  - 因子计算: core.factors.calc_factors_single
  - 评分: core.scoring.score_all_stocks
  - P0-1/P0-2/P0-3/P1-1/P1-2 约束全部保留
"""
import sys, os, json, time, logging
from datetime import datetime
import pandas as pd
import numpy as np
import requests

sys.path.insert(0, "/root/a-share-quant-sim")
sys.path.insert(0, os.path.dirname(__file__))

# ── Core engine (shared with run_backtest.py) ─────────────────────
from core.account import PortfolioState, buy, sell, check_stop_loss, portfolio_value, check_take_profit, apply_holding_decay
from core.config import config as core_config, STRATEGY_PROFILES
from core.scoring import score_all_stocks
from core.factors import calc_factors_single

# ── Auxiliary modules ──────────────────────────────────────────────
from constraints import build_trade_context
from data_quality import DataQualityAuditor, print_quality_report
from portfolio_controls import cap_daily_turnover
from industry import get_industry, portfolio_industry_breakdown, cap_industry_weights
from indices import get_index_trends, IndexBenchmarkService

# ── Logging ────────────────────────────────────────────────────────
from sim_logging import get_logger

# ── Config ─────────────────────────────────────────────────────────
_sim_data_dir = os.environ.get("BACKTEST_DATA_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
DATA_DIR = _sim_data_dir
PORTFOLIO_DIR = os.path.join(DATA_DIR, "portfolio")
DAILY_DIR = os.path.join(DATA_DIR, "daily")
SIGNAL_DIR = os.path.join(DATA_DIR, "signals")
os.makedirs(PORTFOLIO_DIR, exist_ok=True)
os.makedirs(SIGNAL_DIR, exist_ok=True)

# Strategy params — from STRATEGY_PROFILES
_PROFILE = "v6b_hlr"
_strategy_profile = STRATEGY_PROFILES[_PROFILE]

REBAL_FREQ = _strategy_profile.rebalance_freq
STOP_LOSS = _strategy_profile.stop_loss
TOP_N = _strategy_profile.top_n
MAX_INDUSTRY_WEIGHT = _strategy_profile.max_industry_weight
MAX_DAILY_TURNOVER = _strategy_profile.max_daily_turnover
MAX_SINGLE_WEIGHT = _strategy_profile.max_position

# Trading costs
SLIPPAGE_RATE = core_config.costs.slippage_rate
COMMISSION_RATE = core_config.costs.commission_rate
INITIAL_CAPITAL = core_config.costs.initial_capital

logger = get_logger("sim_daily")

logger.debug(f"策略 profile: {_PROFILE}, top_n={TOP_N}, freq={REBAL_FREQ}, sl={STOP_LOSS}, "
             f"ind_cap={MAX_INDUSTRY_WEIGHT}, turnover_cap={MAX_DAILY_TURNOVER}")


# ═══════════════════════════════════════════════════════════════════
# 腾讯实时行情接口 (qt.gtimg.cn)
# ═══════════════════════════════════════════════════════════════════

TX_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'http://stockapp.finance.qq.com/',
}


def _tx_code(code):
    """转换为腾讯行情代码前缀"""
    if code.startswith('6') or code.startswith('9'):
        return f"sh{code}"
    return f"sz{code}"


def fetch_tencent_spot_batch(codes, timeout=15):
    """
    批量拉取腾讯实时行情快照
    返回: dict {code: {name, code, price, prev_close, open, high, low, volume, amount, timestamp}}
    实测: 50只 86ms, 5000只 ≈ 200ms
    """
    tx_codes = [_tx_code(c) for c in codes]
    url = f"http://qt.gtimg.cn/q={','.join(tx_codes)}"

    try:
        r = requests.get(url, headers=TX_HEADERS, timeout=timeout)
        text = r.text.strip()
    except Exception as e:
        logger.error(f"实时行情请求失败: {e}")
        return {}

    results = {}
    for line in text.split(';'):
        line = line.strip()
        if '~' not in line:
            continue
        # 提取代码
        if '=' not in line:
            continue
        code_key = line.split('=')[0].split('_')[-1]
        parts = line.split('~')
        if len(parts) < 40:
            continue

        # 提取 6 位代码
        stock_code = parts[2]

        try:
            results[stock_code] = {
                'name': parts[1],
                'code': stock_code,
                'price': float(parts[3]),       # 当前价
                'prev_close': float(parts[4]),  # 昨收
                'open': float(parts[5]),        # 开盘
                'high': float(parts[33]),       # 最高
                'low': float(parts[34]),        # 最低
                'volume': float(parts[6]),      # 成交量(手)
                'amount': float(parts[37]) * 10000 if parts[37] else 0,  # 成交额(元)
                'timestamp': parts[30],         # 时间戳 yyyymmddHHMMSS
                'change_pct': float(parts[32]) if parts[32] else 0,
            }
        except (ValueError, IndexError):
            continue

    logger.info(f"实时行情: 请求 {len(codes)} 只, 返回 {len(results)} 只")
    return results


# ═══════════════════════════════════════════════════════════════════
# Pipeline 步骤函数（复用 v6 + 增强）
# ═══════════════════════════════════════════════════════════════════

def step_update_data():
    """Step 0: 更新行情数据 (复用项目目录的 update_daily_data.py)"""
    logger.info("📥 更新行情数据...")
    import subprocess
    script_dir = os.path.dirname(os.path.abspath(__file__))
    update_script = os.path.join(script_dir, "update_daily_data.py")
    env = os.environ.copy()
    # 确保子进程也使用相同的数据目录
    if "BACKTEST_DATA_DIR" in os.environ:
        env["BACKTEST_DATA_DIR"] = os.environ["BACKTEST_DATA_DIR"]
    result = subprocess.run(
        [sys.executable, update_script],
        capture_output=True, text=True, timeout=300,
        cwd=os.path.dirname(script_dir),  # 在项目根目录运行
        env=env
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
            if 'tp_taken' not in state.holdings[code]:
                state.holdings[code]['tp_taken'] = []
        for entry in state.nav_history:
            if 'nav' not in entry and 'portfolio_value' in entry:
                entry['nav'] = entry['portfolio_value']
        logger.info(f"已加载账户: 现金 ¥{state.cash:,.0f}, 持仓 {len(state.holdings)} 只")
        return state, True
    logger.info(f"初始资金: ¥{INITIAL_CAPITAL:,.0f}")
    return PortfolioState(cash=INITIAL_CAPITAL, initial_capital=INITIAL_CAPITAL), False


def step_load_prices(intraday=False):
    """
    Step 2: 加载当日价格数据
    intraday=False: 日终模式, 从本地CSV加载(日K线)
    intraday=True:  盘中模式, 用腾讯实时快照(上半天数据已包含开/高/低/收=当前价)

    返回: (date_str, price_data, code_dataframes, files)
        date_str: "2026-06-01_AM" 或 "2026-06-01"
        price_data: Series, index=code, value=price
        code_dataframes: dict {code: df} (仅在非盘中模式有完整数据)
        files: list of csv filenames
    """
    files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
    if not files:
        logger.error("没有找到日K线数据")
        return None, None, None, None

    if not intraday:
        # ── 日终模式: 本地 CSV ──
        sample_df = pd.read_csv(os.path.join(DAILY_DIR, files[0]), index_col='date', parse_dates=True)
        latest_date = sample_df.index[-1]
        logger.info(f"最新数据日期: {latest_date.date()}")

        price_data = pd.Series(dtype=float)
        code_dataframes = {}
        for f in files:
            code = f.replace(".csv", "")
            df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
            if latest_date in df.index:
                price_data[code] = df.loc[latest_date, 'close']
                code_dataframes[code] = df

        return str(latest_date.date()), price_data, code_dataframes, files

    else:
        # ── 盘中模式: 实时快照 ──
        logger.info("📡 拉取盘中实时行情...")
        codes = [f.replace(".csv", "") for f in files]
        t0 = time.time()
        spot_data = fetch_tencent_spot_batch(codes)
        elapsed = (time.time() - t0) * 1000
        logger.info(f"实时行情拉取耗时: {elapsed:.0f}ms")

        if not spot_data:
            logger.error("实时行情拉取失败，无法继续")
            return None, None, None, None

        price_data = pd.Series(dtype=float)
        code_dataframes = {}  # 盘中模式不需要完整DF

        for code in codes:
            if code in spot_data:
                sd = spot_data[code]
                # 盘中价格用当前价(实时快照的 close 字段)
                price_data[code] = sd['price']

        now = datetime.now()
        date_str = f"{now.strftime('%Y-%m-%d')}_AM"
        logger.info(f"盘中数据时间: {now.strftime('%H:%M')}, 有效股票 {len(price_data)} 只")

        # 同时加载本地CSV供因子计算用(用的是历史数据，不受盘中影响)
        for f in files:
            code = f.replace(".csv", "")
            try:
                df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
                code_dataframes[code] = df
            except Exception:
                pass

        return date_str, price_data, code_dataframes, files


def step_load_prices_pm(trade_plan):
    """
    Step 2b: 下午开盘时加载价格
    用实时快照获取开盘价和当前价, 优先用开盘价执行

    返回: (datetime, price_data)
    """
    files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
    codes = [f.replace(".csv", "") for f in files]
    # 只拉取计划中的股票 + 当前持仓
    plan_codes = set()
    for action in trade_plan.get('sell_plan', []):
        plan_codes.add(action['code'])
    for action in trade_plan.get('buy_plan', []):
        plan_codes.add(action['code'])
    # 加上当前持仓
    account_file = os.path.join(PORTFOLIO_DIR, "account.json")
    if os.path.exists(account_file):
        with open(account_file) as f:
            data = json.load(f)
        for code in data.get('holdings', {}):
            plan_codes.add(code)

    logger.info(f"📡 下午开盘价采集 ({len(plan_codes)} 只)...")
    t0 = time.time()
    spot_data = fetch_tencent_spot_batch(list(plan_codes))
    elapsed = (time.time() - t0) * 1000
    logger.info(f"实时行情耗时: {elapsed:.0f}ms")

    price_data = pd.Series(dtype=float)
    for code in plan_codes:
        if code in spot_data:
            sd = spot_data[code]
            # 13:00时 open 就是下午开盘价, price 是当前价
            # 优先用开盘价(open), 如果没有则用当前价
            exec_price = sd['open'] if sd['open'] > 0 else sd['price']
            price_data[code] = exec_price

    now = datetime.now()

    return now, price_data


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


def step_check_take_profit(state, date, price_data, names):
    """Step 3b: 分级止盈"""
    tp_tiers = _strategy_profile.tp_tiers or [(0.10, 0.30), (0.20, 0.30), (0.30, 1.00)]
    prev_holdings = {code: h['shares'] for code, h in state.holdings.items()}
    state = check_take_profit(state, date, price_data, tiers=tp_tiers)
    for code in prev_holdings:
        if code in state.holdings:
            if state.holdings[code]['shares'] < prev_holdings[code]:
                sold = prev_holdings[code] - state.holdings[code]['shares']
                logger.info(f"  🎯 {code} {names.get(code, code)} 分级止盈: 卖出 {sold} 股")
        elif code not in state.holdings:
            logger.info(f"  🎯 {code} {names.get(code, code)} 分级止盈: 全部清仓")
    return state


def step_holding_decay(state, date, price_data, names):
    """Step 3c: 持有期 decay"""
    prev_shares = {code: h['shares'] for code, h in state.holdings.items()}
    state = apply_holding_decay(state, date, price_data, rebalance_freq=REBAL_FREQ)
    for code in prev_shares:
        if code in state.holdings and state.holdings[code]['shares'] < prev_shares[code]:
            reduced = prev_shares[code] - state.holdings[code]['shares']
            logger.info(f"  📉 {code} {names.get(code, code)} 持有期decay: 减持 {reduced} 股")
    return state


def step_data_quality(files, date):
    """Step 4: 数据质量门禁"""
    code_list = [f.replace(".csv", "") for f in files]
    # Strip _AM/_PM suffix for intraday modes (pd.to_datetime can't parse those)
    clean_date = date.split("_")[0] if "_" in date else date
    auditor = DataQualityAuditor(code_list, daily_dir=DAILY_DIR, as_of=clean_date)
    quality_result = auditor.audit()
    print_quality_report(quality_result)
    return not quality_result.approved


# ── 上午模式：只生成信号，不执行 ──

def step_generate_signal(state, date, price_data, code_dataframes, files, loaded, names):
    """
    Step 5 (AM): 上午收盘 → 生成操作计划
    算因子评分 → 确定目标持仓 → 对比当前持仓 → 输出买卖计划
    不修改 state，只生成 signal 文件
    """
    trade_count_file = os.path.join(PORTFOLIO_DIR, "trade_count.txt")
    trade_count = 0
    if os.path.exists(trade_count_file):
        with open(trade_count_file) as f:
            trade_count = int(f.read().strip())

    need_rebalance = (trade_count % REBAL_FREQ == 0) or not loaded
    if not need_rebalance:
        logger.info(f"非调仓日 (距下次调仓 {REBAL_FREQ - trade_count % REBAL_FREQ} 天)")
        # 仍然生成报告（展示当前持仓状态）
        return {"mode": "no_rebalance", "trade_count": trade_count}

    logger.info("🔄 调仓日 — 生成操作计划")

    # 生成评分
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
        logger.info(f"  {i+1}. {code} {name:<10} 评分={s:.3f} 当前价={p:.2f}")

    # ── 计算目标权重 ──
    _vol_series = pd.Series(dtype=float)
    for _f in files:
        _code = _f.replace(".csv", "")
        _df = pd.read_csv(os.path.join(DAILY_DIR, _f), index_col='date', parse_dates=True)
        if len(_df) >= 21:
            _ret = _df['close'].pct_change().tail(20)
            _vol_series[_code] = _ret.std()

    # 先算初始权重
    from core.account import allocate_weights
    target_weights = allocate_weights(
        top_stocks, price_data,
        method='vol_inverse',
        vol_series=_vol_series,
        max_position=MAX_SINGLE_WEIGHT,
    )

    # 换手率控制 / 行业上限
    current_pv = portfolio_value(state, date, price_data) if state.holdings else INITIAL_CAPITAL
    price_dict = price_data.to_dict()
    turnover_info = None
    industry_info = None

    if target_weights:
        target_weights, turnover_info = cap_daily_turnover(
            None, target_weights, price_dict, max_turnover=MAX_DAILY_TURNOVER,
            current_state=state,
        )
        code_industry_map = {c: get_industry(c, names.get(c, "")) for c in target_weights}
        target_weights, industry_info = cap_industry_weights(
            target_weights, code_industry_map, MAX_INDUSTRY_WEIGHT
        )

    # ── 生成操作计划 ──
    to_sell = [c for c in list(state.holdings.keys()) if c not in top_stocks]
    to_keep = [c for c in top_stocks if c in state.holdings]
    to_buy = [c for c in top_stocks if c not in state.holdings]

    sell_plan = []
    buy_plan = []
    hold_plan = []

    weight_per = 1.0 / TOP_N
    REBALANCE_THRESHOLD = 0.8
    MIN_ADD_AMOUNT = 10000

    for code in to_sell:
        if code in price_data.index and code in state.holdings:
            p = price_data[code]
            info = state.holdings[code]
            sell_plan.append({
                'code': code,
                'name': names.get(code, code),
                'shares': info['shares'],
                'price': float(p),
                'cost_price': info['cost_price'],
                'reason': '非目标持仓'
            })

    for code in to_keep:
        if code in price_data.index:
            p = price_data[code]
            info = state.holdings[code]
            current_mv = info['shares'] * p
            current_w = current_mv / current_pv if current_pv > 0 else 0
            target_w = target_weights.get(code, weight_per)
            target_mv = current_pv * target_w
            add_mv = target_mv - current_mv
            action = "add" if current_w < target_w * REBALANCE_THRESHOLD and add_mv > MIN_ADD_AMOUNT else "hold"

            hold_plan.append({
                'code': code,
                'name': names.get(code, code),
                'current_shares': info['shares'],
                'price': float(p),
                'current_weight': current_w,
                'target_weight': target_w,
                'action': action,
                'add_amount': max(0, float(add_mv)) if action == "add" else 0,
            })

    for code in to_buy:
        if code in price_data.index:
            p = price_data[code]
            target_w = target_weights.get(code, weight_per)
            target_mv = current_pv * target_w
            buy_plan.append({
                'code': code,
                'name': names.get(code, code),
                'reference_price': float(p),
                'target_weight': target_w,
                'target_amount': float(target_mv),
            })

    # 保存操作计划
    plan = {
        'generated_at': str(datetime.now()),
        'date': str(date),
        'trade_count': trade_count,
        'mode': 'intraday_signal',
        'total_nav': float(current_pv) if current_pv else float(INITIAL_CAPITAL),
        'sell_plan': sell_plan,
        'hold_plan': hold_plan,
        'buy_plan': buy_plan,
    }

    plan_file = os.path.join(PORTFOLIO_DIR, "trade_plan.json")
    with open(plan_file, 'w') as f:
        json.dump(plan, f, indent=2, default=str, ensure_ascii=False)

    logger.info(f"✅ 操作计划已保存 → {plan_file}")

    # ── 输出计划摘要 ──
    logger.info("═" * 50)
    logger.info("📋 下午操作计划")
    logger.info("═" * 50)

    if sell_plan:
        logger.info(f"📉 卖出 ({len(sell_plan)} 只):")
        for item in sell_plan:
            pnl = (item['price'] - item['cost_price']) / item['cost_price']
            mv = item['shares'] * item['price']
            logger.info(f"  ❌ {item['code']} {item['name']:<8} {item['shares']:>6}股  "
                        f"市值¥{mv:>10,.0f}  盈亏{pnl:>7.2%}  (13:00按开盘价)")

    if hold_plan:
        logger.info(f"🔄 保留/补仓 ({len(hold_plan)} 只):")
        for item in hold_plan:
            if item['action'] == "add":
                add_shares = int(item['add_amount'] / item['price'] / 100) * 100
                logger.info(f"  🔺 {item['code']} {item['name']:<8} 权重{item['current_weight']:.1%}→{item['target_weight']:.1%}  "
                            f"补仓¥{item['add_amount']:,.0f} (≈{add_shares}股)")
            else:
                logger.info(f"  ➡️  {item['code']} {item['name']:<8} 权重{item['current_weight']:.1%}  持有不动")

    if buy_plan:
        logger.info(f"✅ 新买入 ({len(buy_plan)} 只):")
        for item in buy_plan:
            est_shares = int(item['target_amount'] / item['reference_price'] / 100) * 100
            logger.info(f"  ✅ {item['code']} {item['name']:<8} 参考价={item['reference_price']:.2f}  "
                        f"目标¥{item['target_amount']:,.0f} (≈{est_shares}股)")

    logger.info("═" * 50)
    logger.info(f"💰 当前净值: ¥{current_pv:,.0f}" if current_pv else "")
    logger.info(f"⏰ 计划执行时间: 13:00 (下午开盘)")

    return plan


# ── 下午模式：加载计划，执行交易 ──

def step_execute_plan(state, date, price_data, names, code_dataframes=None):
    """
    Step 5 (PM): 下午开盘 → 加载上午生成的计划 → 按计划执行交易
    """
    plan_file = os.path.join(PORTFOLIO_DIR, "trade_plan.json")
    if not os.path.exists(plan_file):
        logger.error("没有找到操作计划 (trade_plan.json)，无法执行")
        return state, None

    with open(plan_file) as f:
        plan = json.load(f)

    if not plan.get('sell_plan') and not plan.get('buy_plan') and not any(h.get('action') == 'add' for h in plan.get('hold_plan', [])):
        logger.info("操作计划为空，无需执行")
        return state, plan

    # 记录执行结果
    exec_report = {
        'executed_at': str(datetime.now()),
        'date': str(date),
        'results': [],
    }

    logger.info("═" * 50)
    logger.info("🔨 执行操作计划")
    logger.info("═" * 50)

    # 1. 卖出
    for item in plan.get('sell_plan', []):
        code = item['code']
        if code in state.holdings and code in price_data.index:
            p = price_data[code]
            if pd.isna(p) or p <= 0:
                logger.warning(f"  ⚠️ {code} 价格无效 ({p}), 跳过")
                continue

            # P0-1: 涨跌停检查
            if code in code_dataframes:
                ctx = build_trade_context(code, code_dataframes[code], date)
                if ctx:
                    blocked, reason = ctx.is_sell_blocked()
                    if blocked:
                        logger.warning(f"  ⏭️ {code} {item['name']} 【{reason}】暂无法卖出")
                        exec_report['results'].append({'code': code, 'action': 'sell', 'status': 'blocked', 'reason': reason})
                        continue

            old_shares = state.holdings[code]['shares']
            state = sell(state, code, p, date, 'SELL')
            logger.info(f"  ❌ {code} {item['name']} 卖出 {old_shares}股 @ {p:.2f}")
            exec_report['results'].append({'code': code, 'action': 'sell', 'status': 'done', 'shares': old_shares, 'price': float(p)})
        else:
            logger.info(f"  ⏭️ {code} 不在持仓中, 跳过")

    # 2. 补仓
    for item in plan.get('hold_plan', []):
        if item.get('action') != 'add':
            continue
        code = item['code']
        if code not in state.holdings or code not in price_data.index:
            continue

        p = price_data[code]
        if pd.isna(p) or p <= 0:
            continue

        add_mv = item.get('add_amount', 0)
        if add_mv <= 0:
            continue

        adj_p = p * (1 + SLIPPAGE_RATE)
        add_shares = int(add_mv / adj_p / 100) * 100
        if add_shares > 0:
            state = buy(state, code, p, date, shares=add_shares)
            logger.info(f"  🔺 {code} {item['name']} 补仓 {add_shares}股 @ {p:.2f} (含滑点)")
            exec_report['results'].append({'code': code, 'action': 'add', 'status': 'done', 'shares': add_shares, 'price': float(p)})

    # 3. 买入新股票
    for item in plan.get('buy_plan', []):
        code = item['code']
        if code in state.holdings:
            continue  # 已持仓（可能补仓后已有）
        if code not in price_data.index:
            continue

        p = price_data[code]
        if pd.isna(p) or p <= 0:
            continue

        # P0-1: 涨跌停检查
        if code in code_dataframes:
            ctx = build_trade_context(code, code_dataframes[code], date)
            if ctx:
                blocked, reason = ctx.is_buy_blocked()
                if blocked:
                    logger.warning(f"  ⏭️ {code} {item['name']} 【{reason}】无法买入")
                    exec_report['results'].append({'code': code, 'action': 'buy', 'status': 'blocked', 'reason': reason})
                    continue

        target_mv = item.get('target_amount', 0)
        adj_p = p * (1 + SLIPPAGE_RATE)
        est_shares = int(target_mv / adj_p / 100) * 100

        old_holdings = set(state.holdings.keys())
        state = buy(state, code, p, date, shares=max(0, est_shares))
        if code in state.holdings and code not in old_holdings:
            logger.info(f"  ✅ {code} {item['name']} 买入 @ {p:.2f}")
            exec_report['results'].append({'code': code, 'action': 'buy', 'status': 'done', 'price': float(p)})
        elif code in state.holdings:
            logger.info(f"  ✅ {code} {item['name']} 增仓 @ {p:.2f}")
            exec_report['results'].append({'code': code, 'action': 'add', 'status': 'done', 'price': float(p)})
        else:
            logger.info(f"  ⏭️ {code} {item['name']} 资金不足跳过")
            exec_report['results'].append({'code': code, 'action': 'buy', 'status': 'skipped', 'reason': '资金不足'})

    # 更新 trade_count
    trade_count = plan.get('trade_count', 0) + 1
    trade_count_file = os.path.join(PORTFOLIO_DIR, "trade_count.txt")
    with open(trade_count_file, 'w') as f:
        f.write(str(trade_count))

    # 保存执行报告
    report_file = os.path.join(PORTFOLIO_DIR, "exec_report.json")
    with open(report_file, 'w') as f:
        json.dump(exec_report, f, indent=2, default=str, ensure_ascii=False)

    logger.info("═" * 50)
    logger.info(f"✅ 执行完成 → {report_file}")

    return state, plan


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
        f.write(str(trade_count))


def step_report(state, date, price_data, names, mode="day_end"):
    """Step 7: 生成报告"""
    final_pv = portfolio_value(state, date, price_data)
    final_ret = (final_pv / state.initial_capital) - 1

    daily_ret = 0
    if len(state.nav_history) > 0:
        prev_nav = state.nav_history[-1].get('nav', state.nav_history[-1].get('portfolio_value', final_pv))
        daily_ret = (final_pv / prev_nav) - 1 if prev_nav > 0 else 0

    state.nav_history.append({
        'date': str(date),
        'nav': final_pv,
        'daily_return': daily_ret,
        'total_return': final_ret,
    })

    logger.info(f"📊 收盘报告 ({mode})")
    logger.info(f"  日期: {date}")
    logger.info(f"  总净值: ¥{final_pv:,.0f}")
    logger.info(f"  今日收益: {daily_ret:+.2%}")
    logger.info(f"  总收益率: {final_ret:+.2%}")
    logger.info(f"  持仓: {len(state.holdings)} 只")
    logger.info(f"  现金占比: {state.cash/final_pv:.1%}")

    # 指数趋势
    try:
        index_trends = get_index_trends(os.path.join(DATA_DIR, "cache", "indices"))
        if index_trends:
            logger.info(IndexBenchmarkService.format_trends(index_trends))
    except Exception as e:
        logger.warning(f"指数趋势获取失败: {e}")

    # 行业分布
    try:
        if state.holdings:
            code_industry_map = {c: get_industry(c, names.get(c, "")) for c in state.holdings}
            breakdown = portfolio_industry_breakdown(state.holdings, price_data, code_industry_map)
            if breakdown:
                logger.info("行业分布:")
                for ind, w in list(breakdown.items())[:10]:
                    bar = "█" * int(w * 40)
                    logger.info(f"  {ind:<12} {w:>6.1%} {bar}")
    except Exception:
        pass

    # 持仓明细
    if state.holdings:
        logger.info("持仓明细:")
        for code, info in state.holdings.items():
            if code in price_data.index:
                p = price_data[code]
                mv = info['shares'] * p
                w = mv / final_pv if final_pv > 0 else 0
                pnl = (p - info['cost_price']) / info['cost_price']
                logger.info(f"  {code} {names.get(code, code):<8} {info['shares']:>6}股  "
                            f"市值¥{mv:>10,.0f} 权重{w:.1%} 盈亏{pnl:+.2%}")

    return {
        'date': str(date),
        'nav': final_pv,
        'daily_return': daily_ret,
        'total_return': final_ret,
        'holdings_count': len(state.holdings),
    }


# ═══════════════════════════════════════════════════════════════════
# 主入口：三种模式
# ═══════════════════════════════════════════════════════════════════

def run_intraday_signal():
    """
    上午收盘模式 (11:35)
    ── 拉取实时快照(上半天) + 日K线历史数据 → 算因子 → 生成操作计划 ──
    不修改账户, 只输出 plan 文件
    """
    logger.info("=" * 70)
    logger.info(f"v7 模拟交易 — 上午信号 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    logger.info("=" * 70)

    # Step 0: 更新日频数据 (盘中也可能有新数据)
    step_update_data()

    # Step 1: 加载账户
    state, loaded = step_load_account()

    # Step 2: 加载盘中价格 (实时快照 + 本地CSV)
    date, price_data, code_dataframes, files = step_load_prices(intraday=True)
    if date is None:
        logger.error("价格数据加载失败")
        return None

    # 股票名称
    names = {}
    try:
        hs300 = pd.read_csv("/root/hs300_constituents.csv")
        names = dict(zip(hs300['品种代码'].astype(str).str.zfill(6), hs300['品种名称']))
    except Exception:
        pass

    # Step 3: 止损检查
    state, stopped = step_check_stop_loss(state, date, price_data, names)

    # Step 3b: 分级止盈
    if _strategy_profile.use_take_profit:
        state = step_check_take_profit(state, date, price_data, names)

    # Step 3c: 持有期 decay
    if _strategy_profile.use_holding_decay:
        state = step_holding_decay(state, date, price_data, names)

    # Step 4: 数据质量
    quality_blocked = step_data_quality(files, date)

    # Step 5: 生成信号 (不执行)
    plan = step_generate_signal(state, date, price_data, code_dataframes, files, loaded, names)

    return plan


def run_intraday_execute():
    """
    下午开盘模式 (13:00)
    ── 加载上午生成的计划 → 拉开盘价 → 执行交易 ──
    """
    logger.info("=" * 70)
    logger.info(f"v7 模拟交易 — 下午执行 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    logger.info("=" * 70)

    # Step 1: 加载账户
    state, loaded = step_load_account()

    # 股票名称
    names = {}
    try:
        hs300 = pd.read_csv("/root/hs300_constituents.csv")
        names = dict(zip(hs300['品种代码'].astype(str).str.zfill(6), hs300['品种名称']))
    except Exception:
        pass

    # Step 2: 加载计划 + 开盘价
    plan_file = os.path.join(PORTFOLIO_DIR, "trade_plan.json")
    if not os.path.exists(plan_file):
        logger.error("没有找到操作计划，请先运行上午信号")
        return None

    with open(plan_file) as f:
        plan = json.load(f)

    # 用实时快照获取开盘价
    date, price_data = step_load_prices_pm(plan)
    if date is None:
        logger.error("开盘价加载失败")
        return None

    # code_dataframes 用于涨跌停检查
    files = [f for f in os.listdir(DAILY_DIR) if f.endswith(".csv")]
    code_dataframes = {}
    for f in files:
        code = f.replace(".csv", "")
        try:
            df = pd.read_csv(os.path.join(DAILY_DIR, f), index_col='date', parse_dates=True)
            code_dataframes[code] = df
        except Exception:
            pass

    # Step 5: 执行计划
    state, plan = step_execute_plan(state, date, price_data, names, code_dataframes)

    # Step 6: 保存状态
    trade_count = plan.get('trade_count', 0) + 1 if plan else 0
    step_save_state(state, trade_count)

    # Step 7: 报告
    report = step_report(state, date, price_data, names, mode="intraday_pm")

    logger.info("=" * 70)
    return report


def run_day_end():
    """
    日终模式 (18:00) — 与 v6 逻辑一致
    ── 收盘后拉日频数据 → 完整流程 → 日终报告 ──
    """
    logger.info("=" * 70)
    logger.info(f"v7 模拟交易 — 日终 ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    logger.info("=" * 70)

    # Step 0: 更新数据
    step_update_data()

    # Step 1: 加载账户
    state, loaded = step_load_account()

    # Step 2: 加载价格 (本地CSV, 日终)
    result = step_load_prices(intraday=False)
    if result[0] is None:
        return None
    latest_date, price_data, code_dataframes, files = result

    # 股票名称
    names = {}
    try:
        hs300 = pd.read_csv("/root/hs300_constituents.csv")
        names = dict(zip(hs300['品种代码'].astype(str).str.zfill(6), hs300['品种名称']))
    except Exception:
        pass

    # Step 3: 止损
    state, stopped = step_check_stop_loss(state, latest_date, price_data, names)

    # Step 3b: 分级止盈
    if _strategy_profile.use_take_profit:
        state = step_check_take_profit(state, latest_date, price_data, names)

    # Step 3c: 持有期 decay
    if _strategy_profile.use_holding_decay:
        state = step_holding_decay(state, latest_date, price_data, names)

    # Step 4: 数据质量
    quality_blocked = step_data_quality(files, latest_date)

    # Step 5: 调仓 (日终模式直接执行)
    # 复用 v6 的调仓逻辑 (直接 buy/sell)
    trade_count_file = os.path.join(PORTFOLIO_DIR, "trade_count.txt")
    trade_count = 0
    if os.path.exists(trade_count_file):
        with open(trade_count_file) as f:
            trade_count = int(f.read().strip())

    need_rebalance = (trade_count % REBAL_FREQ == 0) or not loaded
    if not need_rebalance:
        logger.info(f"非调仓日 (距下次调仓 {REBAL_FREQ - trade_count % REBAL_FREQ} 天)")
        trade_count_final = trade_count
        turnover_info = None
        industry_info = None
    else:
        logger.info("🔄 调仓日 (日终模式)")

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

        # 构建 trade context
        trade_contexts = {}
        for code in top_stocks:
            if code in code_dataframes:
                ctx = build_trade_context(code, code_dataframes[code], latest_date)
                if ctx:
                    trade_contexts[code] = ctx

        # 卖出非目标
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
                            ctx = build_trade_context(code, code_dataframes[code], latest_date)
                        if ctx:
                            blocked, reason = ctx.is_sell_blocked()
                            if blocked:
                                logger.info(f"  ⏭️  {code} {names.get(code, code)} 【{reason}】暂无法卖出")
                                sell_blocked_codes.append(code)
                                continue
                        old_shares = state.holdings.get(code, {}).get('shares', 0)
                        state = sell(state, code, p, latest_date, 'SELL')
                        sold = old_shares > 0 and code not in state.holdings
                        if sold:
                            logger.info(f"  ❌ {code} {names.get(code, code)} 已卖出")

        # 权重分配
        from core.account import allocate_weights
        _vol_series = pd.Series(dtype=float)
        for _f in files:
            _code = _f.replace(".csv", "")
            _df = pd.read_csv(os.path.join(DATA_DIR, "daily", _f), index_col='date', parse_dates=True)
            if len(_df) >= 21:
                _ret = _df['close'].pct_change().tail(20)
                _vol_series[_code] = _ret.std()

        target_weights = allocate_weights(top_stocks, price_data, method='vol_inverse',
                                          vol_series=_vol_series, max_position=MAX_SINGLE_WEIGHT)
        weight_per_stock = 1.0 / TOP_N

        current_pv = portfolio_value(state, latest_date, price_data)
        price_dict = price_data.to_dict()

        turnover_info = None
        industry_info = None
        if target_weights:
            target_weights, turnover_info = cap_daily_turnover(
                None, target_weights, price_dict, max_turnover=MAX_DAILY_TURNOVER, current_state=state)
            code_industry_map = {c: get_industry(c, names.get(c, "")) for c in target_weights}
            target_weights, industry_info = cap_industry_weights(
                target_weights, code_industry_map, MAX_INDUSTRY_WEIGHT)

        REBALANCE_THRESHOLD = 0.8
        MIN_ADD_AMOUNT = 10000

        # 补仓
        for code in top_stocks:
            if code in state.holdings and code in price_data.index:
                p = price_data[code]
                if pd.isna(p) or p <= 0:
                    continue
                info = state.holdings[code]
                current_mv = info['shares'] * p
                current_w = current_mv / current_pv if current_pv > 0 else 0
                target_w = target_weights.get(code, weight_per_stock)
                if current_w < target_w * REBALANCE_THRESHOLD:
                    target_mv = current_pv * target_w
                    add_mv = target_mv - current_mv
                    if add_mv > MIN_ADD_AMOUNT:
                        adj_p = p * (1 + SLIPPAGE_RATE)
                        add_shares = int(add_mv / adj_p / 100) * 100
                        if add_shares > 0:
                            state = buy(state, code, p, latest_date, shares=add_shares)
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
                    state = buy(state, code, p, latest_date)
                    if code in state.holdings and code not in old_holdings:
                        logger.info(f"  ✅ {code} {names.get(code, code)} 买入 @ {p:.2f}")
                    else:
                        logger.info(f"  ⏭️  {code} {names.get(code, code)} 资金不足跳过")

        trade_count_final = trade_count + 1

    # Step 6: 保存
    step_save_state(state, trade_count_final)

    # Step 7: 收盘报告
    report = step_report(state, latest_date, price_data, names, mode="day_end")

    # Step 8: 保存 (含 nav_history)
    step_save_state(state, trade_count_final)

    logger.info("=" * 70)
    return report


# ═══════════════════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="v7 模拟交易 — 支持盘中双阶段")
    parser.add_argument('mode', choices=['intraday_signal', 'intraday_execute', 'day_end'],
                        default='day_end', nargs='?',
                        help='运行模式: intraday_signal=上午出信号, intraday_execute=下午执行, day_end=日终')
    args = parser.parse_args()

    try:
        if args.mode == 'intraday_signal':
            report = run_intraday_signal()
        elif args.mode == 'intraday_execute':
            report = run_intraday_execute()
        else:
            report = run_day_end()

        if report:
            nav = report.get('nav', None)
            if nav:
                try:
                    nav_str = f"¥{float(nav):,.0f}"
                except (ValueError, TypeError):
                    nav_str = f"¥{nav}"
                logger.info(f"\n📊 运行完成, 净值: {nav_str}")
            elif 'sell_plan' in report or 'buy_plan' in report:
                sell_n = len(report.get('sell_plan', []))
                buy_n = len(report.get('buy_plan', []))
                logger.info(f"\n📊 运行完成, 信号: 卖 {sell_n} 只 / 买 {buy_n} 只")
            elif report.get('mode') == 'no_rebalance':
                logger.info(f"\n📊 运行完成, 非调仓日 (距下次调仓 {report.get('trade_count', '?')})")
            else:
                logger.info(f"\n📊 运行完成")
    except Exception as e:
        logger.error(f"❌ 错误: {e}", exc_info=True)
        import traceback
        traceback.print_exc()
