#!/usr/bin/env python3
"""
scripts/strategies/v46_industry_filter.py — v46 行业动量过滤策略
====================================================
基于 v39i（夏普1.199/回撤16.69%），在选股候选集确定后，
额外过滤：只保留行业动量 Top 10 行业的股票。

改进逻辑：
1. v39i 原有选股流程不变（mom_threshold + 风控过滤）
2. 新增：计算行业动量得分，过滤掉动量靠后行业的股票
3. 预期效果：减少弱势行业暴露，提升夏普

回测验证：WF 4 folds 对比 v39i。
"""
import pandas as pd
import numpy as np
from scripts.strategies.v39i_optimized import (
    select_stocks_v39i, DEFAULT_PARAMS as V39I_PARAMS, _get_mom_threshold
)

# v46 在 v39i 参数基础上新增行业过滤参数
DEFAULT_PARAMS = {
    **V39I_PARAMS,
    # ── 行业动量过滤参数 ──
    "INDUSTRY_FILTER": True,       # 是否启用行业过滤
    "INDUSTRY_TOP_N": 10,          # 保留动量前 N 个行业
    "INDUSTRY_MOM_WEIGHTS": (0.4, 0.3, 0.3),  # 5/21/60日权重
    # ── 连板因子参数 ──
    "STREAK_FACTOR": True,         # 是否启用连板因子
    "W_STREAK": 0.05,              # 连板因子权重
    # ── 业绩预告因子参数 ──
    "EARNINGS_FILTER": True,       # 是否启用业绩预告过滤
    "EARNINGS_WINDOW": 10,         # 负面预告规避天数
    "EARNINGS_POSITIVE_DAYS": 20,  # 正面预告信号窗口
    "W_EARNINGS": 0.03,            # 正面预告加分权重
}


def select_stocks_v46(factors, date, close_panel, volume_panel, amount_panel,
                      high_panel, low_panel, open_panel,
                      current_holdings=None, params=None, sold_recently=None,
                      industry_map=None):
    """
    v46 选股：v39i + 行业动量过滤
    
    参数:
        factors: 因子面板 dict (mom_5, pv_corr_10, ...)
        date: 当前日期
        close_panel, volume_panel, ...: 价格面板
        current_holdings: 当前持仓
        params: 策略参数
        sold_recently: 近期卖出（冷却期）
        industry_map: {code: industry_name} 股票→行业映射
    
    返回:
        list[(code, score)] — 按评分降序排列
    """
    from core.industry_momentum import compute_industry_momentum_rank
    
    p = {**DEFAULT_PARAMS, **(params or {})}
    
    # Step 1: 先走 v39i 原有流程，拿到候选集（含评分）
    # 但我们需要"先过滤再评分"，所以重写 v39i 逻辑并插入行业过滤
    
    if date not in factors['mom_5'].index:
        return []
    
    mom_threshold = _get_mom_threshold(factors, date, p)
    
    m5 = factors['mom_5'].loc[date].dropna()
    candidates = list(m5.index)
    candidates = [c for c in candidates if m5[c] > mom_threshold]
    
    # PV相关性过滤
    if date in factors['pv_corr_10'].index:
        pv10 = factors['pv_corr_10'].loc[date]
        candidates = [c for c in candidates if c in pv10.index and pv10[c] >= p["PV_CORR_10_MIN"]]
    
    # 退市风险过滤
    if date in factors['dr_threshold'].index and date in factors['delist_risk'].index:
        dr_t = factors['dr_threshold'].loc[date]
        candidates = [c for c in candidates
                      if c not in factors['delist_risk'].columns
                      or factors['delist_risk'].loc[date, c] <= dr_t]
    
    # 持仓/冷却期过滤
    if current_holdings:
        candidates = [c for c in candidates if c not in current_holdings]
    if sold_recently:
        candidates = [c for c in candidates if c not in sold_recently]
    
    if not candidates:
        return []

    # ★ v46 新增：业绩预告过滤（排除负面预告10天内的股票）
    if p.get("EARNINGS_FILTER", False):
        from core.earnings_preview import earnings_signal_mask
        mask_panel = close_panel if isinstance(close_panel, pd.DataFrame) else factors['mom_5']
        avoid_mask = earnings_signal_mask(mask_panel, lookback=p.get("EARNINGS_WINDOW", 10))
        candidates = [c for c in candidates if not avoid_mask.get(c, False)]

    if not candidates:
        return []

    # ★ v46 新增：行业动量过滤
    if p.get("INDUSTRY_FILTER", False) and industry_map:
        from core.industry_momentum import compute_industry_momentum_rank
        
        ind_mom = compute_industry_momentum_rank(
            (close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel),
            industry_map,
            date=date,
            top_n=p.get("INDUSTRY_TOP_N", 10),
        )
        
        candidates = [c for c in candidates if c in ind_mom.index and pd.notna(ind_mom[c])]
        
        if not candidates:
            return []
    
    # ★ v46 新增：连板因子加分
    streak_bonus = None
    if p.get("STREAK_FACTOR", False):
        from core.streak_factor import compute_streak_factor
        streak_bonus = compute_streak_factor(
            (close_panel, volume_panel, amount_panel, high_panel, low_panel, open_panel),
            date=date,
        )
    
    # Step 2: 对过滤后的候选集评分（与 v39i 相同）
    from scripts.strategies.v39c_pv_resonance import _score_column
    
    scores = pd.Series(0.0, index=candidates)
    
    for fname, wkey in [('mom_5', 'W_MOM'), ('pv_corr_20', 'W_PV_CORR'),
                         ('size_factor', 'W_SIZE'), ('illiq', 'W_ILLIQ')]:
        if p.get(wkey, 0) > 0:
            f_scores = _score_column(factors, date, fname)
            scores += f_scores.reindex(candidates).fillna(0) * p[wkey]
    
    if p.get("W_TURNOVER", 0) > 0:
        to_scores = _score_column(factors, date, 'turnover_avg', clip_min=0, clip_max=0.05)
        scores += to_scores.reindex(candidates).fillna(0) * p["W_TURNOVER"]
    if p.get("W_GAP", 0) > 0:
        gap_scores = _score_column(factors, date, 'gap_ratio', clip_min=0, clip_max=0.05)
        scores += gap_scores.reindex(candidates).fillna(0) * p["W_GAP"]
    if p.get("W_FUND_FLOW", 0) > 0:
        ff_scores = _score_column(factors, date, 'fund_flow', clip_min=0.5, clip_max=3.0)
        scores += ff_scores.reindex(candidates).fillna(0) * p["W_FUND_FLOW"]
    
    # ★ v46 连板因子加分
    if streak_bonus is not None and p.get("W_STREAK", 0) > 0:
        scores += streak_bonus.reindex(candidates).fillna(0) * p["W_STREAK"]
    
    # ★ v46 新增：业绩预告正面加分
    if p.get("EARNINGS_FILTER", False) and p.get("W_EARNINGS", 0) > 0:
        from core.earnings_preview import compute_earnings_signal
        sig_panel = close_panel if isinstance(close_panel, pd.DataFrame) else factors['mom_5']
        earnings_sig = compute_earnings_signal(sig_panel, current_date=date)
        positive_sig = earnings_sig[earnings_sig > 0]
        if len(positive_sig) > 0:
            scores += positive_sig.reindex(candidates).fillna(0) * p["W_EARNINGS"]
    
    # 排序返回
    result = list(zip(scores.index, scores.values))
    result.sort(key=lambda x: x[1], reverse=True)
    return result
