#!/usr/bin/env python3
"""
趋势择时策略调研
================
目标：判断大盘牛熊，熊市减仓/空仓，牛市正常持仓

趋势指标：
1. MA 均线系统（MA20/MA60/MA120/MA250）
2. MACD 趋势
3. 动量因子（大盘 N 日收益率）
4. 波动率趋势
5. 市场宽度（上涨家数占比）

择时规则：
- 强牛市：满仓（100% 仓位）
- 弱牛市/震荡：半仓（50% 仓位）
- 熊市：空仓或极低仓位（10%）

回测：在 v6b/v8 基础上叠加趋势择时
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("BACKTEST_DATA_DIR", "/root/data")
DAILY_DIR = os.path.join(DATA_DIR, "daily")

import numpy as np
import pandas as pd
import urllib.request, re

from core.factors import calc_factors_panel
from core.scoring import composite_score
from core.account import (PortfolioState, buy, sell, check_stop_loss,
                          check_take_profit, apply_holding_decay, portfolio_value)
from core.config import config as core_config, STRATEGY_PROFILES

INITIAL_CAPITAL = core_config.costs.initial_capital

# ── 加载数据 ──────────────────────────────────────────────────────
print("加载数据...")
codes = sorted([f.replace(".csv","") for f in os.listdir(DAILY_DIR) if f.endswith(".csv")])
close_p, vol_p = {}, {}
for c in codes:
    f = os.path.join(DAILY_DIR, f"{c}.csv")
    if os.path.exists(f):
        df = pd.read_csv(f, index_col='date', parse_dates=True)['2021-01-01':]
        if len(df)>100: close_p[c]=df['close']; vol_p[c]=df['volume']
close = pd.DataFrame(close_p); vol = pd.DataFrame(vol_p); amt = close*vol
dates = close.dropna(how='all').index.sort_values()
print(f"  {close.shape}")

# ── 构造大盘指数（等权）──────────────────────────────────────────
print("构造大盘指数...")
# 用所有股票等权平均作为大盘
valid_cols = [c for c in close.columns if close[c].notna().sum() > 200]
close_valid = close[valid_cols]
# 等权日收益 → 累计指数
daily_ret = close_valid.pct_change().mean(axis=1)
market_index = (1 + daily_ret).cumprod() * 1000  # 基准 1000 点
print(f"  大盘指数: {market_index.iloc[0]:.1f} → {market_index.iloc[-1]:.1f}")

# ── 趋势指标计算 ─────────────────────────────────────────────────
print("计算趋势指标...")

# 1. 均线
ma20 = market_index.rolling(20).mean()
ma60 = market_index.rolling(60).mean()
ma120 = market_index.rolling(120).mean()
ma250 = market_index.rolling(250).mean()

# 2. 动量
mom_10 = market_index.pct_change(10)
mom_20 = market_index.pct_change(20)
mom_60 = market_index.pct_change(60)

# 3. 波动率
vol_20 = daily_ret.rolling(20).std() * np.sqrt(252)
vol_60 = daily_ret.rolling(60).std() * np.sqrt(252)

# 4. 市场宽度（上涨家数占比）
advancing = (close_valid.pct_change() > 0).sum(axis=1)
total = close_valid.notna().sum(axis=1)
market_breadth = advancing / total  # 0~1

# 5. MACD
ema12 = market_index.ewm(span=12).mean()
ema26 = market_index.ewm(span=26).mean()
macd_line = ema12 - ema26
signal_line = macd_line.ewm(span=9).mean()
macd_hist = macd_line - signal_line

# ── 择时信号 ─────────────────────────────────────────────────────
print("生成择时信号...")

# 综合评分（多指标投票）
trend_score = pd.Series(0.0, index=dates)

# 均线信号：价格在 MA 上方 +1，下方 -1
trend_score += (market_index > ma20).astype(int) * 1
trend_score += (market_index > ma60).astype(int) * 1
trend_score += (market_index > ma120).astype(int) * 1
trend_score += (market_index > ma250).astype(int) * 1
# 归一化到 [-1, 1]
trend_score = trend_score / 4 * 2 - 1  # 0~4 → -1~1

# 动量信号
trend_score += mom_20.apply(lambda x: 1 if x > 0.05 else (-1 if x < -0.05 else 0)) * 0.5
trend_score += mom_60.apply(lambda x: 1 if x > 0.10 else (-1 if x < -0.10 else 0)) * 0.5

# 市场宽度信号
trend_score += market_breadth.apply(lambda x: 1 if x > 0.6 else (-1 if x < 0.4 else 0)) * 0.5

# MACD 信号
trend_score += (macd_hist > 0).astype(int) * 0.5

# 最终仓位比例
# trend_score 范围约 -3 ~ +3
# 映射到仓位：强牛市=100%，震荡=50%，熊市=10%
def score_to_position(score):
    if score > 1.5:
        return 1.0   # 满仓
    elif score > 0.5:
        return 0.7   # 7成
    elif score > -0.5:
        return 0.4   # 4成（震荡）
    elif score > -1.5:
        return 0.2   # 2成（弱熊）
    else:
        return 0.05  # 5%（强熊，几乎空仓）

position_ratio = trend_score.apply(score_to_position)

# 打印择时统计
print(f"\n仓位分布:")
print(f"  满仓(>0.8): {(position_ratio > 0.8).sum()} 天 ({(position_ratio > 0.8).mean()*100:.1f}%)")
print(f"  高仓(0.6-0.8): {((position_ratio > 0.5) & (position_ratio <= 0.8)).sum()} 天")
print(f"  中仓(0.3-0.5): {((position_ratio > 0.2) & (position_ratio <= 0.5)).sum()} 天")
print(f"  低仓(<0.2): {(position_ratio <= 0.2).sum()} 天 ({(position_ratio <= 0.2).mean()*100:.1f}%)")

# 各期仓位
periods = [
    ('2023-01-01','2023-06-30','2023H1'),
    ('2023-07-01','2023-12-31','2023H2'),
    ('2024-01-01','2024-06-30','2024H1'),
    ('2024-07-01','2024-12-31','2024H2'),
    ('2025-01-01','2025-06-30','2025H1'),
    ('2025-07-01','2025-12-31','2025H2'),
]
print(f"\n各期平均仓位:")
for s,e,label in periods:
    pr = position_ratio[(position_ratio.index >= s) & (position_ratio.index <= e)]
    mi = market_index[(market_index.index >= s) & (market_index.index <= e)]
    if len(pr) > 0:
        print(f"  {label}: 仓位={pr.mean()*100:.0f}% | 大盘收益={(mi.iloc[-1]/mi.iloc[0]-1)*100:+.1f}%")

# ── 回测引擎（带趋势择时）────────────────────────────────────────
print("\n回测...")

def run_bt_with_timing(score, position_ratio, label):
    """带趋势择时的回测"""
    state = PortfolioState(cash=INITIAL_CAPITAL, initial_capital=INITIAL_CAPITAL)
    nav_list = []
    TOP_N=12; REBAL=20; MAX_IND=0.25; MAX_POS=0.10
    
    for i, date in enumerate(dates):
        if i < 120:
            nav_list.append(INITIAL_CAPITAL); continue
        if date not in close.index:
            nav_list.append(nav_list[-1] if nav_list else INITIAL_CAPITAL); continue
        
        pd_ = close.loc[date]
        state = check_stop_loss(state, date, pd_)
        state = check_take_profit(state, date, pd_, [(0.10,0.30),(0.20,0.30),(0.30,1.00)])
        state = apply_holding_decay(state, date, pd_, REBAL)
        
        # 获取当前仓位比例
        pr = position_ratio.get(date, 0.5)  # 默认 50%
        
        if (i-120) % REBAL == 0 and date in score.index:
            ds = score.loc[date].dropna()
            ds = ds[ds.index.isin(pd_.dropna().index)]
            if len(ds) > 0:
                top=[]; ic={}; mpi=max(1,int(MAX_IND*TOP_N))
                for c in ds.sort_values(ascending=False).index:
                    ind=c[:2]
                    if ic.get(ind,0)<mpi: top.append(c); ic[ind]=ic.get(ind,0)+1
                    if len(top)>=TOP_N: break
                if top:
                    cpv = portfolio_value(state, date, pd_)
                    
                    # 根据仓位比例调整目标市值
                    target_total = cpv * pr  # 目标持仓市值
                    current_holdings_value = sum(
                        state.holdings[c]['shares'] * pd_.get(c, 0)
                        for c in state.holdings if c in pd_.index and not pd.isna(pd_[c])
                    )
                    
                    # 卖出超出仓位的持仓
                    if current_holdings_value > target_total * 1.1:
                        # 按比例卖出
                        sell_ratio = 1 - target_total / current_holdings_value
                        for c in list(state.holdings.keys()):
                            if c in pd_.index and not pd.isna(pd_[c]) and pd_[c] > 0:
                                shares_to_sell = int(state.holdings[c]['shares'] * sell_ratio / 100) * 100
                                if shares_to_sell > 0:
                                    state = sell(state, c, pd_[c], date, shares_to_sell)
                    
                    # 买入（不超过仓位上限）
                    available_cash = state.cash
                    max_invest = target_total - current_holdings_value
                    
                    if max_invest > 1000:  # 至少 1000 元才买
                        for c in top:
                            if c not in state.holdings and c in pd_.index:
                                p = pd_[c]
                                if pd.isna(p) or p <= 0: continue
                                tv = min(max_invest / len(top), MAX_POS * cpv)
                                ap = p * (1 + core_config.costs.slippage_rate)
                                sh = int(tv / ap / 100) * 100
                                if sh > 0 and state.cash >= sh * ap:
                                    state = buy(state, c, p, date, shares=sh)
        
        nav_list.append(portfolio_value(state, date, pd_))
    
    nav = pd.Series(nav_list, index=dates)
    rets = nav.pct_change().dropna()
    tr = nav.iloc[-1]/nav.iloc[0]-1
    y = max(len(nav)/252, 0.01)
    ar = (1+tr)**(1/y)-1
    av = rets.std()*np.sqrt(252)
    sp = ar/av if av > 0 else 0
    peak = nav.cummax()
    md = ((nav-peak)/peak).min()
    cm = ar/abs(md) if md != 0 else 0
    
    print(f"  {label}:")
    print(f"    总收益={tr*100:.2f}% | 年化={ar*100:.2f}% | Sharpe={sp:.3f} | 回撤={md*100:.2f}% | Calmar={cm:.3f}")
    
    return nav, {'annual':round(ar*100,2),'sharpe':round(sp,3),'dd':round(md*100,2),'calmar':round(cm,3)}

# 计算因子
print("计算个股因子...")
factors = calc_factors_panel(close, vol, amt)

# v6b 权重
W_V6B = STRATEGY_PROFILES['v6b_8f_pos_ic'].factor_weights
avail = {k:v for k,v in W_V6B.items() if k in factors}
score_v6b = composite_score(factors, avail)

print("\n" + "="*60)
print("策略对比（全集 2021~2026-05）")
print("="*60)

# 1. v6b 无择时
print("\n[1] v6b 无择时...")
nav_base, res_base = run_bt_with_timing(
    score_v6b, 
    pd.Series(1.0, index=dates),  # 始终满仓
    "v6b_无择时(始终满仓)"
)

# 2. v6b + 趋势择时
print("\n[2] v6b + 趋势择时...")
nav_timing, res_timing = run_bt_with_timing(score_v6b, position_ratio, "v6b_趋势择时")

# 3. 纯择时（不选股，只按仓位持有现金/等权指数）
print("\n[3] 纯择时（大盘指数 × 仓位）...")
timing_only = market_index * position_ratio + 1000 * (1 - position_ratio)
timing_rets = timing_only.pct_change().dropna()
tr_t = timing_only.iloc[-1]/timing_only.iloc[0]-1
y_t = max(len(timing_only)/252, 0.01)
ar_t = (1+tr_t)**(1/y_t)-1
av_t = timing_rets.std()*np.sqrt(252)
sp_t = ar_t/av_t if av_t > 0 else 0
peak_t = timing_only.cummax()
md_t = ((timing_only-peak_t)/peak_t).min()
cm_t = ar_t/abs(md_t) if md_t != 0 else 0
print(f"  纯择时: 总收益={tr_t*100:.2f}% | 年化={ar_t*100:.2f}% | Sharpe={sp_t:.3f} | 回撤={md_t*100:.2f}% | Calmar={cm_t:.3f}")
res_timing_only = {'annual':round(ar_t*100,2),'sharpe':round(sp_t,3),'dd':round(md_t*100,2),'calmar':round(cm_t,3)}

# 汇总
print(f"\n{'='*60}")
print(f"{'策略':>20} | {'年化%':>8} | {'Sharpe':>8} | {'回撤%':>8} | {'Calmar':>8}")
print("-" * 60)
for lbl, rv in [("v6b_无择时",res_base),("v6b_趋势择时",res_timing),("纯择时(指数)",res_timing_only)]:
    print(f"  {lbl:>18} | {rv['annual']:>8.2f} | {rv['sharpe']:>8.3f} | {rv['dd']:>8.2f} | {rv['calmar']:>8.3f}")

# 分时段对比
print(f"\n\n分时段对比:")
print(f"{'时段':>8} | {'大盘收益':>10} | {'v6b无择时':>10} | {'v6b择时':>10} | {'择时仓位':>8}")
print("-" * 60)
for s,e,label in periods:
    # 大盘
    mi = market_index[(market_index.index>=s)&(market_index.index<=e)]
    mkt_ret = (mi.iloc[-1]/mi.iloc[0]-1)*100 if len(mi)>0 else 0
    
    # v6b 无择时
    nb = nav_base[(nav_base.index>=s)&(nav_base.index<=e)]
    base_ret = (nb.iloc[-1]/nb.iloc[0]-1)*100 if len(nb)>0 else 0
    
    # v6b 择时
    nt = nav_timing[(nav_timing.index>=s)&(nav_timing.index<=e)]
    timing_ret = (nt.iloc[-1]/nt.iloc[0]-1)*100 if len(nt)>0 else 0
    
    # 平均仓位
    pr = position_ratio[(position_ratio.index>=s)&(position_ratio.index<=e)]
    avg_pos = pr.mean()*100 if len(pr)>0 else 0
    
    print(f"  {label:>6} | {mkt_ret:>+10.1f}% | {base_ret:>+10.1f}% | {timing_ret:>+10.1f}% | {avg_pos:>7.0f}%")

print("\n完成")
