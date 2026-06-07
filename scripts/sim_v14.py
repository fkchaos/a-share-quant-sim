"""
v14 价量张力因子策略
===================

基于国联民生证券《量化专题报告：基于资金推动力的价量张力因子构建》(2026.05)

核心思想：
  价格偏离度 × 量能变化率 = 价量张力
  当价格偏离与量能变化出现"张力"（不一致）时，蕴含反转信号。

因子构建：
  1. 价格偏离度 = (CLOSE - MA(CLOSE, 20)) / MA(CLOSE, 20)
  2. 量能变化率 = VOLUME / MA(VOLUME, 20)
  3. 价量张力 = 价格偏离度 × 量能变化率
  4. 截面排名 = RANK(价量张力)

交易逻辑：
  - 周频调仓（每5个交易日）
  - 买入价量张力最低的股票（价格下跌但量能萎缩 = 恐慌减弱）
  - 卖出价量张力最高的股票（价格上涨但量能萎缩 = 动量衰竭）

与 v13 的区别：
  - v13: 纯反转因子（跌幅基础分 + 量价加减分），持仓2-5天
  - v14: 价量张力因子（价格偏离 × 量能变化），周频调仓，更侧重中期反转
"""
import sys, os
import pandas as pd
import numpy as np
from datetime import datetime

sys.path.insert(0, "/root/a-share-quant-sim")
sys.path.insert(0, os.path.dirname(__file__))

from core.account import PortfolioState, buy, sell, check_stop_loss, portfolio_value, check_take_profit, apply_holding_decay
from core.config import STRATEGY_PROFILES, TradingCosts, MarketFilter
from core.scoring import score_all_stocks
from core.factors import calc_factors_single
from core.strategy import StrategyEngine
from constraints import build_trade_context
from data_quality import DataQualityAuditor, print_quality_report
from portfolio_controls import cap_daily_turnover
from industry import get_industry, portfolio_industry_breakdown, cap_industry_weights
from indices import get_index_trends, IndexBenchmarkService
from sim_logging import get_logger

# ── Config ─────────────────────────────────────────────────────────
DATA_DIR = "/root/data"
PORTFOLIO_DIR = os.path.join(DATA_DIR, "portfolio")
DAILY_DIR = os.path.join(DATA_DIR, "daily")
SIGNAL_DIR = os.path.join(DATA_DIR, "signals")
os.makedirs(PORTFOLIO_DIR, exist_ok=True)
os.makedirs(SIGNAL_DIR, exist_ok=True)

PROFILE = "v17_price_volume_tension"
_strategy_profile = STRATEGY_PROFILES.get(PROFILE)
if _strategy_profile is None:
    # 使用 v13 的 profile 作为基础，覆盖部分参数
    _strategy_profile = STRATEGY_PROFILES["v13_small_mid_short"]
    print(f"⚠️  {PROFILE} profile 未在 config.py 中定义，使用 v13 profile 替代")

REBAL_FREQ = 5   # 周频调仓
STOP_LOSS = 0.05  # 5% 止损
TAKE_PROFIT = 0.10  # 10% 止盈
TOP_N = 12
MAX_SINGLE_WEIGHT = 0.10
MAX_DAILY_TURNOVER = 0.30

_costs = TradingCosts()
SLIPPAGE_RATE = _costs.slippage_rate
COMMISSION_RATE = _costs.commission_rate
INITIAL_CAPITAL = _costs.initial_capital

logger = get_logger("sim_v14")


# ── 价量张力因子计算 ───────────────────────────────────────────────

def calc_price_volume_tension_factors(code_dfs, date):
    """
    计算价量张力因子
    
    核心逻辑:
        价格偏离度 = (CLOSE - MA20) / MA20
        量能变化率 = VOLUME / MA20(VOLUME)
        价量张力 = 价格偏离度 × 量能变化率
    
    高张力 = 价格上涨但量能不足（动量衰竭）→ 看空
    低张力 = 价格下跌但量能萎缩（恐慌减弱）→ 看多（反转信号）
    """
    factors = {}
    
    for code, df in code_dfs.items():
        if len(df) < 25:
            continue
        
        try:
            # 价格偏离度
            ma20_close = df['close'].rolling(20).mean()
            price_deviation = (df['close'] - ma20_close) / (ma20_close + 1e-10)
            
            # 量能变化率
            ma20_volume = df['volume'].rolling(20).mean()
            volume_ratio = df['volume'] / (ma20_volume + 1e-10)
            
            # 价量张力
            tension = price_deviation * volume_ratio
            
            # 取当日值
            if date in df.index:
                idx = df.index.get_loc(date)
                factors[code] = {
                    'price_deviation': float(price_deviation.iloc[idx]),
                    'volume_ratio': float(volume_ratio.iloc[idx]),
                    'tension': float(tension.iloc[idx]),
                }
        except Exception:
            continue
    
    return factors


def score_stocks_v14(factors, current_holdings=None):
    """
    v14 评分函数
    
    评分逻辑:
        1. 基础分 = 价量张力截面排名（低张力 = 高分为看多）
        2. 振幅收窄加分
        3. 反转信号加分（近5日跌幅大）
    """
    if not factors:
        return {}
    
    codes = list(factors.keys())
    
    # 提取因子值
    tension_values = np.array([factors[c]['tension'] for c in codes])
    price_dev_values = np.array([factors[c]['price_deviation'] for c in codes])
    volume_ratio_values = np.array([factors[c]['volume_ratio'] for c in codes])
    
    # 截面排名（百分位）
    from scipy.stats import rankdata
    
    # 价量张力排名：低张力 → 高分为看多（反转信号）
    tension_rank = rankdata(tension_values) / len(codes)
    
    # 价格偏离排名：偏离度低（超跌）→ 高分为看多
    price_dev_rank = rankdata(price_dev_values) / len(codes)
    
    # 量能排名：量能萎缩 → 高分为看多
    volume_rank = rankdata(volume_ratio_values) / len(codes)
    
    # 综合评分
    scores = {}
    for i, code in enumerate(codes):
        # 基础分：价量张力（权重 0.5）
        base_score = tension_rank[i]
        
        # 反转信号：价格偏离（权重 0.3）- 超跌加分
        reversal_score = price_dev_rank[i]
        
        # 量能确认：量能萎缩（权重 0.2）
        volume_score = volume_rank[i]
        
        # 综合分
        total = base_score * 0.5 + reversal_score * 0.3 + volume_score * 0.2
        scores[code] = total
    
    return scores


# ── 主流程 ─────────────────────────────────────────────────────────

def run_sim_v14(mode="day_end"):
    """
    v14 价量张力因子策略
    
    支持模式:
        intraday_signal: 上午出信号
        intraday_execute: 下午执行
        report_only: 纯报告
        day_end: 日终完整流程
    """
    logger.info("=" * 70)
    logger.info(f"v14 价量张力因子策略 — {mode} ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    logger.info("=" * 70)
    
    # v14 信号文件
    plan_file = os.path.join(PORTFOLIO_DIR, "trade_plan_v14.json")
    account_file = os.path.join(PORTFOLIO_DIR, "account_v14.json")
    
    # ── Step 1: 加载账户 ──
    state = PortfolioState(cash=INITIAL_CAPITAL, initial_capital=INITIAL_CAPITAL)
    if os.path.exists(account_file):
        try:
            state = PortfolioState.load(account_file)
            logger.info(f"📂 加载账户: 净值={state.initial_capital:,.0f}")
        except Exception as e:
            logger.warning(f"账户加载失败: {e}，使用初始资金")
    
    # ── Step 2: 加载价格数据 ──
    price_data, code_dfs, latest_date = _load_prices()
    if latest_date is None:
        logger.error("价格数据加载失败")
        return None
    
    logger.info(f"📅 最新数据日期: {latest_date.date()}")
    logger.info(f"📊 股票数量: {len(code_dfs)}")
    
    # ── Step 3: 计算价量张力因子 ──
    factors = calc_price_volume_tension_factors(code_dfs, latest_date)
    logger.info(f"📈 计算价量张力因子: {len(factors)} 只")
    
    if not factors:
        logger.error("因子计算失败")
        return None
    
    # ── Step 4: 评分选股 ──
    scores = score_stocks_v14(factors, state.holdings)
    
    # 按分数排序
    sorted_stocks = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_stocks = [c for c, s in sorted_stocks[:TOP_N]]
    
    logger.info(f"📊 选股结果: {len(top_stocks)} 只")
    for i, code in enumerate(top_stocks[:5]):
        s = scores[code]
        t = factors[code]['tension']
        logger.info(f"  {i+1}. {code} 评分={s:.3f} 张力={t:.4f}")
    
    # ── Step 5: 风控检查 ──
    risk_sell = []
    for code in list(state.holdings.keys()):
        if code not in price_data.index:
            continue
        p = price_data[code]
        if pd.isna(p) or p <= 0:
            continue
        h = state.holdings[code]
        pnl = (p - h['cost_price']) / h['cost_price']
        if pnl <= -STOP_LOSS:
            risk_sell.append({'code': code, 'shares': 'all', 'price': float(p), 'reason': '止损'})
        elif pnl >= TAKE_PROFIT:
            risk_sell.append({'code': code, 'shares': 'all', 'price': float(p), 'reason': '止盈'})
    
    # ── Step 6: 生成操作计划 ──
    plan = {
        'mode': mode,
        'date': str(latest_date.date()),
        'timestamp': datetime.now().isoformat(),
        'strategy': 'v17_price_volume_tension',
        'rebal_freq': REBAL_FREQ,
        'top_stocks': top_stocks,
        'scores': {c: float(s) for c, s in scores.items() if c in top_stocks},
        'factors': {c: factors[c] for c in top_stocks if c in factors},
        'risk_sell': risk_sell,
        'current_holdings': {c: state.holdings[c] for c in state.holdings},
    }
    
    # 保存计划
    import json
    with open(plan_file, 'w') as f:
        json.dump(plan, f, indent=2, default=str)
    
    # ── Step 7: 执行（非纯报告模式） ──
    if mode != "report_only":
        # 风控卖出
        for rs in risk_sell:
            code = rs['code']
            if code in state.holdings:
                p = rs['price']
                state = sell(state, code, p, latest_date, reason=rs['reason'])
                logger.info(f"⚠️ 风控卖出: {code} @ {p:.2f} ({rs['reason']})")
        
        # 调仓判断
        need_rebalance = False
        if mode == "day_end":
            need_rebalance = True
        elif mode in ("intraday_signal", "intraday_execute"):
            # 检查是否到了调仓日
            if state.holdings:
                first_code = list(state.holdings.keys())[0]
                entry_date = state.holdings[first_code].get('entry_date', None)
                if entry_date:
                    days_held = (latest_date - pd.Timestamp(entry_date)).days
                    need_rebalance = days_held >= REBAL_FREQ
            else:
                need_rebalance = True
        
        if need_rebalance:
            # 卖出不在目标中的
            to_sell = [c for c in list(state.holdings.keys()) if c not in top_stocks]
            for code in to_sell:
                if code in price_data.index:
                    p = price_data[code]
                    if not pd.isna(p) and p > 0:
                        state = sell(state, code, p, latest_date, reason='调仓卖出')
            
            # 买入目标股票
            current_pv = portfolio_value(state, latest_date, price_data) if state.holdings else INITIAL_CAPITAL
            invest_per_stock = current_pv / TOP_N * (1 - SLIPPAGE_RATE)
            
            for code in top_stocks:
                if code not in state.holdings and code in price_data.index:
                    p = price_data[code]
                    if not pd.isna(p) and p > 0:
                        target_value = min(invest_per_stock, current_pv * MAX_SINGLE_WEIGHT)
                        state = buy(state, code, p, latest_date, target_value=target_value)
        
        # 保存账户
        state.save(account_file)
    
    # ── Step 8: 报告 ──
    report = _generate_report(state, latest_date, price_data, plan)
    
    logger.info("=" * 70)
    return report


def _load_prices():
    """加载价格数据"""
    import glob
    
    price_data = {}
    code_dfs = {}
    dates = set()
    
    for csv_file in glob.glob(os.path.join(DAILY_DIR, "*.csv")):
        code = os.path.basename(csv_file).replace(".csv", "")
        try:
            df = pd.read_csv(csv_file, index_col='date', parse_dates=True)
            if len(df) > 0 and 'close' in df.columns:
                code_dfs[code] = df
                price_data[code] = df['close'].iloc[-1]
                dates.add(df.index[-1])
        except Exception:
            continue
    
    latest_date = max(dates) if dates else None
    price_series = pd.Series(price_data)
    return price_series, code_dfs, latest_date


def _generate_report(state, latest_date, price_data, plan):
    """生成报告"""
    pv = portfolio_value(state, latest_date, price_data)
    total_value = pv + state.cash
    
    report = {
        'date': str(latest_date.date()),
        'portfolio_value': float(pv) if not pd.isna(pv) else 0,
        'cash': float(state.cash),
        'total_value': float(total_value) if not pd.isna(total_value) else float(state.cash),
        'holdings_count': len(state.holdings),
        'top_stocks': plan.get('top_stocks', []),
    }
    
    logger.info(f"📊 组合净值: ¥{report['total_value']:,.0f}")
    logger.info(f"📊 持仓: {report['holdings_count']} 只")
    
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="v14 价量张力因子策略")
    parser.add_argument('mode', choices=['intraday_signal', 'intraday_execute', 'day_end', 'report_only'],
                        default='day_end', nargs='?')
    args = parser.parse_args()
    
    report = run_sim_v14(args.mode)
    if report:
        print(f"\n✅ v14 运行完成")
        print(f"净值: ¥{report['total_value']:,.0f}")
    else:
        print(f"\n❌ v14 运行失败")
