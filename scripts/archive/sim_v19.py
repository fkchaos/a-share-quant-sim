"""
v19 球队硬币因子策略
=====================

基于方正金工《多因子选股系列研究之四：个股动量效应识别及"球队硬币"因子构建》

核心思想：
  "球队硬币"因子识别个股动量效应。
  动量效应在 A 股特定市场状态下有效（牛市/震荡市）。
  与 v13 反转因子互补：反转因子在熊市有效，动量因子在牛市有效。

因子构建：
  1. 短期动量 = 5日收益率排名
  2. 中期动量 = 10日收益率排名
  3. 长期动量 = 20日收益率排名
  4. RSI = 14日 RSI（超买超卖）
  5. 量比 = 当日成交量 / 20日平均成交量

交易逻辑：
  - 周频调仓（每5个交易日）
  - 综合评分 = 0.3×短期动量 + 0.25×中期动量 + 0.2×长期动量 + 0.15×RSI + 0.1×量比
  - 买入综合评分最高的股票

与 v13 的区别：
  - v13: 反转因子（跌幅基础分 + 量价加减分），熊市有效
  - v19: 动量因子（收益率排名 + RSI + 量比），牛市有效
  - 两者互补：可根据市场状态动态切换权重
"""
import sys, os
import pandas as pd
import numpy as np
from datetime import datetime

sys.path.insert(0, "/root/a-share-quant-sim")
sys.path.insert(0, os.path.dirname(__file__))

from core.account import PortfolioState, buy, sell, portfolio_value
from core.config import STRATEGY_PROFILES, TradingCosts
from sim_logging import get_logger

# ── Config ─────────────────────────────────────────────────────────
DATA_DIR = "/root/data"
PORTFOLIO_DIR = os.path.join(DATA_DIR, "portfolio")
DAILY_DIR = os.path.join(DATA_DIR, "daily")
os.makedirs(PORTFOLIO_DIR, exist_ok=True)

PROFILE = "v19_team_coin"
_strategy_profile = STRATEGY_PROFILES.get(PROFILE)
if _strategy_profile is None:
    _strategy_profile = STRATEGY_PROFILES["v13_small_mid_short"]

REBAL_FREQ = 5
STOP_LOSS = 0.05
TAKE_PROFIT = 0.10
TOP_N = 10

_costs = TradingCosts()
SLIPPAGE_RATE = _costs.slippage_rate
COMMISSION_RATE = _costs.commission_rate
INITIAL_CAPITAL = _costs.initial_capital

logger = get_logger("sim_v19")


def calc_team_coin_factors(code_dfs, date):
    """
    计算球队硬币因子
    
    核心逻辑:
        短期动量 = 5日收益率
        中期动量 = 10日收益率
        长期动量 = 20日收益率
        RSI = 14日 RSI
        量比 = VOLUME / MA20(VOLUME)
    """
    factors = {}
    
    for code, df in code_dfs.items():
        if len(df) < 25:
            continue
        
        try:
            # 动量因子
            mom_5 = df['close'].pct_change(5)
            mom_10 = df['close'].pct_change(10)
            mom_20 = df['close'].pct_change(20)
            
            # RSI
            delta = df['close'].diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / (loss + 1e-10)
            rsi = 100 - (100 / (1 + rs))
            
            # 量比
            ma20_vol = df['volume'].rolling(20).mean()
            vol_ratio = df['volume'] / (ma20_vol + 1e-10)
            
            if date in df.index:
                idx = df.index.get_loc(date)
                factors[code] = {
                    'mom_5': float(mom_5.iloc[idx]),
                    'mom_10': float(mom_10.iloc[idx]),
                    'mom_20': float(mom_20.iloc[idx]),
                    'rsi': float(rsi.iloc[idx]),
                    'vol_ratio': float(vol_ratio.iloc[idx]),
                }
        except Exception:
            continue
    
    return factors


def score_stocks_v19(factors, current_holdings=None):
    """
    v19 评分函数
    
    评分逻辑:
        动量强 → 高分为看多
        RSI 适中（40-60）→ 高分为看多
        量比适中 → 高分为看多
    """
    if not factors:
        return {}
    
    codes = list(factors.keys())
    mom_5_values = np.array([factors[c]['mom_5'] for c in codes])
    mom_10_values = np.array([factors[c]['mom_10'] for c in codes])
    mom_20_values = np.array([factors[c]['mom_20'] for c in codes])
    rsi_values = np.array([factors[c]['rsi'] for c in codes])
    vol_ratio_values = np.array([factors[c]['vol_ratio'] for c in codes])
    
    from scipy.stats import rankdata
    
    # 动量排名：高动量 → 高分为看多
    mom_5_rank = rankdata(mom_5_values) / len(codes)
    mom_10_rank = rankdata(mom_10_values) / len(codes)
    mom_20_rank = rankdata(mom_20_values) / len(codes)
    
    # RSI 排名：适中（40-60）→ 高分为看多
    rsi_rank = 1 - np.abs(rsi_values - 50) / 50  # 50为最优
    rsi_rank = np.clip(rsi_rank, 0, 1)
    
    # 量比排名：适中（0.8-1.2）→ 高分为看多
    vol_rank = 1 - np.abs(vol_ratio_values - 1) / 2  # 1为最优
    vol_rank = np.clip(vol_rank, 0, 1)
    
    scores = {}
    for i, code in enumerate(codes):
        total = (mom_5_rank[i] * 0.3 +
                 mom_10_rank[i] * 0.25 +
                 mom_20_rank[i] * 0.2 +
                 rsi_rank[i] * 0.15 +
                 vol_rank[i] * 0.1)
        scores[code] = total
    
    return scores


def run_sim_v19(mode="day_end"):
    """v19 球队硬币因子策略"""
    logger.info("=" * 70)
    logger.info(f"v19 球队硬币因子策略 — {mode} ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    logger.info("=" * 70)
    
    plan_file = os.path.join(PORTFOLIO_DIR, "trade_plan_v19.json")
    account_file = os.path.join(PORTFOLIO_DIR, "account_v19.json")
    
    state = PortfolioState(cash=INITIAL_CAPITAL, initial_capital=INITIAL_CAPITAL)
    if os.path.exists(account_file):
        try:
            state = PortfolioState.load(account_file)
        except Exception:
            pass
    
    price_data, code_dfs, latest_date = _load_prices()
    if latest_date is None:
        logger.error("价格数据加载失败")
        return None
    
    logger.info(f"📅 最新数据日期: {latest_date.date()}")
    logger.info(f"📊 股票数量: {len(code_dfs)}")
    
    factors = calc_team_coin_factors(code_dfs, latest_date)
    logger.info(f"📈 计算球队硬币因子: {len(factors)} 只")
    
    if not factors:
        logger.error("因子计算失败")
        return None
    
    scores = score_stocks_v19(factors, state.holdings)
    sorted_stocks = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_stocks = [c for c, s in sorted_stocks[:TOP_N]]
    
    logger.info(f"📊 选股结果: {len(top_stocks)} 只")
    for i, code in enumerate(top_stocks[:5]):
        s = scores[code]
        m5 = factors[code]['mom_5']
        logger.info(f"  {i+1}. {code} 评分={s:.3f} 5日动量={m5:.4f}")
    
    plan = {
        'mode': mode,
        'date': str(latest_date.date()),
        'strategy': 'v19_team_coin',
        'top_stocks': top_stocks,
    }
    
    import json
    with open(plan_file, 'w') as f:
        json.dump(plan, f, indent=2, default=str)
    
    if mode != "report_only":
        for code in list(state.holdings.keys()):
            if code in price_data.index:
                p = price_data[code]
                if pd.isna(p) or p <= 0:
                    continue
                h = state.holdings[code]
                pnl = (p - h['cost_price']) / h['cost_price']
                if pnl <= -STOP_LOSS:
                    state = sell(state, code, p, latest_date, reason='止损')
                elif pnl >= TAKE_PROFIT:
                    state = sell(state, code, p, latest_date, reason='止盈')
        
        to_sell = [c for c in list(state.holdings.keys()) if c not in top_stocks]
        for code in to_sell:
            if code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    state = sell(state, code, p, latest_date, reason='调仓卖出')
        
        current_pv = portfolio_value(state, latest_date, price_data) if state.holdings else INITIAL_CAPITAL
        for code in top_stocks:
            if code not in state.holdings and code in price_data.index:
                p = price_data[code]
                if not pd.isna(p) and p > 0:
                    target_value = min(current_pv / TOP_N * (1 - SLIPPAGE_RATE), current_pv * 0.10)
                    state = buy(state, code, p, latest_date, target_value=target_value)
        
        state.save(account_file)
    
    pv = portfolio_value(state, latest_date, price_data)
    total_value = pv + state.cash
    logger.info(f"📊 组合净值: ¥{total_value:,.0f}")
    
    return {'total_value': float(total_value)}


def _load_prices():
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
    return pd.Series(price_data), code_dfs, latest_date


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="v19 球队硬币因子策略")
    parser.add_argument('mode', choices=['intraday_signal', 'intraday_execute', 'day_end', 'report_only'],
                        default='day_end', nargs='?')
    args = parser.parse_args()
    run_sim_v19(args.mode)
