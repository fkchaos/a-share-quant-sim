"""
v18 波动率的波动率因子策略
============================

基于方正金工《波动率的波动率与投资者模糊性厌恶》(2022.08)

核心思想：
  波动率本身也存在明显波动，用"波动率的波动率"刻画市场模糊性。
  投资者普遍厌恶波动率模糊性，当模糊性大时急于卖出股票 → 反转信号。

因子构建：
  1. 日波动率 = |HIGH - LOW| / CLOSE（日内振幅）
  2. 波动率的波动率 = STD(日波动率, 20)（20日波动率标准差）
  3. 截面排名 = RANK(波动率的波动率)

交易逻辑：
  - 周频调仓（每5个交易日）
  - 买入波动率波动率最低的股票（模糊性小 → 确定性高）
  - 与 v13 反转因子互补：恐慌时（波动率波动率大）增强反转权重

与 v13 的区别：
  - v13: 纯反转因子（跌幅基础分 + 量价加减分）
  - v18: 波动率波动率因子，用于调节反转权重（模糊性大时增强反转）
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

PROFILE = "v18_vol_of_vol"
_strategy_profile = STRATEGY_PROFILES.get(PROFILE)
if _strategy_profile is None:
    _strategy_profile = STRATEGY_PROFILES["v13_small_mid_short"]

REBAL_FREQ = 5
STOP_LOSS = 0.05
TAKE_PROFIT = 0.10
TOP_N = 12

_costs = TradingCosts()
SLIPPAGE_RATE = _costs.slippage_rate
COMMISSION_RATE = _costs.commission_rate
INITIAL_CAPITAL = _costs.initial_capital

logger = get_logger("sim_v18")


def calc_vol_of_vol_factors(code_dfs, date):
    """
    计算波动率的波动率因子
    
    核心逻辑:
        日波动率 = |HIGH - LOW| / CLOSE
        波动率的波动率 = STD(日波动率, 20)
    """
    factors = {}
    
    for code, df in code_dfs.items():
        if len(df) < 25:
            continue
        
        try:
            # 日波动率（振幅）
            daily_vol = (df['high'] - df['low']) / (df['close'] + 1e-10)
            
            # 波动率的波动率（20日标准差）
            vol_of_vol = daily_vol.rolling(20).std()
            
            # 取当日值
            if date in df.index:
                idx = df.index.get_loc(date)
                factors[code] = {
                    'daily_vol': float(daily_vol.iloc[idx]),
                    'vol_of_vol': float(vol_of_vol.iloc[idx]),
                }
        except Exception:
            continue
    
    return factors


def score_stocks_v18(factors, current_holdings=None):
    """
    v18 评分函数
    
    评分逻辑:
        波动率低 → 高分为看多（确定性高）
        波动率高 → 低分为看空（模糊性大）
    """
    if not factors:
        return {}
    
    codes = list(factors.keys())
    vol_of_vol_values = np.array([factors[c]['vol_of_vol'] for c in codes])
    
    from scipy.stats import rankdata
    
    # 波动率波动率排名：低波动 → 高分为看多
    vol_of_vol_rank = rankdata(vol_of_vol_values) / len(codes)
    
    scores = {}
    for i, code in enumerate(codes):
        scores[code] = vol_of_vol_rank[i]
    
    return scores


def run_sim_v18(mode="day_end"):
    """v18 波动率的波动率因子策略"""
    logger.info("=" * 70)
    logger.info(f"v18 波动率的波动率因子策略 — {mode} ({datetime.now().strftime('%Y-%m-%d %H:%M')})")
    logger.info("=" * 70)
    
    plan_file = os.path.join(PORTFOLIO_DIR, "trade_plan_v18.json")
    account_file = os.path.join(PORTFOLIO_DIR, "account_v18.json")
    
    # 加载账户
    state = PortfolioState(cash=INITIAL_CAPITAL, initial_capital=INITIAL_CAPITAL)
    if os.path.exists(account_file):
        try:
            state = PortfolioState.load(account_file)
        except Exception:
            pass
    
    # 加载价格数据
    price_data, code_dfs, latest_date = _load_prices()
    if latest_date is None:
        logger.error("价格数据加载失败")
        return None
    
    logger.info(f"📅 最新数据日期: {latest_date.date()}")
    logger.info(f"📊 股票数量: {len(code_dfs)}")
    
    # 计算因子
    factors = calc_vol_of_vol_factors(code_dfs, latest_date)
    logger.info(f"📈 计算波动率的波动率因子: {len(factors)} 只")
    
    if not factors:
        logger.error("因子计算失败")
        return None
    
    # 评分选股
    scores = score_stocks_v18(factors, state.holdings)
    sorted_stocks = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_stocks = [c for c, s in sorted_stocks[:TOP_N]]
    
    logger.info(f"📊 选股结果: {len(top_stocks)} 只")
    for i, code in enumerate(top_stocks[:5]):
        s = scores[code]
        v = factors[code]['vol_of_vol']
        logger.info(f"  {i+1}. {code} 评分={s:.3f} 波动率的波动率={v:.4f}")
    
    # 生成计划
    plan = {
        'mode': mode,
        'date': str(latest_date.date()),
        'strategy': 'v18_vol_of_vol',
        'top_stocks': top_stocks,
    }
    
    import json
    with open(plan_file, 'w') as f:
        json.dump(plan, f, indent=2, default=str)
    
    # 执行
    if mode != "report_only":
        # 风控
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
        
        # 调仓
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
    return pd.Series(price_data), code_dfs, latest_date


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="v18 波动率的波动率因子策略")
    parser.add_argument('mode', choices=['intraday_signal', 'intraday_execute', 'day_end', 'report_only'],
                        default='day_end', nargs='?')
    args = parser.parse_args()
    run_sim_v18(args.mode)
